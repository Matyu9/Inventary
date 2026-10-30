"""Notifications dans l'appli (et par email quand le SMTP est configuré, cf. reminders.py)."""
from ..extensions import db
from ..models import Notification, Role, User


def notify(users, message, link=None, dedup_key=None):
    """Crée une notification par destinataire (sans commit). `dedup_key` évite les doublons des rappels."""
    created = []
    for user in {u.id: u for u in users if u is not None}.values():
        key = f"{dedup_key}:{user.id}" if dedup_key else None
        if key and db.session.scalar(db.select(Notification.id).filter_by(dedup_key=key)):
            continue
        notification = Notification(user_id=user.id, message=message[:500], link=link, dedup_key=key)
        db.session.add(notification)
        created.append(notification)
    return created


def admins():
    return db.session.scalars(db.select(User).where(User.role == Role.ADMIN, User.active.is_(True))).all()
