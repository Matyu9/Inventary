"""Liens envoyés par email (invitation, mot de passe oublié, changement d'email) et emails de compte."""
import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta

from flask import current_app, url_for

from ..extensions import db
from ..models import AccountToken
from . import branding, mailer

logger = logging.getLogger(__name__)

TTL = {
    AccountToken.INVITE: timedelta(days=7),
    AccountToken.RESET: timedelta(hours=1),
    AccountToken.EMAIL: timedelta(hours=24),
}
TTL_LABELS = {AccountToken.INVITE: "7 jours", AccountToken.RESET: "1 heure", AccountToken.EMAIL: "24 heures"}


def _digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(user, purpose, email=None):
    """Crée un lien à usage unique (et annule les précédents du même type). Renvoie le jeton en clair. Sans commit."""
    db.session.execute(db.delete(AccountToken).where(AccountToken.user_id == user.id,
                                                     AccountToken.purpose == purpose))
    raw = secrets.token_urlsafe(32)
    now = datetime.now()
    db.session.add(AccountToken(id=_digest(raw), user_id=user.id, purpose=purpose, email=email, created_at=now,
                                expires_at=now + TTL[purpose]))
    return raw


def find(raw):
    """Lien valable correspondant au jeton, sinon None (inconnu, expiré, ou compte désactivé)."""
    if not raw or len(raw) > 100:
        return None
    token = db.session.get(AccountToken, _digest(raw))
    if token is None or token.expires_at < datetime.now() or not token.user.active or token.user.archived_at:
        return None
    return token


def consume(token, *purposes):
    """Annule le lien utilisé et, pour le même compte, les autres liens des types donnés. Sans commit."""
    purposes = purposes or (token.purpose,)
    db.session.execute(db.delete(AccountToken).where(AccountToken.user_id == token.user_id,
                                                     AccountToken.purpose.in_(purposes)))


def revoke(user):
    """Annule tous les liens en attente de `user` (compte désactivé, archivé…). Sans commit."""
    db.session.execute(db.delete(AccountToken).where(AccountToken.user_id == user.id))


def pending_invitation(user):
    return db.session.scalar(db.select(AccountToken).where(
        AccountToken.user_id == user.id, AccountToken.purpose == AccountToken.INVITE,
        AccountToken.expires_at >= datetime.now()))


def link(raw):
    return current_app.config["BASE_URL"] + url_for("auth.token_link", token=raw)


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

def _send(to, user, subject, intro, button, url):
    """Envoie un email de compte. Renvoie True s'il est parti, False sinon (SMTP absent ou en panne)."""
    if not mailer.smtp_enabled() or not to:
        return False
    try:
        mailer.send(to, *mailer.compose_account(user, subject, intro, button, url))
    except (OSError, smtplib.SMTPException):
        logger.exception("Échec d'envoi à %s", to)
        return False
    return True


def send_invitation(user, raw):
    asso = branding.name()
    return _send(user.email, user, f"Votre accès à l'inventaire {asso}",
                 f"Un compte vous a été ouvert sur l'inventaire de {asso}.\n\n"
                 f"Choisissez votre mot de passe avec le bouton ci-dessous. Le lien est valable "
                 f"{TTL_LABELS[AccountToken.INVITE]}.", "Choisir mon mot de passe", link(raw))


def send_reset(user, raw):
    return _send(user.email, user, "Réinitialisation de votre mot de passe",
                 f"Une réinitialisation du mot de passe de votre compte a été demandée.\n\n"
                 f"Le lien est valable {TTL_LABELS[AccountToken.RESET]} et ne sert qu'une fois. Si vous n'êtes "
                 f"pas à l'origine de cette demande, ignorez cet email : votre mot de passe ne change pas.",
                 "Choisir un nouveau mot de passe", link(raw))


def send_email_confirmation(user, new_email, raw):
    return _send(new_email, user, "Confirmez votre nouvelle adresse email",
                 f"Vous avez demandé à utiliser cette adresse pour votre compte de l'inventaire.\n\n"
                 f"Confirmez-la avec le bouton ci-dessous (lien valable {TTL_LABELS[AccountToken.EMAIL]}). "
                 f"Sans confirmation, l'ancienne adresse reste en place.", "Confirmer l'adresse", link(raw))


def send_email_changed_notice(user, old_email):
    """Prévient l'ancienne adresse : un changement non souhaité se repère tout de suite."""
    return _send(old_email, user, "Adresse email modifiée",
                 f"L'adresse email de votre compte est désormais {user.email}.\n\n"
                 f"Si vous n'êtes pas à l'origine de ce changement, contactez vite un responsable de l'association.",
                 "Ouvrir l'inventaire", current_app.config["BASE_URL"])
