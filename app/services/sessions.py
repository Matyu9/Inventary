"""Sessions de connexion enregistrées en base, pour pouvoir les révoquer (déconnexion, mot de passe changé)."""
import secrets
from datetime import datetime, timedelta

from flask import current_app
from flask_login import current_user, login_user, logout_user

from ..extensions import db
from ..models import LoginSession


def start(user, remember=False):
    """Ouvre une session pour `user` sur cet appareil (à la place de login_user)."""
    lifetime = current_app.config.get("REMEMBER_COOKIE_DURATION", timedelta(days=365))
    db.session.execute(db.delete(LoginSession).where(LoginSession.created_at < datetime.now() - lifetime))
    user.session_token = secrets.token_urlsafe(32)
    db.session.add(LoginSession(id=user.session_token, user_id=user.id))
    db.session.commit()
    login_user(user, remember=remember)


def end():
    """Déconnecte cet appareil : son cookie, même rejoué, ne donne plus accès."""
    if current_user.is_authenticated:
        db.session.execute(db.delete(LoginSession).where(LoginSession.id == current_user.session_token))
        db.session.commit()
    logout_user()


def revoke(user, keep_current=False):
    """Déconnecte les appareils de `user` (tous, ou tous sauf celui de la requête en cours). Sans commit."""
    query = db.delete(LoginSession).where(LoginSession.user_id == user.id)
    if keep_current and current_user.is_authenticated and current_user.id == user.id:
        query = query.where(LoginSession.id != current_user.session_token)
    db.session.execute(query)


def revoke_all():
    """Déconnecte tout le monde sauf l'appareil de la requête en cours. Renvoie le nombre de sessions fermées. Sans commit."""
    query = db.delete(LoginSession)
    if current_user.is_authenticated:
        query = query.where(LoginSession.id != current_user.session_token)
    return db.session.execute(query).rowcount


def stats():
    """(sessions ouvertes, personnes connectées), pour la page Paramètres."""
    row = db.session.execute(db.select(db.func.count(LoginSession.id),
                                       db.func.count(db.distinct(LoginSession.user_id)))).one()
    return tuple(row)
