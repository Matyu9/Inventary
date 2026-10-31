"""Catégories et champs personnalisés."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, SelectMultipleField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError

from ..extensions import db, fold
from ..models import Category, CategoryType, CustomField, CustomFieldValue, FieldType, Item
from ..permissions import ensure, require
from ..services.activity import log

bp = Blueprint("referentiel", __name__)

DEFAULT_COLOR = "#8b1e3f"
FIELD_TYPE_LABELS = {FieldType.TEXTE: "Texte", FieldType.NOMBRE: "Nombre", FieldType.DATE: "Date",
                     FieldType.BOOLEEN: "Oui / Non", FieldType.LISTE: "Liste de choix"}


class CategoryForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    type = SelectField("Type", choices=[(t, CategoryType.LABELS[t]) for t in CategoryType.ALL])
    color = StringField("Couleur", validators=[Optional(), Regexp(r"^#[0-9a-fA-F]{6}$", message="Format #RRGGBB")])
    # Un <input type="color"> envoie toujours une valeur (#000000 par défaut) : la case dit « pas de couleur ».
    no_color = BooleanField("Sans couleur")

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def chosen_color(self):
        return None if self.no_color.data else self.color.data or None

    def prefill_color(self):
        """Coche « Sans couleur » si besoin, et propose quand même une teinte au sélecteur."""
        self.no_color.data = not self.color.data
        self.color.data = self.color.data or DEFAULT_COLOR

    def validate_name(self, field):
        existing = db.session.scalar(db.select(Category).where(
            db.func.fold(Category.name) == fold(field.data.strip())))
        if existing and existing is not self._current:
            raise ValidationError("Cette catégorie existe déjà.")


class CustomFieldForm(FlaskForm):
    label = StringField("Libellé", validators=[DataRequired(), Length(max=80)])
    type = SelectField("Type", choices=list(FIELD_TYPE_LABELS.items()))
    options = TextAreaField("Choix possibles (un par ligne)")
    required = BooleanField("Obligatoire")
    position = IntegerField("Ordre d'affichage", default=0, validators=[Optional()])
    categories = SelectMultipleField("Catégories concernées (aucune = toutes)", coerce=int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.categories.choices = [(c.id, c.name) for c in
                                   db.session.scalars(db.select(Category).order_by(Category.name))]

    def option_list(self):
        return [o.strip() for o in (self.options.data or "").splitlines() if o.strip()]

    def validate_options(self, field):
        if self.type.data == FieldType.LISTE and not self.option_list():
            raise ValidationError("Indiquez au moins un choix.")


# ---------------------------------------------------------------------------
# Catégories
# ---------------------------------------------------------------------------

@bp.route("/categories", methods=["GET", "POST"])
@require("inventory.view")
def categories():
    form = CategoryForm()
    if request.method == "GET":
        form.prefill_color()
    else:
        ensure("item.edit")
        if form.validate():
            category = Category(name=form.name.data.strip(), type=form.type.data, color=form.chosen_color())
            db.session.add(category)
            log("category.create", f"Catégorie créée : {category.name}")
            db.session.commit()
            flash("Catégorie créée.", "success")
            return redirect(url_for(".categories"))
    counts = dict(db.session.execute(db.select(Item.category_id, db.func.count()).group_by(Item.category_id)).all())
    rows = db.session.scalars(db.select(Category).order_by(Category.type, Category.name)).all()
    return render_template("referentiel/categories.html", form=form, categories=rows, counts=counts,
                           labels=CategoryType.LABELS)


@bp.route("/categories/<int:category_id>/modifier", methods=["GET", "POST"])
@require("item.edit")
def edit_category(category_id):
    category = db.session.get(Category, category_id) or abort(404)
    form = CategoryForm(obj=category, current=category)
    if request.method == "GET":
        form.prefill_color()
    if form.validate_on_submit():
        category.name, category.type = form.name.data.strip(), form.type.data
        category.color = form.chosen_color()
        db.session.commit()
        flash("Catégorie modifiée.", "success")
        return redirect(url_for(".categories"))
    return render_template("referentiel/category_form.html", form=form, category=category)


@bp.route("/categories/<int:category_id>/supprimer", methods=["POST"])
@require("item.delete")
def delete_category(category_id):
    category = db.session.get(Category, category_id) or abort(404)
    used = db.session.scalar(db.select(Item.id).where(Item.category_id == category.id).limit(1)
                             .execution_options(include_deleted=True))
    if used:
        flash("Cette catégorie contient des objets (corbeille comprise) : elle ne peut pas être supprimée.", "error")
    else:
        log("category.delete", f"Catégorie supprimée : {category.name}")
        db.session.delete(category)
        db.session.commit()
        flash("Catégorie supprimée.", "success")
    return redirect(url_for(".categories"))


# ---------------------------------------------------------------------------
# Champs personnalisés
# ---------------------------------------------------------------------------

@bp.route("/champs-perso")
@require("customfield.manage")
def custom_fields():
    fields = db.session.scalars(db.select(CustomField).order_by(CustomField.position, CustomField.label)).all()
    return render_template("referentiel/custom_fields.html", fields=fields, labels=FIELD_TYPE_LABELS)


def _apply_field_form(form, field):
    field.label = form.label.data.strip()
    field.type = form.type.data
    field.options = form.option_list() if form.type.data == FieldType.LISTE else None
    field.required = form.required.data
    field.position = form.position.data or 0
    field.categories = db.session.scalars(db.select(Category).where(Category.id.in_(form.categories.data))).all()


@bp.route("/champs-perso/nouveau", methods=["GET", "POST"])
@require("customfield.manage")
def create_custom_field():
    form = CustomFieldForm()
    if form.validate_on_submit():
        field = CustomField()
        _apply_field_form(form, field)
        db.session.add(field)
        log("customfield.create", f"Champ perso créé : {field.label}")
        db.session.commit()
        flash("Champ créé.", "success")
        return redirect(url_for(".custom_fields"))
    return render_template("referentiel/custom_field_form.html", form=form, cf=None)


@bp.route("/champs-perso/<int:field_id>/modifier", methods=["GET", "POST"])
@require("customfield.manage")
def edit_custom_field(field_id):
    field = db.session.get(CustomField, field_id) or abort(404)
    form = CustomFieldForm(obj=field)
    if request.method == "GET":
        form.options.data = "\n".join(field.options or [])
        form.categories.data = [c.id for c in field.categories]
    if form.validate_on_submit():
        _apply_field_form(form, field)
        db.session.commit()
        flash("Champ modifié.", "success")
        return redirect(url_for(".custom_fields"))
    return render_template("referentiel/custom_field_form.html", form=form, cf=field)


@bp.route("/champs-perso/<int:field_id>/supprimer", methods=["POST"])
@require("customfield.manage")
def delete_custom_field(field_id):
    field = db.session.get(CustomField, field_id) or abort(404)
    used = db.session.scalar(db.select(db.func.count(CustomFieldValue.id)).where(CustomFieldValue.field_id == field.id))
    log("customfield.delete", f"Champ perso supprimé : {field.label} ({used} valeur(s))")
    db.session.delete(field)
    db.session.commit()
    flash("Champ supprimé.", "success")
    return redirect(url_for(".custom_fields"))
