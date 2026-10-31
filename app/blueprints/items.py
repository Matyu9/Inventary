from urllib.parse import urlsplit

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import DateField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from ..extensions import db
from ..forms import FrDecimalField, amount_range
from ..models import (Activity, Category, CategoryType, ConsumptionMode, Condition, Item, ItemStatus,
                      LoanLine, Location, Note, Photo, Tag, Tracking, get_live)
from ..permissions import can, ensure, require
from ..services import items as svc
from ..services import trash
from ..services.activity import diff, log
from ..services.photos import InvalidImage, delete_photo_files, save_photo
from .auth import safe_next

bp = Blueprint("items", __name__, url_prefix="/objets")

PER_PAGE = 50
EDITABLE_FIELDS = ("name", "category_id", "brand", "model", "serial_number", "description", "condition",
                   "location_id", "purchase_date", "purchase_price", "estimated_value", "tracking",
                   "unit", "low_stock_threshold", "consumption_mode")
# Statuts modifiables à la main ; les autres sont pilotés par les prêts ou la réforme.
MANUAL_STATUSES = (ItemStatus.DISPONIBLE, ItemStatus.EN_ENTRETIEN)


def _referrer_path():
    """Chemin local de la page d'origine (le Referer complet n'est jamais réutilisé tel quel)."""
    ref = urlsplit(request.referrer or "")
    return ref.path + ("?" + ref.query if ref.query else "") if ref.netloc == request.host else None


def none_if_zero(value):
    value = int(value or 0)
    return value or None


class ItemForm(FlaskForm):
    name = StringField("Désignation", validators=[DataRequired(), Length(max=160)])
    category_id = SelectField("Catégorie", coerce=int, validators=[DataRequired()])
    brand = StringField("Marque", validators=[Optional(), Length(max=80)])
    model = StringField("Modèle", validators=[Optional(), Length(max=80)])
    serial_number = StringField("N° de série", validators=[Optional(), Length(max=80)])
    description = TextAreaField("Description", validators=[Optional()])
    condition = SelectField("État", choices=[(c, c.capitalize()) for c in Condition.ALL])
    status = SelectField("Statut", choices=[(s, s.capitalize()) for s in MANUAL_STATUSES])
    location_id = SelectField("Lieu", coerce=none_if_zero)
    tags = StringField("Tags", description="Séparés par des virgules", validators=[Optional()])
    purchase_date = DateField("Date d'achat", validators=[Optional()])
    purchase_price = FrDecimalField("Prix d'achat (€)", places=2, validators=[Optional(), amount_range()])
    estimated_value = FrDecimalField("Valeur estimée (€)", places=2, validators=[Optional(), amount_range()])
    tracking = SelectField("Suivi", choices=[(Tracking.INDIVIDUEL, "Objet individuel"),
                                             (Tracking.QUANTITE, "En quantité (consommable)")])
    quantity = IntegerField("Quantité initiale", default=1, validators=[Optional(), NumberRange(min=0, max=1_000_000)])
    unit = StringField("Unité", validators=[Optional(), Length(max=30)])
    low_stock_threshold = IntegerField("Seuil d'alerte", validators=[Optional(), NumberRange(min=0, max=1_000_000)])
    consumption_mode = SelectField("Mode", choices=[("", "—"), (ConsumptionMode.CONSOMME, "Consommé (distribué)"),
                                                    (ConsumptionMode.RETOURNABLE, "Retournable (prêté)")])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.category_id.choices = [(c.id, c.name) for c in
                                    db.session.scalars(db.select(Category).order_by(Category.name))]
        self.location_id.choices = [(0, "—")] + location_choices()


def location_choices():
    locations = db.session.scalars(db.select(Location)).all()
    return sorted(((loc.id, loc.path) for loc in locations), key=lambda c: c[1].lower())


def filter_options():
    return {
        "categories": db.session.scalars(db.select(Category).order_by(Category.name)).all(),
        "tags": db.session.scalars(db.select(Tag).order_by(Tag.name)).all(),
        "locations": location_choices(),
        "statuses": ItemStatus.ALL,
        "conditions": Condition.ALL,
    }


@bp.app_template_global()
def tag_names():
    return db.session.scalars(db.select(Tag.name).order_by(Tag.name)).all()


def get_item(item_id):
    return get_live(Item, item_id) or abort(404)


# ---------------------------------------------------------------------------
# Liste
# ---------------------------------------------------------------------------

@bp.route("")
@require("inventory.view")
def index():
    page = max(request.args.get("page", 1, type=int), 1)
    query = svc.search_items(request.args)
    total = db.session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    items = db.session.scalars(query.limit(PER_PAGE).offset((page - 1) * PER_PAGE)).all()
    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    args = {k: v for k, v in request.args.items() if k in svc.FILTERS and v}
    ctx = dict(items=items, total=total, page=page, pages=pages, args=args)
    if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "item-results":
        return render_template("items/_results.html", **ctx)
    return render_template("items/index.html", **ctx, **filter_options())


@bp.route("/actions-groupees", methods=["POST"])
@require("item.edit")
def bulk():
    ids = request.form.getlist("ids", type=int)
    items = db.session.scalars(db.select(Item).where(Item.id.in_(ids))).all() if ids else []
    action = request.form.get("action")
    if not items:
        flash("Aucun objet sélectionné.", "warning")
    elif action == "move":
        location = db.session.get(Location, request.form.get("location_id", type=int) or 0)
        for item in items:
            if item.location_id != (location.id if location else None):
                log("item.move", f"Déplacé vers {location.path if location else 'aucun lieu'}", item=item,
                    changes={"location": [item.location.path if item.location else None,
                                          location.path if location else None]})
                item.location = location
        db.session.commit()
        flash(f"{len(items)} objet(s) déplacé(s).", "success")
    elif action == "tag":
        tags = svc.parse_tags(request.form.get("tag"))
        for item in items:
            for tag in tags:
                if tag not in item.tags:
                    item.tags.append(tag)
        db.session.commit()
        flash(f"Tags ajoutés à {len(items)} objet(s).", "success")
    else:
        flash("Action inconnue.", "error")
    return redirect(safe_next(_referrer_path(), url_for(".index")))


# ---------------------------------------------------------------------------
# Création / modification
# ---------------------------------------------------------------------------

@bp.route("/champs")
@require("item.edit")
def custom_fields_partial():
    """Fragment HTMX : champs perso de la catégorie choisie."""
    category_id = request.args.get("category_id", type=int)
    item = get_live(Item, request.args.get("item_id", type=int) or 0)
    values = {v.field_id: v.value for v in item.custom_values} if item else {}
    return render_template("items/_custom_fields.html", fields=svc.fields_for_category(category_id),
                           values=values, errors={})


def _save_item(form, item=None):
    """Valide formulaire + champs perso ; renvoie l'objet enregistré ou None si erreurs."""
    fields = svc.fields_for_category(form.category_id.data)
    custom_values, custom_errors = svc.validate_custom_values(fields, request.form)
    if not form.validate() or custom_errors:
        return None, custom_errors

    data = {f: getattr(form, f).data for f in EDITABLE_FIELDS}
    data["consumption_mode"] = data["consumption_mode"] or None
    category = db.session.get(Category, data["category_id"])
    if category.type == CategoryType.CONSOMMABLE:
        data["tracking"] = Tracking.QUANTITE
    if item is not None and data["tracking"] != item.tracking:
        if item.kit_id:
            form.tracking.errors.append("Retirez d'abord l'objet de son kit.")
            return None, custom_errors
        if db.session.scalar(db.select(LoanLine.id).where(LoanLine.item_id == item.id).limit(1)):
            form.tracking.errors.append("Impossible : cet objet a déjà été prêté avec l'autre mode de suivi.")
            return None, custom_errors

    creating = item is None
    if creating:
        item = Item(code=Item.next_code(current_app.config["ITEM_CODE_PREFIX"]))
        for field, value in data.items():
            setattr(item, field, value)
        item.status = form.status.data
        item.quantity = form.quantity.data if item.is_quantity and form.quantity.data is not None else 1
        db.session.add(item)
        changes = {}
    else:
        changes = diff(item, EDITABLE_FIELDS, data)
        if item.status in MANUAL_STATUSES and form.status.data != item.status:
            changes["status"] = [item.status, form.status.data]
            item.status = form.status.data

    old_tags = sorted(t.name for t in item.tags)
    item.tags = svc.parse_tags(form.tags.data)
    new_tags = sorted(t.name for t in item.tags)
    if old_tags != new_tags and not creating:
        changes["tags"] = [", ".join(old_tags), ", ".join(new_tags)]
        svc.prune_tags()

    db.session.flush()
    changes.update(svc.apply_custom_values(item, {k: v for k, v in custom_values.items()
                                                  if v is not None or not creating}))
    if creating:
        log("item.create", f"Création de {item.code} — {item.name}", item=item)
    elif changes:
        log("item.update", "Fiche modifiée", item=item, changes=changes)
    db.session.commit()
    return item, {}


@bp.route("/nouveau", methods=["GET", "POST"])
@require("item.edit")
def create():
    form = ItemForm()
    if request.method == "GET":
        form.category_id.data = request.args.get("category_id", type=int)
        form.location_id.data = request.args.get("location_id", type=int)
    custom_errors = {}
    if request.method == "POST":
        item, custom_errors = _save_item(form)
        if item:
            flash(f"Objet {item.code} créé.", "success")
            if request.form.get("then") == "new":  # saisie à la chaîne : même catégorie et même lieu
                return redirect(url_for(".create", category_id=item.category_id, location_id=item.location_id))
            return redirect(url_for(".detail", item_id=item.id))
    category_id = form.category_id.data or (form.category_id.choices[0][0] if form.category_id.choices else None)
    return render_template("items/form.html", form=form, item=None,
                           fields=svc.fields_for_category(category_id),
                           values={int(k[3:]): v for k, v in request.form.items() if k.startswith("cf_")},
                           errors=custom_errors)


@bp.route("/<int:item_id>/modifier", methods=["GET", "POST"])
@require("item.edit")
def edit(item_id):
    item = get_item(item_id)
    form = ItemForm(obj=item)
    if item.status not in MANUAL_STATUSES:
        # Statut piloté par un prêt ou la réforme : affiché mais non modifiable.
        form.status.choices = [(item.status, item.status.capitalize())]
        form.status.data = item.status
    if request.method == "GET":
        form.tags.data = ", ".join(t.name for t in item.tags)
        form.consumption_mode.data = item.consumption_mode or ""
        if item.status in MANUAL_STATUSES:
            form.status.data = item.status
    custom_errors = {}
    if request.method == "POST":
        saved, custom_errors = _save_item(form, item)
        if saved:
            flash("Modifications enregistrées.", "success")
            return redirect(url_for(".detail", item_id=item.id))
    values = ({int(k[3:]): v for k, v in request.form.items() if k.startswith("cf_")} if request.method == "POST"
              else {v.field_id: v.value for v in item.custom_values})
    return render_template("items/form.html", form=form, item=item,
                           fields=svc.fields_for_category(form.category_id.data), values=values,
                           errors=custom_errors)


# ---------------------------------------------------------------------------
# Fiche
# ---------------------------------------------------------------------------

@bp.route("/<int:item_id>")
@require("inventory.view")
def detail(item_id):
    item = get_item(item_id)
    activities = db.session.scalars(
        db.select(Activity).where(Activity.item_id == item.id).order_by(Activity.created_at.desc(), Activity.id.desc())
        .limit(50)
    ).all()
    return render_template("items/detail.html", item=item, custom=svc.custom_display(item), activities=activities)


@bp.route("/<int:item_id>/reformer", methods=["POST"])
@require("item.delete")
def retire(item_id):
    item = get_item(item_id)
    if item.status in (ItemStatus.EN_GARDE, ItemStatus.SORTI):
        flash("Impossible de réformer un objet actuellement prêté.", "error")
    elif item.status == ItemStatus.REFORME:
        item.status = ItemStatus.DISPONIBLE
        log("item.restore", "Objet réintégré à l'inventaire", item=item)
        db.session.commit()
        flash("Objet réintégré.", "success")
    else:
        log("item.retire", "Objet réformé", item=item, changes={"status": [item.status, ItemStatus.REFORME]})
        item.status = ItemStatus.REFORME
        db.session.commit()
        flash("Objet réformé.", "success")
    return redirect(url_for(".detail", item_id=item.id))


@bp.route("/<int:item_id>/supprimer", methods=["POST"])
@require("item.delete")
def delete(item_id):
    item = get_item(item_id)
    try:
        trash.trash_item(item, current_user)
    except trash.TrashError as exc:
        flash(str(exc), "error")
        return redirect(url_for(".detail", item_id=item.id))
    db.session.commit()
    flash(f"Objet {item.code} mis à la corbeille : il peut être restauré pendant {trash.RETENTION_DAYS} jours.",
          "success")
    return redirect(url_for(".index"))


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

@bp.route("/<int:item_id>/photos", methods=["POST"])
@require("item.edit")
def add_photo(item_id):
    item = get_item(item_id)
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        flash("Aucun fichier envoyé.", "warning")
    added = 0
    for f in files:
        try:
            filename, thumb = save_photo(f)
        except InvalidImage as exc:
            flash(f"{f.filename} : {exc}", "error")
            continue
        item.photos.append(Photo(filename=filename, thumbnail=thumb, uploaded_by_id=current_user.id,
                                 caption=(request.form.get("caption") or "").strip()[:255] or None))
        added += 1
    if added:
        log("photo.add", f"{added} photo(s) ajoutée(s)", item=item)
        db.session.commit()
        flash(f"{added} photo(s) ajoutée(s).", "success")
    return redirect(url_for(".detail", item_id=item.id))


@bp.route("/photos/<int:photo_id>/supprimer", methods=["POST"])
@require("item.edit")
def delete_photo(photo_id):
    photo = db.session.get(Photo, photo_id) or abort(404)
    item = photo.item
    delete_photo_files(photo)
    db.session.delete(photo)
    log("photo.delete", "Photo supprimée", item=item)
    db.session.commit()
    return redirect(url_for(".detail", item_id=item.id))


@bp.route("/fichiers/<path:filename>")
@require("inventory.view")
def uploaded_file(filename):
    # Photos seulement : documents et photos de profil ont leurs propres routes et leurs propres droits.
    if not filename.startswith("photos/"):
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_DIR"], filename)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def _notes_response(item):
    if request.headers.get("HX-Request"):
        return render_template("items/_notes.html", item=item)
    return redirect(url_for(".detail", item_id=item.id) + "#notes")


@bp.route("/<int:item_id>/notes", methods=["POST"])
@require("note.add")
def add_note(item_id):
    item = get_item(item_id)
    text = (request.form.get("text") or "").strip()
    if text:
        db.session.add(Note(item=item, author_id=current_user.id, text=text[:5000]))
        db.session.commit()
    return _notes_response(item)


@bp.route("/notes/<int:note_id>/supprimer", methods=["POST"])
@require("note.add")
def delete_note(note_id):
    note = db.session.get(Note, note_id) or abort(404)
    ensure("note.delete", note)
    item = note.item
    db.session.delete(note)
    db.session.commit()
    return _notes_response(item)
