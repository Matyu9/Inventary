"""Permissions centralisées.

Toute vérification de droits passe par `can(user, action, obj)`, utilisée à la fois
par le décorateur `require` des routes et par les templates (pour masquer les boutons).
Les droits de chaque rôle se cochent dans l'écran « Rôles » (table `role`) ; l'administrateur les a tous.
"""
from functools import wraps

from flask import abort, has_app_context
from flask_login import current_user, login_required

from .extensions import db
from .models import Role

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR

# Catalogue des droits, regroupés comme dans l'écran « Rôles ».
PERMISSION_GROUPS = [
    ("Inventaire", [
        ("inventory.view", "Voir l'inventaire (objets, kits, consommables, lieux)"),
        ("item.edit", "Créer et modifier objets, kits, photos, tags et lieux"),
        ("item.delete", "Supprimer ou réformer du matériel"),
        ("note.add", "Ajouter des notes (et supprimer les siennes)"),
        ("note.delete_any", "Supprimer les notes des autres"),
        ("maintenance.edit", "Enregistrer des entretiens (et supprimer les siens)"),
        ("maintenance.delete_any", "Supprimer les entretiens des autres"),
        ("reminder.edit", "Programmer des rappels"),
        ("stock.move", "Entrées et sorties de stock des consommables"),
        ("scan", "Scanner les QR codes"),
        ("label.print", "Imprimer les étiquettes"),
    ]),
    ("Prêts", [
        ("myloans.view", "Voir ses propres prêts"),
        ("calendar.view", "Voir le calendrier"),
        ("loan.request", "Faire une demande de prêt"),
        ("loan.guarantee", "Être garant d'un prêt (et le remettre ou le récupérer)"),
        ("loan.list_all", "Voir tous les prêts"),
        ("loan.validate", "Valider ou refuser les demandes"),
        ("loan.checkout_any", "Remettre et récupérer n'importe quel prêt"),
        ("loan.cancel_any", "Annuler n'importe quel prêt"),
        ("loan.verify", "Pointer le matériel d'un prêt (départ, fin de concert)"),
    ]),
    ("Inventaires et rapports", [
        ("audit.scan", "Participer aux inventaires"),
        ("audit.manage", "Ouvrir et clôturer les inventaires"),
        ("report.view", "Voir les rapports"),
        ("export", "Exporter"),
        ("import", "Importer"),
    ]),
    ("Administration", [
        ("borrower.create", "Créer des fiches emprunteurs (sans accès)"),
        ("user.manage", "Gérer les comptes et leur attribuer un rôle"),
        ("customfield.manage", "Gérer les champs personnalisés"),
        ("settings.manage", "Paramètres de l'appli"),
    ]),
]
PERMISSIONS = {key: label for _, perms in PERMISSION_GROUPS for key, label in perms}

# Droits des rôles de base tant qu'ils n'ont pas été modifiés (et à la création de la base).
DEFAULT_PERMISSIONS = {
    A: set(PERMISSIONS),
    P: {"inventory.view", "item.edit", "note.add", "maintenance.edit", "reminder.edit", "stock.move", "scan",
        "label.print", "myloans.view", "calendar.view", "loan.request", "loan.guarantee", "loan.list_all",
        "loan.verify", "audit.scan", "report.view", "export", "borrower.create"},
    E: {"myloans.view", "calendar.view"},
}


# Droits vérifiés sur un objet précis (prêt, note, entretien) : déduits des droits ci-dessus dans can().
OBJECT_ACTIONS = {"loan.view", "loan.checkout", "note.delete", "maintenance.delete", "loan.cancel"}


def permissions_of(role_key):
    """Droits d'un rôle : tous pour l'administrateur, sinon ceux enregistrés (ou ceux par défaut)."""
    if role_key == A:
        return set(PERMISSIONS)
    role = db.session.get(Role, role_key) if has_app_context() else None
    if role is not None:
        return set(role.permissions or ())
    return DEFAULT_PERMISSIONS.get(role_key, set())


def can(user, action, obj=None):
    if action not in PERMISSIONS and action not in OBJECT_ACTIONS:
        raise KeyError(f"Permission inconnue : {action}")
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    perms = permissions_of(user.role)

    # Droits dépendant de l'objet.
    if action == "loan.view":
        return "loan.list_all" in perms or obj is not None and obj.borrower_id == user.id
    if action == "loan.checkout":  # remise / retour physique
        return "loan.checkout_any" in perms or (
            "loan.guarantee" in perms and obj is not None and obj.guarantor_id == user.id)
    if action == "note.delete":
        return "note.delete_any" in perms or "note.add" in perms and obj is not None and obj.author_id == user.id
    if action == "maintenance.delete":
        return "maintenance.delete_any" in perms or (
            "maintenance.edit" in perms and obj is not None and obj.recorded_by_id == user.id)
    if action == "loan.cancel":
        return "loan.cancel_any" in perms or obj is not None and user.id in (obj.requested_by_id, obj.guarantor_id)
    return action in perms


def holders(permission):
    """Clés des rôles qui ont ce droit (pour filtrer les personnes en SQL)."""
    keys = {A} | {k for k, perms in DEFAULT_PERMISSIONS.items() if permission in perms}
    for role in db.session.scalars(db.select(Role)):
        if role.key != A:
            (keys.add if permission in (role.permissions or ()) else keys.discard)(role.key)
    return keys


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
