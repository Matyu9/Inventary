"""Permissions centralisées.

Toute vérification de droits passe par `can(user, action, obj)`, utilisée à la fois
par le décorateur `require` des routes et par les templates (pour masquer les boutons).
"""
from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from .models import Role

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR

# Droits qui ne dépendent que du rôle.
ROLE_PERMISSIONS = {
    "inventory.view": {A, P},
    "item.edit": {A, P},  # créer / modifier objets, kits, photos, tags, lieux
    "item.delete": {A},  # supprimer ou réformer
    "note.add": {A, P},
    "maintenance.edit": {A, P},
    "reminder.edit": {A, P},
    "stock.move": {A, P},
    "customfield.manage": {A},
    "borrower.create": {A, P},  # fiche emprunteur sans accès
    "user.manage": {A},  # comptes, activation, rôles
    "loan.request": {A, P},
    "loan.validate": {A},
    "loan.list_all": {A, P},
    "audit.scan": {A, P},
    "audit.manage": {A},
    "report.view": {A, P},
    "export": {A, P},
    "import": {A},
    "settings.manage": {A},
    "scan": {A, P},
    "label.print": {A, P},
    "calendar.view": {A, P, E},
    "myloans.view": {A, P, E},
}


def can(user, action, obj=None):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    role = user.role

    # Droits dépendant de l'objet.
    if action == "loan.view":
        return role in (A, P) or obj is not None and obj.borrower_id == user.id
    if action == "loan.checkout":  # remise / retour physique
        return role == A or role == P and obj is not None and obj.guarantor_id == user.id
    if action == "note.delete":
        return role == A or role == P and obj is not None and obj.author_id == user.id
    if action == "maintenance.delete":
        return role == A or role == P and obj is not None and obj.recorded_by_id == user.id
    if action == "loan.cancel":
        return role == A or obj is not None and user.id in (obj.requested_by_id, obj.guarantor_id)

    allowed = ROLE_PERMISSIONS.get(action)
    if allowed is None:
        raise KeyError(f"Permission inconnue : {action}")
    return role in allowed


def require(action):
    """Décorateur de route : connexion obligatoire + droit `action` (vérifié sans objet)."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not can(current_user, action):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def ensure(action, obj=None):
    """Vérification ponctuelle dans une vue, une fois l'objet chargé."""
    if not can(current_user, action, obj):
        abort(403)
