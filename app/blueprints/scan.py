"""QR codes, étiquettes, page de scan et fichiers PWA."""
from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, TextAreaField
from wtforms.validators import AnyOf, DataRequired, Length, Optional

from ..extensions import db
from ..models import Item, Kit, Loan, LoanLine, LoanStatus, PublicReport, get_live
from ..permissions import can, require
from ..services import branding, public_reports, throttle
from ..services.items import search_items
from ..services.mailer import deliver_quietly
from ..services.qr import find_by_token, qr_svg, qr_url, resolve_scan

bp = Blueprint("scan", __name__)

# Signalements depuis un QR : par heure, 5 par adresse IP et 10 par objet.
_reports_by_ip = throttle.Limiter(5, 3600)
_reports_by_target = throttle.Limiter(10, 3600)


class ReportForm(FlaskForm):
    kind = HiddenField(validators=[AnyOf(list(PublicReport.KINDS))])
    message = TextAreaField("Message", validators=[DataRequired("Décrivez le problème ou l'endroit où vous l'avez "
                                                                "trouvé."), Length(max=2000)])
    name = StringField("Votre nom (facultatif)", validators=[Optional(), Length(max=120)])
    contact = StringField("Téléphone ou email pour vous recontacter (facultatif)",
                          validators=[Optional(), Length(max=160)])
    website = StringField("Ne pas remplir")  # piège à robots, masqué à l'écran

MAX_LABELS = 500
# Formats d'étiquettes A4 courants (planches adhésives type Avery).
LABEL_FORMATS = {
    "petit": {"label": "Petit — 38 × 21 mm (65 par page)", "cols": 5, "w": 38.1, "h": 21.2},
    "moyen": {"label": "Moyen — 63,5 × 38 mm (21 par page)", "cols": 3, "w": 63.5, "h": 38.1},
    "grand": {"label": "Grand — 99 × 68 mm (8 par page)", "cols": 2, "w": 99.1, "h": 67.7},
}


def target_url(obj):
    if isinstance(obj, Kit):
        return url_for("kits.detail", kit_id=obj.id)
    return url_for("items.detail", item_id=obj.id)


@bp.route("/q/<token>")
def resolve(token):
    """Destination des QR codes collés sur le matériel, accessible sans compte."""
    obj = find_by_token(token) or abort(404)
    if can(current_user, "inventory.view"):
        return redirect(target_url(obj))
    return _public_page(obj, ReportForm())


def _public_page(obj, form, status=200):
    """Fiche réduite : nom et code, de quoi signaler un problème ou un objet trouvé ; rien d'autre n'est dévoilé.
    Emprunteur connecté : on y ajoute ses prêts en cours sur cet objet."""
    loans = []
    if current_user.is_authenticated and isinstance(obj, Item):
        loans = db.session.scalars(
            db.select(Loan).join(LoanLine).where(
                LoanLine.item_id == obj.id, Loan.borrower_id == current_user.id,
                Loan.status.in_((LoanStatus.VALIDE, LoanStatus.EN_COURS)))
        ).all()
    template = "scan/limited.html" if current_user.is_authenticated else "scan/public.html"
    return render_template(template, obj=obj, loans=loans, form=form, token=obj.qr_token), status


@bp.route("/q/<token>/signaler", methods=["POST"])
def report(token):
    obj = find_by_token(token) or abort(404)
    form = ReportForm()
    if not form.validate():
        return _public_page(obj, form, 400)
    if form.website.data:  # robot : on fait comme si de rien n'était
        flash("Merci, votre message a bien été transmis.", "success")
        return redirect(url_for(".resolve", token=token))
    by_ip, by_target = [request.remote_addr or "?"], [token]
    if _reports_by_ip.blocked(by_ip) or _reports_by_target.blocked(by_target):
        flash("Trop de messages envoyés : réessayez un peu plus tard.", "error")
        return _public_page(obj, form, 429)
    _reports_by_ip.hit(by_ip)
    _reports_by_target.hit(by_target)
    user = current_user if current_user.is_authenticated else None
    public_reports.create(obj, form.kind.data, form.message.data, name=form.name.data or (user and user.full_name),
                          contact=form.contact.data, user=user)
    db.session.commit()
    deliver_quietly()
    flash("Merci, votre message a bien été transmis aux responsables du matériel.", "success")
    return redirect(url_for(".resolve", token=token))


@bp.route("/scan")
@require("scan")
def index():
    return render_template("scan/index.html")


@bp.route("/scan/resoudre")
@require("scan")
def lookup():
    """Résout une saisie manuelle ou un QR lu par la caméra."""
    text = request.args.get("code", "")
    obj = resolve_scan(text)
    if obj is None:
        flash(f"Aucun objet ne correspond à « {text.strip()[:60]} ».", "warning")
        return redirect(url_for(".index"))
    return redirect(target_url(obj))


@bp.route("/objets/<int:item_id>/qr.svg")
@require("inventory.view")
def item_qr(item_id):
    item = get_live(Item, item_id) or abort(404)
    svg = qr_svg(qr_url(item.qr_token))
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Content-Disposition": f'inline; filename="{item.code}.svg"'})


@bp.route("/kits/<int:kit_id>/qr.svg")
@require("inventory.view")
def kit_qr(kit_id):
    kit = get_live(Kit, kit_id) or abort(404)
    return Response(qr_svg(qr_url(kit.qr_token)), mimetype="image/svg+xml",
                    headers={"Content-Disposition": f'inline; filename="kit-{kit.id}.svg"'})


@bp.route("/etiquettes")
@require("label.print")
def labels():
    ids = request.args.getlist("ids", type=int)
    kit_ids = request.args.getlist("kit_ids", type=int)
    if request.args.get("kits") == "tous":
        kit_ids = db.session.scalars(db.select(Kit.id)).all()
    if kit_ids and request.args.get("avec_objets"):  # kits et les objets qu'ils contiennent
        ids += db.session.scalars(db.select(Item.id).where(Item.kit_id.in_(kit_ids))).all()
    if ids or kit_ids or request.args.get("selection") or request.args.get("kits"):
        query = db.select(Item).where(Item.id.in_(ids)).order_by(Item.code)
    else:
        query = search_items(request.args)
    kits = db.session.scalars(db.select(Kit).where(Kit.id.in_(kit_ids)).order_by(Kit.name)).all() if kit_ids else []
    entries = [{"code": f"KIT-{k.id:03d}", "name": k.name, "token": k.qr_token} for k in kits]
    entries += [{"code": i.code, "name": i.name, "token": i.qr_token}
                for i in db.session.scalars(query.limit(MAX_LABELS + 1))]
    truncated = len(entries) > MAX_LABELS
    entries = entries[:MAX_LABELS]

    size = request.args.get("format", "moyen")
    skip = max(0, min(request.args.get("decalage", 0, type=int), 200))
    labels = [dict(e, svg=qr_svg(qr_url(e["token"]))) for e in entries]
    base_url = current_app.config["BASE_URL"]
    return render_template(
        "scan/labels.html", labels=labels, size=size if size in LABEL_FORMATS else "moyen",
        formats=LABEL_FORMATS, skip=skip, truncated=truncated, max_labels=MAX_LABELS,
        local_url=any(h in base_url for h in ("localhost", "127.0.0.1")), base_url=base_url,
        query_args=[(k, v) for k, v in request.args.items(multi=True) if k not in ("format", "decalage", "csrf_token")],
    )


@bp.route("/manifest.webmanifest")
def manifest():
    brand = branding.current()
    data = {
        "name": f"Inventaire — {brand.name}",
        "short_name": "Inventaire",
        "lang": "fr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": brand.theme_color,
        "icons": [
            {"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
        ],
        "shortcuts": [{"name": "Scanner", "url": "/scan"}],
    }
    return current_app.response_class(current_app.json.dumps(data), mimetype="application/manifest+json")


@bp.route("/sw.js")
def service_worker():
    # Servi à la racine pour que sa portée couvre toute l'appli.
    response = send_from_directory(current_app.static_folder, "js/sw.js", mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response
