"""Préférences d'affichage de chacun (Mon compte → Affichage) : page d'accueil et taille du texte."""
from flask import url_for

from ..permissions import can

# clé : (libellé, route, droit nécessaire). Le tableau de bord, ouvert à tous, est la page par défaut.
HOME_PAGES = {
    "dashboard": ("Tableau de bord", "dashboard.index", None),
    "loans": ("Prêts", "loans.index", "loan.list_all"),
    "mine": ("Mes prêts", "loans.mine", "myloans.view"),
    "scan": ("Scanner", "scan.index", "scan"),
    "items": ("Matériel", "items.index", "inventory.view"),
    "calendar": ("Calendrier", "calendar.index", "calendar.view"),
}
DEFAULT_HOME = "dashboard"

# clé : libellé. La taille correspondante est dans css/app.css (data-text sur <html>).
TEXT_SIZES = {"normal": "Normale", "large": "Grande", "xlarge": "Très grande"}
DEFAULT_TEXT_SIZE = "normal"


def home_choices(user):
    """[(clé, libellé)] des pages d'accueil auxquelles la personne a droit."""
    return [(key, label) for key, (label, _, permission) in HOME_PAGES.items()
            if permission is None or can(user, permission)]


def home_url(user):
    """Page où arriver après la connexion. Un choix devenu interdit (droit retiré) ramène au tableau de bord."""
    key = user.home if user.home in dict(home_choices(user)) else DEFAULT_HOME
    return url_for(HOME_PAGES[key][1])
