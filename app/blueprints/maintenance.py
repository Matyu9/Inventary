"""Entretiens et rappels d'un objet."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, SelectField, SelectMultipleField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from ..extensions import db
from ..forms import FrDecimalField, amount_range
from ..models import Item, ItemStatus, Maintenance, Reminder, User, get_live
from ..permissions import can, ensure, holders, require
from ..services.activity import log

bp = Blueprint("maintenance", __name__)

KINDS = ["révision", "réparation", "nettoyage", "réglage", "contrôle", "autre"]


class MaintenanceForm(FlaskForm):
    date = DateField("Date", default=date.today, validators=[DataRequired()])
    kind = SelectField("Type", choices=[(k, k.capitalize()) for k in KINDS], default=KINDS[0])
    provider = StringField("Prestataire / luthier", validators=[Optional(), Length(max=120)])
    cost = FrDecimalField("Coût (€)", places=2, validators=[Optional(), amount_range()])
    description = TextAreaField("Détail", validators=[Optional()])
    next_due = DateField("Prochain entretien", validators=[Optional()])
    status = SelectField("Statut de l'objet", choices=[("", "Inchangé"), (ItemStatus.EN_ENTRETIEN, "Mettre en entretien"),
                                                      (ItemStatus.DISPONIBLE, "Remettre disponible")], default="")

    def validate_next_due(self, field):
        if field.data and self.date.data and field.data <= self.date.data:
            raise ValidationError("Le prochain entretien doit être après la date de celui-ci.")


class ReminderForm(FlaskForm):
    due_date = DateField("Date", validators=[DataRequired()])
    message = StringField("Message", validators=[DataRequired(), Length(max=255)])
    recipients = SelectMultipleField("Destinataires (aucun = les administrateurs)", coerce=int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        staff = db.session.scalars(db.select(User).where(User.role.in_(holders("inventory.view")), User.active.is_(True))
                                   .order_by(User.last_name)).all()
        self.recipients.choices = [(u.id, u.full_name) for u in staff]


def _back(item, anchor):
    return redirect(url_for("items.detail", item_id=item.id) + anchor)


def _errors(form):
    for field in form:
        for error in field.errors:
            flash(f"{field.label.text} : {error}", "error")


@bp.route("/objets/<int:item_id>/entretiens", methods=["POST"])
@require("maintenance.edit")
def add(item_id):
    item = get_live(Item, item_id) or abort(404)
    form = MaintenanceForm()
    if not form.validate():
        _errors(form)
        return _back(item, "#entretiens")
    m = Maintenance(item=item, date=form.date.data, kind=form.kind.data, provider=form.provider.data or None,
                    cost=form.cost.data, description=form.description.data or None, next_due=form.next_due.data,
                    recorded_by_id=current_user.id)
    db.session.add(m)
    changes = {}
    if form.status.data and form.status.data != item.status:
        if item.status in (ItemStatus.DISPONIBLE, ItemStatus.EN_ENTRETIEN):
            changes["status"] = [item.status, form.status.data]
            item.status = form.status.data
        else:
            flash(f"Statut inchangé : l'objet est actuellement « {item.status} ».", "warning")
    log("maintenance.add", f"Entretien ({m.kind}) enregistré" + (f" chez {m.provider}" if m.provider else ""),
        item=item, changes=changes)
    db.session.commit()
    flash("Entretien enregistré.", "success")
    return _back(item, "#entretiens")


@bp.route("/entretiens/<int:maintenance_id>/supprimer", methods=["POST"])
@require("maintenance.edit")
def delete(maintenance_id):
    m = db.session.get(Maintenance, maintenance_id) or abort(404)
    ensure("maintenance.delete", m)
    item = m.item
    log("maintenance.delete", f"Entretien du {m.date:%d/%m/%Y} supprimé", item=item)
    db.session.delete(m)
    db.session.commit()
    return _back(item, "#entretiens")


@bp.route("/objets/<int:item_id>/rappels", methods=["POST"])
@require("reminder.edit")
def add_reminder(item_id):
    item = get_live(Item, item_id) or abort(404)
    form = ReminderForm()
    if not form.validate():
        _errors(form)
        return _back(item, "#rappels")
    reminder = Reminder(item=item, due_date=form.due_date.data, message=form.message.data.strip(),
                        created_by_id=current_user.id)
    reminder.recipients = db.session.scalars(db.select(User).where(User.id.in_(form.recipients.data))).all()
    db.session.add(reminder)
    log("reminder.add", f"Rappel prévu le {reminder.due_date:%d/%m/%Y} : {reminder.message}", item=item)
    db.session.commit()
    flash("Rappel enregistré.", "success")
    return _back(item, "#rappels")


@bp.route("/rappels/<int:reminder_id>/supprimer", methods=["POST"])
@require("reminder.edit")
def delete_reminder(reminder_id):
    reminder = db.session.get(Reminder, reminder_id) or abort(404)
    item = reminder.item
    db.session.delete(reminder)
    db.session.commit()
    return _back(item, "#rappels")


@bp.app_template_global()
def maintenance_form():
    return MaintenanceForm(formdata=None)


@bp.app_template_global()
def reminder_form():
    return ReminderForm(formdata=None)


@bp.app_template_global()
def item_reminders(item):
    return db.session.scalars(db.select(Reminder).where(Reminder.item_id == item.id)
                              .order_by(Reminder.sent, Reminder.due_date)).all()
