from flask_login import current_user

from ..extensions import db
from ..models import Activity


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
