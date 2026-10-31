"""QR codes, étiquettes, page de scan et fichiers PWA."""
from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Item, Kit, Loan, LoanLine, LoanStatus, get_live
from ..permissions import can, require
from ..services.items import search_items
from ..services.qr import find_by_token, qr_svg, qr_url, resolve_scan

bp = Blueprint("scan", __name__)

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
@login_required
def resolve(token):
    """Destination des QR codes collés sur le matériel."""
    obj = find_by_token(token) or abort(404)
    if can(current_user, "inventory.view"):
        return redirect(target_url(obj))
    # Emprunteur : fiche réduite, avec ses éventuels prêts en cours sur cet objet.
    loans = []
    if isinstance(obj, Item):
        loans = db.session.scalars(
            db.select(Loan).join(LoanLine).where(
                LoanLine.item_id == obj.id, Loan.borrower_id == current_user.id,
                Loan.status.in_((LoanStatus.VALIDE, LoanStatus.EN_COURS)))
        ).all()
    return render_template("scan/limited.html", obj=obj, loans=loans)


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
    fmt = LABEL_FORMATS.get(size) or LABEL_FORMATS["moyen"]
    skip = max(0, min(request.args.get("decalage", 0, type=int), 200))
    labels = [dict(e, svg=qr_svg(qr_url(e["token"]))) for e in entries]
    base_url = current_app.config["BASE_URL"]
    return render_template(
        "scan/labels.html", labels=labels, fmt=fmt, size=size if size in LABEL_FORMATS else "moyen",
        formats=LABEL_FORMATS, skip=skip, truncated=truncated, max_labels=MAX_LABELS,
        local_url=any(h in base_url for h in ("localhost", "127.0.0.1")), base_url=base_url,
        query_args=[(k, v) for k, v in request.args.items(multi=True) if k not in ("format", "decalage", "csrf_token")],
    )


@bp.route("/manifest.webmanifest")
def manifest():
    name = current_app.config["ASSO_NAME"]
    data = {
        "name": f"Inventaire — {name}",
        "short_name": "Inventaire",
        "lang": "fr",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#8b1e3f",
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
