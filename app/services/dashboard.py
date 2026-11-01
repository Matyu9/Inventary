"""Tableau de bord à la carte : chacun choisit les blocs affichés et leur ordre (stocké dans User.dashboard)."""
from dataclasses import dataclass

from ..permissions import can


@dataclass(frozen=True)
class Widget:
    label: str
    permission: str | None = None  # droit nécessaire pour voir le bloc
    note: str | None = None
    half: bool = False  # demi-largeur : côte à côte avec un autre bloc demi-largeur


# Dans l'ordre par défaut.
WIDGETS = {
    "stats": Widget("Nombre d'objets par statut"),
    "to_confirm": Widget("Demandes à confirmer comme garant", note="affiché seulement s'il y en a"),
    "pending": Widget("Demandes à valider", "loan.validate"),
    "overdue": Widget("Prêts en retard"),
    "upcoming": Widget("Sorties des 7 prochains jours"),
    "maintenances": Widget("Entretiens à prévoir", half=True),
    "low_stock": Widget("Stocks bas", half=True),
}


def available(user):
    """Blocs auxquels la personne a droit, dans l'ordre par défaut."""
    return [key for key, w in WIDGETS.items() if w.permission is None or can(user, w.permission)]


def layout(user):
    """[(clé, affiché)] dans l'ordre choisi. Un bloc nouveau (ou un droit obtenu depuis) s'ajoute à la fin, affiché."""
    keys = available(user)
    saved = user.dashboard if isinstance(user.dashboard, dict) else {}
    order = [k for k in dict.fromkeys(saved.get("order") or []) if k in keys]
    order += [k for k in keys if k not in order]
    hidden = set(saved.get("hidden") or [])
    return [(k, k not in hidden) for k in order]


def visible(user):
    return [k for k, shown in layout(user) if shown]


def full_width(keys):
    """Blocs affichés sur toute la largeur : les pleins, et un demi-largeur sans voisin demi-largeur à côté
    (sinon il laisserait un trou, l'ordre choisi étant respecté)."""
    wide, i = set(), 0
    while i < len(keys):
        if WIDGETS[keys[i]].half and i + 1 < len(keys) and WIDGETS[keys[i + 1]].half:
            i += 2
            continue
        wide.add(keys[i])
        i += 1
    return wide


def save(user, order, shown):
    """Enregistre l'ordre et les blocs affichés. Clés inconnues ou interdites ignorées. Sans commit."""
    keys = available(user)
    order = [k for k in dict.fromkeys(order) if k in keys]
    order += [k for k in keys if k not in order]
    hidden = [k for k in order if k not in set(shown)]
    # La disposition par défaut n'est pas stockée : un futur bloc y prendra sa place prévue.
    user.dashboard = None if order == keys and not hidden else {"order": order, "hidden": hidden}


def move(order, key, delta):
    """Déplace `key` de `delta` places (-1 : vers le haut), sans sortir de la liste."""
    order = list(order)
    if key in order:
        i = order.index(key)
        j = min(max(i + delta, 0), len(order) - 1)
        order.insert(j, order.pop(i))
    return order


def reset(user):
    user.dashboard = None
