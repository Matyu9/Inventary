from datetime import datetime, timedelta

from flask_login import current_user

from ..extensions import db
from ..models import Activity

# Durée de conservation des connexions au journal : elles contiennent des adresses IP.
LOGIN_RETENTION_DAYS = 365


def log(action, message, *, item=None, kit=None, loan=None, changes=None, user=None, target=None):
    """Ajoute une entrée au journal d'activité (sans commit). `target` : la personne concernée, s'il y en a une."""
    if user is None and current_user and current_user.is_authenticated:
        user = current_user
    entry = Activity(
        user=user,
        action=action,
        message=message,
        item_id=item.id if item else None,
        kit_id=kit.id if kit else None,
        loan_id=loan.id if loan else None,
        changes=changes or None,
        target_user_id=target.id if target else None,
    )
    db.session.add(entry)
    return entry


def purge_expired_logins(now=None):
    """Efface les connexions, déconnexions et échecs trop anciens (lancé par les rappels automatiques)."""
    limit = (now or datetime.now()) - timedelta(days=LOGIN_RETENTION_DAYS)
    return db.session.execute(db.delete(Activity).where(Activity.action.startswith("auth."),
                                                        Activity.created_at < limit)).rowcount


def diff(obj, fields, new_values):
    """Applique `new_values` sur `obj` et renvoie {champ: [ancien, nouveau]} pour ce qui a changé."""
    changes = {}
    for field in fields:
        if field not in new_values:
            continue
        old, new = getattr(obj, field), new_values[field]
        if old != new:
            changes[field] = [_jsonable(old), _jsonable(new)]
            setattr(obj, field, new)
    return changes


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
