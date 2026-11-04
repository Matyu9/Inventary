"""Jetons d'accès à l'API : création, vérification, révocation.

Le jeton n'est montré qu'une fois, à sa création ; la base n'en garde que l'empreinte SHA-256.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from ..extensions import db
from ..models import ApiToken

PREFIX = "ohn_"
# Durées proposées à la création : jours (None = sans expiration).
LIFETIMES = {"30": ("30 jours", 30), "90": ("90 jours", 90), "365": ("1 an", 365), "": ("Sans expiration", None)}
DEFAULT_LIFETIME = "90"
# Mise à jour de « dernière utilisation » au plus toutes les 5 minutes : pas d'écriture à chaque appel.
USED_EVERY = timedelta(minutes=5)


def _digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(user, name, days=None):
    """Crée un jeton pour `user` et renvoie (jeton en clair, ApiToken). Sans commit."""
    raw = PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(id=_digest(raw), user_id=user.id, name=name, prefix=raw[:len(PREFIX) + 6],
                     expires_at=datetime.now() + timedelta(days=days) if days else None)
    db.session.add(token)
    return raw, token


def authenticate(raw):
    """Jeton correspondant, s'il est valable et que son propriétaire a toujours accès à l'appli ; sinon None."""
    if not raw or not raw.startswith(PREFIX):
        return None
    token = db.session.get(ApiToken, _digest(raw))
    if token is None or token.expired:
        return None
    user = token.user
    if not user.active or user.archived_at is not None:
        return None
    now = datetime.now()
    if token.last_used_at is None or now - token.last_used_at >= USED_EVERY:
        token.last_used_at = now
        db.session.commit()
    return token


def of(user):
    """Jetons de `user`, le plus récent d'abord (expirés compris, pour qu'on les voie et les supprime)."""
    return db.session.scalars(db.select(ApiToken).where(ApiToken.user_id == user.id)
                              .order_by(ApiToken.created_at.desc())).all()


def revoke_one(user, token_id):
    """Supprime un jeton de `user`. Renvoie le jeton supprimé, ou None s'il n'existe pas (ou est à quelqu'un
    d'autre). Sans commit."""
    token = db.session.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        return None
    db.session.delete(token)
    return token


def revoke(user):
    """Supprime tous les jetons de `user`. Renvoie leur nombre. Sans commit."""
    return db.session.execute(db.delete(ApiToken).where(ApiToken.user_id == user.id)).rowcount

