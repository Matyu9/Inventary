from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import or_
from wtforms import SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from ..extensions import db, fold_contains
from ..models import Activity, Item, ItemStatus, Kit, Loan, LoanLine, LoanStatus, get_live
from ..permissions import require
from ..services import trash
from ..services.activity import log
from .items import location_choices, none_if_zero

bp = Blueprint("kits", __name__, url_prefix="/kits")

ACTIVE = (LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS)


class KitForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    location_id = SelectField("Lieu de rangement", coerce=none_if_zero)
    description = TextAreaField("Description", validators=[Optional()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.location_choices = [(0, "—")] + location_choices()
        self.location_id.choices = self.location_choices


def get_kit(kit_id):
    return get_live(Kit, kit_id) or abort(404)


def kit_status(kit):
    """Statut global : disponible si tout est là, sinon le statut le plus parlant."""
    statuses = {i.status for i in kit.items}
    if not statuses:
        return "vide"
    for status in (ItemStatus.EN_GARDE, ItemStatus.SORTI):
        if status in statuses:
            return status
    return ItemStatus.DISPONIBLE if statuses == {ItemStatus.DISPONIBLE} else "incomplet"


def active_kit_loan(kit):
    return db.session.scalar(db.select(Loan).join(LoanLine).where(
        LoanLine.kit_id == kit.id, Loan.status.in_(ACTIVE)).limit(1))


def active_item_loan(item):
    return db.session.scalar(db.select(Loan).join(LoanLine).where(
        LoanLine.item_id == item.id, LoanLine.returned_at.is_(None), Loan.status.in_(ACTIVE)).limit(1))


@bp.app_template_global()
def kit_loans(kit):
    return db.session.scalars(db.select(Loan).join(LoanLine).where(LoanLine.kit_id == kit.id)
                              .order_by(Loan.start.desc()).limit(20)).all()


@bp.route("")
@require("inventory.view")
def index():
    kits = db.session.scalars(db.select(Kit).order_by(Kit.name)).all()
    return render_template("kits/index.html", kits=kits, kit_status=kit_status)


@bp.route("/nouveau", methods=["GET", "POST"])
@require("item.edit")
def create():
    form = KitForm()
    if form.validate_on_submit():
        kit = Kit(name=form.name.data.strip(), location_id=form.location_id.data,
                  description=form.description.data or None)
        db.session.add(kit)
        db.session.flush()
        log("kit.create", f"Kit créé : {kit.name}", kit=kit)
        db.session.commit()
        flash("Kit créé. Ajoutez-y maintenant les objets.", "success")
        return redirect(url_for(".detail", kit_id=kit.id))
    return render_template("kits/form.html", form=form, kit=None)


@bp.route("/<int:kit_id>")
@require("inventory.view")
def detail(kit_id):
    kit = get_kit(kit_id)
    activities = db.session.scalars(db.select(Activity).where(Activity.kit_id == kit.id)
                                    .order_by(Activity.created_at.desc(), Activity.id.desc()).limit(50)).all()
    return render_template("kits/detail.html", kit=kit, status=kit_status(kit), activities=activities,
                           locked=active_kit_loan(kit))


@bp.route("/<int:kit_id>/modifier", methods=["GET", "POST"])
@require("item.edit")
def edit(kit_id):
    kit = get_kit(kit_id)
    form = KitForm(obj=kit)
    if form.validate_on_submit():
        kit.name = form.name.data.strip()
        kit.location_id = form.location_id.data
        kit.description = form.description.data or None
        log("kit.update", "Kit modifié", kit=kit)
        db.session.commit()
        flash("Kit modifié.", "success")
        return redirect(url_for(".detail", kit_id=kit.id))
    return render_template("kits/form.html", form=form, kit=kit)


@bp.route("/<int:kit_id>/candidats")
@require("item.edit")
def candidates(kit_id):
    """Fragment HTMX : objets pouvant être ajoutés au kit."""
    kit = get_kit(kit_id)
    q = (request.args.get("q") or "").strip()
    items = []
    if q:
        items = db.session.scalars(db.select(Item).where(
            or_(fold_contains(Item.code, q), fold_contains(Item.name, q)),
            Item.kit_id.is_(None), Item.tracking == "individuel", Item.status != ItemStatus.REFORME,
        ).order_by(Item.code).limit(15)).all()
    return render_template("kits/_candidates.html", kit=kit, items=items, q=q)


@bp.route("/<int:kit_id>/objets", methods=["POST"])
@require("item.edit")
def add_item(kit_id):
    kit = get_kit(kit_id)
    item = get_live(Item, request.form.get("item_id", type=int) or 0) or abort(404)
    if active_kit_loan(kit):
        flash("Ce kit fait l'objet d'un prêt en cours : sa composition ne peut pas changer.", "error")
    elif item.kit_id:
        flash(f"{item.code} fait déjà partie du kit « {item.kit.name} ».", "error")
    elif item.is_quantity:
        flash("Les consommables ne peuvent pas être mis dans un kit.", "error")
    elif loan := active_item_loan(item):
        flash(f"{item.code} est concerné par le prêt n°{loan.id} : attendez sa fin.", "error")
    else:
        item.kit = kit
        log("kit.add", f"{item.code} ajouté au kit", kit=kit)
        log("kit.add", f"Ajouté au kit « {kit.name} »", item=item)
        db.session.commit()
        flash(f"{item.code} ajouté au kit.", "success")
    return redirect(url_for(".detail", kit_id=kit.id))


@bp.route("/<int:kit_id>/objets/<int:item_id>/retirer", methods=["POST"])
@require("item.edit")
def remove_item(kit_id, item_id):
    kit = get_kit(kit_id)
    item = get_live(Item, item_id)
    if item is None or item.kit_id != kit.id:
        abort(404)
    if active_kit_loan(kit):
        flash("Ce kit fait l'objet d'un prêt en cours : sa composition ne peut pas changer.", "error")
    else:
        item.kit = None
        log("kit.remove", f"{item.code} retiré du kit", kit=kit)
        log("kit.remove", f"Retiré du kit « {kit.name} »", item=item)
        db.session.commit()
        flash(f"{item.code} retiré du kit.", "success")
    return redirect(url_for(".detail", kit_id=kit.id))


@bp.route("/<int:kit_id>/supprimer", methods=["POST"])
@require("item.delete")
def delete(kit_id):
    kit = get_kit(kit_id)
    try:
        trash.trash_kit(kit, current_user)
    except trash.TrashError as exc:
        flash(str(exc), "error")
        return redirect(url_for(".detail", kit_id=kit.id))
    db.session.commit()
    flash(f"Kit « {kit.name} » mis à la corbeille : il peut être restauré pendant {trash.RETENTION_DAYS} jours.",
          "success")
    return redirect(url_for(".index"))
