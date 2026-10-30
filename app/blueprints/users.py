"""Comptes utilisateurs (admin) et fiches emprunteurs (admin et profs)."""
import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import or_
from wtforms import BooleanField, EmailField, SelectField, StringField
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError

from ..extensions import db, fold_contains
from ..models import Loan, LoanStatus, Notification, Role, User
from ..permissions import can, require
from ..services.activity import log

bp = Blueprint("users", __name__)

MEMBER_STATUSES = [("", "—"), ("musicien", "Musicien"), ("élève", "Élève"), ("autre", "Autre")]


class UserForm(FlaskForm):
    first_name = StringField("Prénom", validators=[DataRequired(), Length(max=80)])
    last_name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    email = EmailField("Email", validators=[Optional(), Length(max=255),
                                            Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Adresse email invalide.")])
    phone = StringField("Téléphone", validators=[Optional(), Length(max=30)])
    section = StringField("Pupitre / classe", validators=[Optional(), Length(max=80)])
    member_status = SelectField("Statut", choices=MEMBER_STATUSES)
    role = SelectField("Rôle", choices=[(r, Role.LABELS[r]) for r in Role.ALL], default=Role.EMPRUNTEUR)
    active = BooleanField("Peut se connecter")

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def validate_email(self, field):
        if not field.data:
            return
        existing = db.session.scalar(db.select(User).where(db.func.lower(User.email) == field.data.strip().lower()))
        if existing and existing is not self._current:
            raise ValidationError("Cet email est déjà utilisé.")

    def validate_active(self, field):
        if field.data and not self.email.data:
            raise ValidationError("Un email est nécessaire pour se connecter.")


def get_user(user_id):
    return db.session.get(User, user_id) or abort(404)


def temporary_password():
    return "-".join(secrets.token_hex(2) for _ in range(3))  # ex. 3f9a-0c41-b2d7


def _apply(form, user, allow_role):
    user.first_name = form.first_name.data.strip()
    user.last_name = form.last_name.data.strip()
    user.email = (form.email.data or "").strip().lower() or None
    user.phone = form.phone.data or None
    user.section = form.section.data or None
    user.member_status = form.member_status.data or None
    if allow_role:
        if user.role != form.role.data:
            user.role_from_oidc = False  # choisi par un admin : les groupes OIDC ne le remplacent plus
        user.role = form.role.data
        user.active = form.active.data


@bp.route("/utilisateurs")
@require("borrower.create")
def index():
    query = db.select(User)
    role = request.args.get("role")
    if role in Role.ALL:
        query = query.where(User.role == role)
    if q := (request.args.get("q") or "").strip():
        query = query.where(or_(*(fold_contains(c, q) for c in (User.first_name, User.last_name, User.email,
                                                                 User.section))))
    users = db.session.scalars(query.order_by(User.last_name, User.first_name)).all()
    active_loans = dict(db.session.execute(
        db.select(Loan.borrower_id, db.func.count(Loan.id))
        .where(Loan.status.in_((LoanStatus.VALIDE, LoanStatus.EN_COURS))).group_by(Loan.borrower_id)).all())
    return render_template("users/index.html", users=users, role=role, q=q, active_loans=active_loans,
                           labels=Role.LABELS)


@bp.route("/utilisateurs/nouveau", methods=["GET", "POST"])
@require("borrower.create")
def create():
    admin = can(current_user, "user.manage")
    form = UserForm()
    if not admin:
        # Un prof crée une simple fiche emprunteur, sans accès.
        form.role.data, form.active.data = Role.EMPRUNTEUR, False
    if form.validate_on_submit():
        user = User()
        _apply(form, user, allow_role=admin)
        if not admin:
            user.role, user.active = Role.EMPRUNTEUR, False
        password = None
        if user.active:
            password = temporary_password()
            user.set_password(password)
            user.must_change_password = True
        db.session.add(user)
        db.session.flush()
        log("user.create", f"Fiche créée : {user.full_name} ({user.role_label})")
        db.session.commit()
        if password:
            return render_template("users/password_created.html", user=user, password=password)
        flash(f"Fiche de {user.full_name} créée.", "success")
        return redirect(request.args.get("retour") == "pret" and url_for("loans.create", borrower_id=user.id)
                        or url_for(".index"))
    return render_template("users/form.html", form=form, user=None, admin=admin)


@bp.route("/utilisateurs/<int:user_id>/modifier", methods=["GET", "POST"])
@require("user.manage")
def edit(user_id):
    user = get_user(user_id)
    form = UserForm(obj=user, current=user)
    if request.method == "GET":
        form.member_status.data = user.member_status or ""
    if form.validate_on_submit():
        if user.id == current_user.id and (form.role.data != Role.ADMIN or not form.active.data):
            flash("Vous ne pouvez pas retirer vos propres droits d'administrateur.", "error")
        else:
            was_active = user.active
            _apply(form, user, allow_role=True)
            password = None
            if user.active and not was_active and not user.password_hash:
                password = temporary_password()
                user.set_password(password)
                user.must_change_password = True
            log("user.update", f"Fiche modifiée : {user.full_name}")
            db.session.commit()
            if password:
                return render_template("users/password_created.html", user=user, password=password)
            flash("Modifications enregistrées.", "success")
            return redirect(url_for(".index"))
    return render_template("users/form.html", form=form, user=user, admin=True)


@bp.route("/utilisateurs/<int:user_id>/mot-de-passe", methods=["POST"])
@require("user.manage")
def reset_password(user_id):
    user = get_user(user_id)
    if not user.email:
        flash("Ajoutez d'abord un email à cette fiche.", "error")
        return redirect(url_for(".edit", user_id=user.id))
    password = temporary_password()
    user.set_password(password)
    user.must_change_password = True
    user.active = True
    log("user.password", f"Mot de passe provisoire généré pour {user.full_name}")
    db.session.commit()
    return render_template("users/password_created.html", user=user, password=password)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@bp.route("/notifications")
@require("myloans.view")
def notifications():
    items = db.session.scalars(
        db.select(Notification).where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(100)).all()
    unread = [n for n in items if not n.read]
    for n in unread:
        n.read = True
    db.session.commit()
    return render_template("users/notifications.html", notifications=items, unread_ids={n.id for n in unread})


@bp.app_template_global()
def unread_notifications():
    if not current_user.is_authenticated:
        return 0
    return db.session.scalar(db.select(db.func.count(Notification.id)).where(
        Notification.user_id == current_user.id, Notification.read.is_(False)))
