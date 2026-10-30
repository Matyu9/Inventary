"""Envoi d'emails via SMTP (facultatif)."""
import logging
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import current_app

from ..extensions import db
from ..models import Notification, User

logger = logging.getLogger(__name__)
# Une notification plus ancienne n'est plus envoyée (ex. SMTP configuré après coup).
MAX_AGE = timedelta(days=2)


def smtp_enabled():
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and (cfg.get("SMTP_FROM") or cfg.get("SMTP_USER")))


def send(to, subject, body):
    cfg = current_app.config
    message = EmailMessage()
    message["From"] = cfg["SMTP_FROM"] or cfg["SMTP_USER"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
        if cfg["SMTP_TLS"]:
            smtp.starttls()
        if cfg["SMTP_USER"]:
            smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        smtp.send_message(message)


def deliver_pending(limit=200):
    """Envoie par email les notifications récentes pas encore envoyées. Renvoie le nombre d'emails envoyés."""
    if not smtp_enabled():
        return 0
    cfg = current_app.config
    since = datetime.now() - MAX_AGE
    pending = db.session.scalars(
        db.select(Notification).join(User).where(
            Notification.emailed.is_(False), Notification.created_at >= since,
            User.email.is_not(None), User.active.is_(True))
        .order_by(Notification.id).limit(limit)).all()
    sent = 0
    for notification in pending:
        body = notification.message
        if notification.link:
            body += f"\n\n{cfg['BASE_URL']}{notification.link}"
        body += f"\n\n— Inventaire {cfg['ASSO_NAME']}"
        try:
            send(notification.user.email, f"[{cfg['ASSO_NAME']}] {notification.message[:80]}", body)
        except (OSError, smtplib.SMTPException):
            logger.exception("Échec d'envoi à %s", notification.user.email)
            break  # serveur indisponible : on réessaiera au prochain passage
        notification.emailed = True
        sent += 1
    db.session.commit()
    return sent


def deliver_quietly():
    """Après une action utilisateur : tente l'envoi sans jamais faire échouer la requête."""
    try:
        return deliver_pending(limit=20)
    except Exception:  # noqa: BLE001
        logger.exception("Envoi des emails impossible")
        db.session.rollback()
        return 0
