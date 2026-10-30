from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from ..extensions import db
from ..models import Item, Kit, Location
from ..permissions import require
from ..services.activity import diff, log
from ..services.items import location_descendants
from .items import location_choices, none_if_zero

bp = Blueprint("locations", __name__, url_prefix="/lieux")


class LocationForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    parent_id = SelectField("Dans", coerce=none_if_zero)
    description = TextAreaField("Description", validators=[Optional()])

    def __init__(self, *args, exclude=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Un lieu ne peut pas être rangé dans lui-même ni dans un de ses sous-lieux.
        excluded = location_descendants(exclude) if exclude else set()
        self.parent_id.choices = [(0, "— (niveau racine)")] + [c for c in location_choices() if c[0] not in excluded]


def get_location(location_id):
    return db.session.get(Location, location_id) or abort(404)


def _counts():
    return dict(db.session.execute(
        db.select(Item.location_id, db.func.count()).group_by(Item.location_id)).all())


@bp.route("")
@require("inventory.view")
def index():
    roots = db.session.scalars(db.select(Location).where(Location.parent_id.is_(None)).order_by(Location.name)).all()
    unplaced = db.session.scalar(db.select(db.func.count(Item.id)).where(Item.location_id.is_(None)))
    return render_template("locations/index.html", roots=roots, counts=_counts(), unplaced=unplaced)


@bp.route("/<int:location_id>")
@require("inventory.view")
def detail(location_id):
    location = get_location(location_id)
    ids = location_descendants(location.id)
    items = db.session.scalars(db.select(Item).where(Item.location_id.in_(ids)).order_by(Item.code)).all()
    kits = db.session.scalars(db.select(Kit).where(Kit.location_id.in_(ids)).order_by(Kit.name)).all()
    return render_template("locations/detail.html", location=location, items=items, kits=kits, counts=_counts())


@bp.route("/nouveau", methods=["GET", "POST"])
@require("item.edit")
def create():
    form = LocationForm()
    if request.method == "GET":
        form.parent_id.data = request.args.get("parent_id", type=int)
    if form.validate_on_submit():
        location = Location(name=form.name.data.strip(), parent_id=form.parent_id.data,
                            description=form.description.data or None)
        db.session.add(location)
        log("location.create", f"Lieu créé : {location.name}")
        db.session.commit()
        flash("Lieu créé.", "success")
        return redirect(url_for(".detail", location_id=location.id))
    return render_template("locations/form.html", form=form, location=None)


@bp.route("/<int:location_id>/modifier", methods=["GET", "POST"])
@require("item.edit")
def edit(location_id):
    location = get_location(location_id)
    form = LocationForm(obj=location, exclude=location.id)
    if form.validate_on_submit():
        changes = diff(location, ("name", "parent_id", "description"), {
            "name": form.name.data.strip(), "parent_id": form.parent_id.data,
            "description": form.description.data or None})
        if changes:
            log("location.update", f"Lieu modifié : {location.name}", changes=changes)
        db.session.commit()
        flash("Lieu modifié.", "success")
        return redirect(url_for(".detail", location_id=location.id))
    return render_template("locations/form.html", form=form, location=location)


@bp.route("/<int:location_id>/supprimer", methods=["POST"])
@require("item.delete")
def delete(location_id):
    location = get_location(location_id)
    if location.items or location.children:
        flash("Videz ce lieu (objets et sous-lieux) avant de le supprimer.", "error")
        return redirect(url_for(".detail", location_id=location.id))
    log("location.delete", f"Lieu supprimé : {location.name}")
    db.session.delete(location)
    db.session.commit()
    flash("Lieu supprimé.", "success")
    return redirect(url_for(".index"))
