"""Mon compte : chacun gère son profil, son adresse email et ses appareils connectés."""
from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import EmailField, PasswordField, StringField
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError

from ..extensions import db
from ..models import AccountToken, User
from ..services import accounts, mailer, people, sessions
from ..services.activity import log
from .users import upload_avatar

bp = Blueprint("account", __name__, url_prefix="/mon-compte")


@bp.before_request
@login_required
def logged_in():
    pass


class ProfileForm(FlaskForm):
    first_name = StringField("Prénom", validators=[DataRequired(), Length(max=80)])
    last_name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    phone = StringField("Téléphone", validators=[Optional(), Length(max=30)])


class EmailForm(FlaskForm):
    email = EmailField("Nouvelle adresse", validators=[
        DataRequired(), Length(max=255), Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Adresse email invalide.")])
    password = PasswordField("Mot de passe actuel")

    def validate_email(self, field):
        email = field.data.strip().lower()
        if email == (current_user.email or ""):
            raise ValidationError("C'est déjà votre adresse.")
        if db.session.scalar(db.select(User).where(db.func.lower(User.email) == email)):
            raise ValidationError("Cette adresse est déjà utilisée par un autre compte.")

    def validate_password(self, field):
        # Confirme que c'est bien la personne : une session laissée ouverte ne suffit pas à détourner le compte.
        if current_user.password_hash and not current_user.check_password(field.data or ""):
            raise ValidationError("Mot de passe incorrect.")


def _render(profile=None, email_form=None):
    return render_template("account/index.html", profile=profile or ProfileForm(obj=current_user),
                           email_form=email_form or EmailForm(), sessions=sessions.of(current_user),
                           device_label=sessions.device_label, smtp=mailer.smtp_enabled(),
                           pending=db.session.scalar(db.select(AccountToken).where(
                               AccountToken.user_id == current_user.id, AccountToken.purpose == AccountToken.EMAIL)))


@bp.route("", methods=["GET", "POST"])
def index():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        current_user.first_name = form.first_name.data.strip()
        current_user.last_name = form.last_name.data.strip()
        current_user.phone = (form.phone.data or "").strip() or None
        log("account.profile", f"{current_user.full_name} a modifié son profil", target=current_user)
        db.session.commit()
        flash("Profil enregistré.", "success")
        return redirect(url_for(".index"))
    return _render(profile=form)


@bp.route("/email", methods=["POST"])
def change_email():
    form = EmailForm()
    if not mailer.smtp_enabled():
        flash("L'envoi d'emails n'est pas configuré : demandez à un responsable de changer votre adresse.", "error")
        return redirect(url_for(".index"))
    if not form.validate_on_submit():
        return _render(email_form=form)
    email = form.email.data.strip().lower()
    raw = accounts.issue(current_user, AccountToken.EMAIL, email=email)
    log("account.email_request", f"{current_user.full_name} a demandé à changer son email pour {email}",
        target=current_user)
    db.session.commit()
    if accounts.send_email_confirmation(current_user, email, raw):
        flash(f"Un lien de confirmation a été envoyé à {email}. L'adresse changera une fois confirmée.", "success")
    else:
        flash("L'email de confirmation n'a pas pu partir : réessayez plus tard.", "error")
    return redirect(url_for(".index"))


@bp.route("/photo", methods=["POST"])
def set_avatar():
    if upload_avatar(current_user):
        log("account.avatar", f"{current_user.full_name} a changé sa photo de profil", target=current_user)
        db.session.commit()
        flash("Photo enregistrée.", "success")
    return redirect(url_for(".index"))


@bp.route("/photo/supprimer", methods=["POST"])
def remove_avatar():
    people.remove_avatar(current_user)
    db.session.commit()
    flash("Photo retirée.", "success")
    return redirect(url_for(".index"))


@bp.route("/sessions/<session_id>/fermer", methods=["POST"])
def revoke_session(session_id):
    if session_id == current_user.session_token:
        flash("Pour fermer cette session, utilisez « Déconnexion ».", "info")
    elif sessions.revoke_one(current_user, session_id):
        db.session.commit()
        flash("Appareil déconnecté.", "success")
    return redirect(url_for(".index") + "#sessions")


@bp.route("/sessions/fermer", methods=["POST"])
def revoke_others():
    sessions.revoke(current_user, keep_current=True)
    db.session.commit()
    flash("Vos autres appareils sont déconnectés.", "success")
    return redirect(url_for(".index") + "#sessions")
