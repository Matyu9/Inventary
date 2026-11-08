"""Liens vers les fiches (personne, objet, kit, lieu, prêt), utilisables dans tous les templates.

Chaque fonction rend un lien `<a>` si la personne connectée peut ouvrir la fiche, sinon le simple texte (échappé) :
un emprunteur voit les noms sans lien vers des pages qui lui renverraient une erreur 403.
Ce sont des globales Jinja, disponibles aussi dans les macros importées sans contexte.
"""
from flask import url_for
from flask_login import current_user
from markupsafe import Markup, escape

from .permissions import can
from .services import ip_access


def _link(href, text, css=None):
    if href is None:
        return escape(text)
    if css:
        return Markup('<a href="{0}" class="{1}">{2}</a>').format(href, css, text)
    return Markup('<a href="{0}">{1}</a>').format(href, text)


def _live(obj):
    return obj is not None and getattr(obj, "deleted_at", None) is None


def link_user(user, text=None, css=None):
    if user is None:
        return Markup("")
    text = user.full_name if text is None else text
    ok = can(current_user, "borrower.create") and ip_access.allowed("admin")
    return _link(url_for("users.detail", user_id=user.id) if ok else None, text, css)


def link_item(item, text=None, css=None):
    if item is None:
        return Markup("")
    text = item.name if text is None else text
    ok = _live(item) and can(current_user, "inventory.view")
    return _link(url_for("items.detail", item_id=item.id) if ok else None, text, css)


def link_kit(kit, text=None, css=None):
    if kit is None:
        return Markup("")
    text = kit.name if text is None else text
    ok = _live(kit) and can(current_user, "inventory.view")
    return _link(url_for("kits.detail", kit_id=kit.id) if ok else None, text, css)


def link_location(location, text=None, css=None, segments=False):
    """Lien vers le lieu (texte : son chemin complet).

    `segments=True` : chaque étape du chemin mène à son propre lieu (« Local › Armoire 2 › Étagère haute »).
    """
    if location is None:
        return Markup("")
    ok = can(current_user, "inventory.view")
    if segments and text is None:
        chain, node, seen = [], location, set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            chain.append(node)
            node = node.parent
        return Markup(" &gt; ").join(
            _link(url_for("locations.detail", location_id=n.id) if ok else None, n.name, css) for n in reversed(chain))
    text = location.path if text is None else text
    return _link(url_for("locations.detail", location_id=location.id) if ok else None, text, css)


def link_loan(loan, text=None, css=None):
    if loan is None:
        return Markup("")
    text = f"n°{loan.id}" if text is None else text
    ok = can(current_user, "loan.view", loan)
    return _link(url_for("loans.detail", loan_id=loan.id) if ok else None, text, css)


def register_links(app):
    app.jinja_env.globals.update(link_user=link_user, link_item=link_item, link_kit=link_kit,
                                 link_location=link_location, link_loan=link_loan)
