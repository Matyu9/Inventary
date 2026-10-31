"""Comptes utilisateurs (admin) et fiches emprunteurs (admin et profs)."""
import secrets
from datetime import datetime

import re
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory,
                   url_for)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import or_
from wtforms import BooleanField, EmailField, SelectField, StringField
from wtforms.validators import DataRequired, Length, Optional, Regexp, ValidationError

from ..extensions import db, fold, fold_contains
from ..models import AccountToken, Activity, Loan, LoanStatus, Notification, Role, User
from ..permissions import can, permissions_of, require
from ..services import accounts, oidc, people, roles, sessions
from ..services.photos import InvalidImage
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
    role = SelectField("Rôle", default=Role.EMPRUNTEUR)
    active = BooleanField("Peut se connecter")

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current
        # Seul un administrateur peut donner (ou retirer) le rôle d'administrateur.
        self.role.choices = roles.choices(include_admin=current_user.is_admin)

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


def managed_user(user_id):
    """Compte que la personne connectée peut gérer : un compte administrateur reste réservé aux admins."""
    user = get_user(user_id)
    if user.is_admin and not current_user.is_admin:
        abort(403)
    return user


def grant_access(user):
    """Ouvre l'accès d'un compte : invitation à choisir son mot de passe (lien par email, sinon à transmettre).

    Avec la connexion OIDC seule, il n'y a pas de mot de passe : la personne se connecte via le fournisseur.
    """
    if oidc.enabled() and current_app.config["OIDC_ONLY"]:
        log("user.invite", f"Accès ouvert à {user.full_name} (connexion OIDC)", target=user)
        db.session.commit()
        return render_template("users/invited.html", user=user, oidc_only=True)
    purpose = AccountToken.RESET if user.password_hash else AccountToken.INVITE
    raw = accounts.issue(user, purpose)
    invited = purpose == AccountToken.INVITE
    log("user.invite" if invited else "user.reset_link",
        f"{'Invitation' if invited else 'Lien de réinitialisation'} pour {user.full_name}", target=user)
    db.session.commit()
    sent = (accounts.send_invitation if invited else accounts.send_reset)(user, raw)
    return render_template("users/invited.html", user=user, link=accounts.link(raw), sent=sent, invited=invited,
                           ttl=accounts.TTL_LABELS[purpose])


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
    labels = dict(roles.choices())
    role = request.args.get("role")
    if role in labels:
        query = query.where(User.role == role)
    if q := (request.args.get("q") or "").strip():
        query = query.where(or_(*(fold_contains(c, q) for c in (User.first_name, User.last_name, User.email,
                                                                 User.section))))
    invited = invited_ids()
    status = request.args.get("statut")
    users = db.session.scalars(query.order_by(User.last_name, User.first_name)).all()
    if status in STATUSES:
        users = [u for u in users if status_of(u, invited) == status]
    else:
        users = [u for u in users if not u.archived_at]  # sans filtre, les fiches archivées sont masquées
    active_loans = dict(db.session.execute(
        db.select(Loan.borrower_id, db.func.count(Loan.id))
        .where(Loan.status.in_((LoanStatus.VALIDE, LoanStatus.EN_COURS))).group_by(Loan.borrower_id)).all())
    return render_template("users/index.html", users=users, role=role, q=q, active_loans=active_loans,
                           labels=labels, status=status, statuses=STATUSES, invited=invited, status_of=status_of,
                           bulk_actions=BULK_ACTIONS, role_choices=roles.choices(include_admin=current_user.is_admin))


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
        db.session.add(user)
        db.session.flush()
        log("user.create", f"Fiche créée : {user.full_name} ({user.role_label})", target=user)
        db.session.commit()
        if user.active:
            return grant_access(user)
        flash(f"Fiche de {user.full_name} créée.", "success")
        return redirect(request.args.get("retour") == "pret" and url_for("loans.create", borrower_id=user.id)
                        or url_for(".index"))
    return render_template("users/form.html", form=form, user=None, admin=admin)


@bp.route("/utilisateurs/<int:user_id>/modifier", methods=["GET", "POST"])
@require("user.manage")
def edit(user_id):
    user = managed_user(user_id)
    form = UserForm(obj=user, current=user)
    if request.method == "GET":
        form.member_status.data = user.member_status or ""
    if form.validate_on_submit():
        # On ne peut pas se retirer l'accès aux comptes (ni, pour un admin, le rôle d'administrateur).
        keeps_access = (form.role.data == Role.ADMIN if user.is_admin
                        else "user.manage" in permissions_of(form.role.data))
        if user.id == current_user.id and not (form.active.data and keeps_access):
            flash("Vous ne pouvez pas retirer vos propres droits sur les comptes.", "error")
        else:
            was_active = user.active
            _apply(form, user, allow_role=True)
            if not user.active:
                sessions.revoke(user)
                accounts.revoke(user)
            log("user.update", f"Fiche modifiée : {user.full_name}", target=user)
            db.session.commit()
            if user.active and not was_active and not user.password_hash:
                return grant_access(user)
            flash("Modifications enregistrées.", "success")
            return redirect(url_for(".detail", user_id=user.id))
    return render_template("users/form.html", form=form, user=user, admin=True)


@bp.route("/utilisateurs/<int:user_id>/mot-de-passe", methods=["POST"])
@require("user.manage")
def reset_password(user_id):
    user = managed_user(user_id)
    if not user.email:
        flash("Ajoutez d'abord un email à cette fiche.", "error")
        return redirect(url_for(".edit", user_id=user.id))
    password = temporary_password()
    user.set_password(password)
    user.must_change_password = True
    user.active = True
    user.archived_at = None
    sessions.revoke(user)
    accounts.revoke(user)
    log("user.password", f"Mot de passe provisoire généré pour {user.full_name}", target=user)
    db.session.commit()
    return render_template("users/password_created.html", user=user, password=password)


@bp.route("/utilisateurs/<int:user_id>/inviter", methods=["POST"])
@require("user.manage")
def invite(user_id):
    """(Re)envoie le lien pour choisir un mot de passe : invitation, ou réinitialisation s'il en a déjà un."""
    user = managed_user(user_id)
    if not user.email:
        flash("Ajoutez d'abord un email à cette fiche.", "error")
        return redirect(url_for(".edit", user_id=user.id))
    if not user.active or user.archived_at:
        flash("Ce compte n'a pas d'accès : cochez d'abord « Peut se connecter ».", "error")
        return redirect(url_for(".detail", user_id=user.id))
    return grant_access(user)


# ---------------------------------------------------------------------------
# Fiche détaillée
# ---------------------------------------------------------------------------

def status_of(user, invited_ids=()):
    if user.archived_at:
        return "archive"
    if not user.active:
        return "sans_acces"
    if user.id in invited_ids:
        return "invite"
    return "actif" if user.last_login_at else "jamais"


STATUSES = {"actif": "Actif", "jamais": "Jamais connecté", "invite": "Invitation en attente",
            "sans_acces": "Sans accès", "archive": "Archivé"}


def invited_ids():
    return set(db.session.scalars(db.select(AccountToken.user_id).where(
        AccountToken.purpose == AccountToken.INVITE, AccountToken.expires_at >= datetime.now())))


@bp.route("/utilisateurs/<int:user_id>")
@require("borrower.create")
def detail(user_id):
    user = get_user(user_id)
    manage = can(current_user, "user.manage") and (current_user.is_admin or not user.is_admin)
    loans = db.session.scalars(db.select(Loan).where(Loan.borrower_id == user.id)
                               .order_by(Loan.start.desc()).limit(100)).all()
    guaranteed = db.session.scalars(db.select(Loan).where(Loan.guarantor_id == user.id,
                                                          Loan.status.in_(people.OPEN_LOANS))
                                    .order_by(Loan.start)).all()
    context = {}
    if manage:
        context = {
            "sessions": sessions.of(user),
            "activities": db.session.scalars(
                db.select(Activity).where(or_(Activity.user_id == user.id, Activity.target_user_id == user.id))
                .order_by(Activity.created_at.desc(), Activity.id.desc()).limit(50)).all(),
            "deletable": not people.references(user),
            "device_label": sessions.device_label,
        }
    return render_template("users/detail.html", user=user, manage=manage, statuses=STATUSES,
                           status=status_of(user, invited_ids()), loans=loans, guaranteed=guaranteed, **context)


@bp.route("/utilisateurs/<int:user_id>/sessions/<session_id>/fermer", methods=["POST"])
@require("user.manage")
def revoke_session(user_id, session_id):
    user = managed_user(user_id)
    if sessions.revoke_one(user, session_id):
        log("user.session", f"Session de {user.full_name} fermée", target=user)
        db.session.commit()
        flash("Appareil déconnecté.", "success")
    return redirect(url_for(".detail", user_id=user.id) + "#sessions")


@bp.route("/utilisateurs/<int:user_id>/sessions/fermer", methods=["POST"])
@require("user.manage")
def revoke_sessions(user_id):
    user = managed_user(user_id)
    sessions.revoke(user, keep_current=True)
    log("user.session", f"Toutes les sessions de {user.full_name} fermées", target=user)
    db.session.commit()
    flash("Tous les appareils sont déconnectés." if user.id != current_user.id
          else "Vos autres appareils sont déconnectés.", "success")
    return redirect(url_for(".detail", user_id=user.id) + "#sessions")


# ---------------------------------------------------------------------------
# Photos de profil
# ---------------------------------------------------------------------------

@bp.route("/photos-de-profil/<name>")
@login_required
def avatar_file(name):
    """Photo de profil, visible de toute personne connectée (le nom de fichier est aléatoire)."""
    if not re.fullmatch(r"[0-9a-f]{32}\.jpg", name):
        abort(404)
    response = send_from_directory(Path(current_app.config["UPLOAD_DIR"]) / "avatars", name)
    response.cache_control.max_age = 7 * 24 * 3600  # une nouvelle photo a un nouveau nom
    response.cache_control.private = True
    return response


def upload_avatar(user):
    """Enregistre la photo envoyée dans le champ « photo ». Renvoie True si c'est fait. Sans commit."""
    file = request.files.get("photo")
    if not file or not file.filename:
        flash("Choisissez une photo.", "warning")
        return False
    try:
        people.set_avatar(user, file)
    except InvalidImage as exc:
        flash(str(exc), "error")
        return False
    return True


@bp.route("/utilisateurs/<int:user_id>/photo", methods=["POST"])
@require("user.manage")
def set_avatar(user_id):
    user = managed_user(user_id)
    if upload_avatar(user):
        log("user.avatar", f"Photo de profil de {user.full_name} modifiée", target=user)
        db.session.commit()
        flash("Photo enregistrée.", "success")
    return redirect(url_for(".detail", user_id=user.id))


@bp.route("/utilisateurs/<int:user_id>/photo/supprimer", methods=["POST"])
@require("user.manage")
def remove_avatar(user_id):
    user = managed_user(user_id)
    people.remove_avatar(user)
    log("user.avatar", f"Photo de profil de {user.full_name} retirée", target=user)
    db.session.commit()
    flash("Photo retirée.", "success")
    return redirect(url_for(".detail", user_id=user.id))


# ---------------------------------------------------------------------------
# Cycle de vie : archiver, supprimer, fusionner, actions groupées
# ---------------------------------------------------------------------------

@bp.route("/utilisateurs/<int:user_id>/archiver", methods=["POST"])
@require("user.manage")
def archive(user_id):
    user = managed_user(user_id)
    if user.id == current_user.id:
        flash("Vous ne pouvez pas archiver votre propre compte.", "error")
        return redirect(url_for(".detail", user_id=user.id))
    try:
        people.archive(user)
    except people.PeopleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        log("user.archive", f"Fiche archivée : {user.full_name}", target=user)
        db.session.commit()
        flash(f"{user.full_name} est archivé(e) : plus d'accès, historique conservé.", "success")
    return redirect(url_for(".detail", user_id=user.id))


@bp.route("/utilisateurs/<int:user_id>/desarchiver", methods=["POST"])
@require("user.manage")
def unarchive(user_id):
    user = managed_user(user_id)
    people.unarchive(user)
    log("user.unarchive", f"Fiche sortie des archives : {user.full_name}", target=user)
    db.session.commit()
    flash(f"{user.full_name} n'est plus archivé(e). Son accès reste fermé : rouvrez-le si besoin.", "success")
    return redirect(url_for(".detail", user_id=user.id))


@bp.route("/utilisateurs/<int:user_id>/supprimer", methods=["POST"])
@require("user.manage")
def delete(user_id):
    user = managed_user(user_id)
    if user.id == current_user.id:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "error")
        return redirect(url_for(".detail", user_id=user.id))
    name = user.full_name
    try:
        people.delete(user)
    except people.PeopleError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for(".detail", user_id=user_id))
    log("user.delete", f"Fiche supprimée : {name}")
    db.session.commit()
    flash(f"Fiche de {name} supprimée.", "success")
    return redirect(url_for(".index"))


@bp.route("/utilisateurs/<int:user_id>/fusionner", methods=["GET", "POST"])
@require("user.manage")
def merge(user_id):
    """Fusionne cette fiche (le doublon) dans une autre, qui est conservée."""
    source = managed_user(user_id)
    if request.method == "POST":
        target = managed_user(request.form.get("target_id", type=int) or 0)
        if source.id == current_user.id:
            flash("Fusionnez plutôt l'autre fiche dans la vôtre : votre compte doit être conservé.", "error")
            return redirect(url_for(".merge", user_id=source.id))
        name = source.full_name
        try:
            moved = people.merge(source, target)
        except people.PeopleError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for(".merge", user_id=source.id))
        log("user.merge", f"Fiche {name} fusionnée dans {target.full_name} "
            f"({sum(moved.values())} élément(s) transféré(s))", target=target)
        db.session.commit()
        flash(f"{name} a été fusionné(e) dans la fiche de {target.full_name}.", "success")
        return redirect(url_for(".detail", user_id=target.id))
    candidates = db.session.scalars(db.select(User).where(User.id != source.id)
                                    .order_by(User.last_name, User.first_name)).all()
    if not current_user.is_admin:
        candidates = [u for u in candidates if not u.is_admin]
    return render_template("users/merge.html", source=source, candidates=candidates,
                           suggested=_similar(source, candidates))


def _similar(user, candidates):
    """Doublons probables : même nom et prénom (sans accents ni casse), ou même email ou téléphone."""
    key = (fold(user.first_name), fold(user.last_name))
    return [u for u in candidates
            if (fold(u.first_name), fold(u.last_name)) == key
            or user.email and u.email == user.email or user.phone and u.phone == user.phone]


BULK_ACTIONS = {"activer": "Ouvrir l'accès", "desactiver": "Couper l'accès", "archiver": "Archiver",
                "role": "Changer le rôle"}


@bp.route("/utilisateurs/actions", methods=["POST"])
@require("user.manage")
def bulk():
    action = request.form.get("action")
    ids = request.form.getlist("user_ids", type=int)
    selected = db.session.scalars(db.select(User).where(User.id.in_(ids))).all() if ids else []
    if action not in BULK_ACTIONS or not selected:
        flash("Choisissez des personnes et une action.", "error")
        return redirect(url_for(".index"))
    new_role = request.form.get("role")
    if action == "role" and new_role not in dict(roles.choices(include_admin=current_user.is_admin)):
        flash("Choisissez le nouveau rôle.", "error")
        return redirect(url_for(".index"))
    done, skipped, invites = [], [], []
    for user in selected:
        reason = _bulk_skip(user, action, new_role)
        if reason:
            skipped.append(f"{user.full_name} ({reason})")
            continue
        if action == "activer":
            user.active = True
            if not user.password_hash and not (oidc.enabled() and current_app.config["OIDC_ONLY"]):
                invites.append((user, accounts.issue(user, AccountToken.INVITE)))
        elif action == "desactiver":
            user.active = False
            sessions.revoke(user)
            accounts.revoke(user)
        elif action == "archiver":
            try:
                people.archive(user)
            except people.PeopleError:
                skipped.append(f"{user.full_name} (prêts en cours)")
                continue
        elif action == "role":
            if user.role != new_role:
                user.role_from_oidc = False
            user.role = new_role
        done.append(user)
        log(f"user.bulk_{action}", f"{BULK_ACTIONS[action]} : {user.full_name}"
            + (f" → {roles.find(new_role).name}" if action == "role" else ""), target=user)
    db.session.commit()
    not_sent = [u.full_name for u, raw in invites if not accounts.send_invitation(u, raw)]
    if done:
        flash(f"{BULK_ACTIONS[action]} : {len(done)} personne(s).", "success")
    if not_sent:
        flash("Invitation non envoyée (email non configuré ou en panne) pour : " + ", ".join(not_sent)
              + ". Générez le lien depuis leur fiche.", "warning")
    if skipped:
        flash("Ignoré(s) : " + " ; ".join(skipped) + ".", "warning")
    return redirect(url_for(".index", **{k: v for k, v in request.args.items()}))


def _bulk_skip(user, action, new_role):
    if user.is_admin and not current_user.is_admin:
        return "compte administrateur"
    if user.id == current_user.id:
        keeps_access = new_role == Role.ADMIN if user.is_admin else "user.manage" in permissions_of(new_role)
        if action in ("desactiver", "archiver") or action == "role" and not keeps_access:
            return "votre propre compte"
    if action == "activer" and (not user.email or user.archived_at):
        return "archivé" if user.archived_at else "pas d'email"
    return None


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
