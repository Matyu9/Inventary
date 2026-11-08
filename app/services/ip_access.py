"""Zones réservées à certaines adresses IP (réglées dans Paramètres → Accès par adresse IP).

Une zone sans liste reste ouverte à tous. Depuis une adresse non autorisée, un visiteur anonyme reçoit une 404 (la zone
ne se dévoile pas) et une personne connectée une page « Accès refusé » qui explique pourquoi.
Les listes sont stockées dans AppSetting (une clé par zone), une adresse ou une plage CIDR par ligne.
En cas de verrouillage : `flask acces-ip --effacer` sur le serveur.
"""
import ipaddress
import re

from flask import abort, current_app, g, request
from flask_login import current_user

from ..extensions import db
from ..models import AppSetting
from . import branding

# clé : (libellé, explication affichée sous le champ)
ZONES = {
    "admin": ("Administration",
              "Personnes, rôles, champs personnalisés, paramètres, journal et état de l'installation."),
    "rescue": ("Connexion de secours",
               "Connexion par mot de passe d'un administrateur (/connexion?local=1) quand l'OIDC est seul proposé."),
    "backchannel": ("Déconnexion envoyée par Adhérents",
                    "Avis de déconnexion du fournisseur OIDC : indiquez l'adresse du serveur Adhérents."),
}
KEY_PREFIX = "ip_allow."
MAX_ENTRIES = 50

# Endpoints de la zone « Administration » : blueprints entiers, sauf les pages utiles à tous qui y sont rangées.
ADMIN_BLUEPRINTS = {"users", "roles", "settings", "journal", "health"}
ADMIN_ENDPOINTS = {"referentiel.custom_fields", "referentiel.create_custom_field",
                   "referentiel.edit_custom_field", "referentiel.delete_custom_field"}
NOT_ADMIN = {"users.notifications", "users.avatar_file", "settings.logo_file"}


class InvalidEntry(ValueError):
    pass


def parse(text):
    """Texte saisi (lignes, virgules ou espaces ; « # » commente la fin d'une ligne) → liste de réseaux.

    Lève InvalidEntry sur la première valeur qui n'est ni une adresse ni une plage CIDR.
    """
    networks = []
    for line in (text or "").splitlines():
        for entry in re.split(r"[,\s]+", line.split("#", 1)[0]):
            if not entry:
                continue
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise InvalidEntry(f"« {entry[:60]} » n'est ni une adresse IP ni une plage (ex. 192.168.1.0/24).")
            if network not in networks:
                networks.append(network)
    if len(networks) > MAX_ENTRIES:
        raise InvalidEntry(f"{MAX_ENTRIES} adresses ou plages au maximum.")
    return networks


def normalize(text):
    """Forme enregistrée : une entrée par ligne, commentaires retirés. Lève InvalidEntry."""
    return "\n".join(str(n) if n.num_addresses > 1 else str(n.network_address) for n in parse(text))


def _address(ip):
    try:
        address = ipaddress.ip_address((ip or "").split("%", 1)[0])
    except ValueError:
        return None
    # « ::ffff:192.0.2.1 » (IPv4 vue par un serveur IPv6) se compare aux plages IPv4.
    return getattr(address, "ipv4_mapped", None) or address


def matches(networks, ip):
    address = _address(ip)
    return address is not None and any(address.version == n.version and address in n for n in networks)


def lists():
    """{zone: texte enregistré}, lu une fois par requête. Base illisible : aucune restriction (comme le branding)."""
    if "ip_access" not in g:
        rows = branding.settings_values()
        g.ip_access = {zone: rows.get(KEY_PREFIX + zone, "") for zone in ZONES}
    return g.ip_access


def allowed(zone, ip=None):
    text = lists()[zone]
    if not text:
        return True
    try:
        networks = parse(text)
    except InvalidEntry:  # valeur abîmée en base : on reste fermé plutôt qu'ouvert
        current_app.logger.error("Liste d'adresses IP illisible pour la zone %s", zone)
        return False
    return matches(networks, request.remote_addr if ip is None else ip)


def save(zone, text):
    """Enregistre la liste d'une zone (vide = zone ouverte). Lève InvalidEntry. Sans commit."""
    if zone not in ZONES:
        raise KeyError(zone)
    value = normalize(text)
    key = KEY_PREFIX + zone
    setting = db.session.get(AppSetting, key)
    if not value:
        if setting:
            db.session.delete(setting)
    elif setting:
        setting.value = value
    else:
        db.session.add(AppSetting(key=key, value=value))
    branding.forget()
    return value


def zone_of(endpoint):
    """Zone protégée par endpoint (la connexion de secours et l'avis Adhérents sont vérifiés dans leurs vues)."""
    if not endpoint or endpoint in NOT_ADMIN:
        return None
    if endpoint in ADMIN_ENDPOINTS or endpoint.split(".", 1)[0] in ADMIN_BLUEPRINTS:
        return "admin"
    return None


def refuse(zone):
    """Refuse l'accès à la zone. Visiteur anonyme (robot, curieux) : 404, la zone ne se dévoile pas.
    Personne connectée : page « Accès refusé » qui explique pourquoi, et tentative notée au journal."""
    from ..errors import _page  # import local : errors importe la base, chargée après ce module
    from .activity import log

    label = ZONES[zone][0]
    current_app.logger.warning("Accès à la zone « %s » refusé depuis %s (%s)", label, request.remote_addr, request.path)
    if not (current_user and current_user.is_authenticated):
        abort(404)
    log("security.ip_denied", f"Accès à la zone « {label} » refusé depuis {request.remote_addr} "
                              f"({request.path[:200]})")
    db.session.commit()
    abort(current_app.make_response(_page(
        403, "key", "Accès refusé depuis ce réseau",
        f"La zone « {label} » n'est accessible que depuis certaines adresses IP, et votre adresse actuelle "
        f"({request.remote_addr}) n'en fait pas partie. Reconnectez-vous depuis un réseau autorisé "
        "(le local de l'association, par exemple) ou adressez-vous à un administrateur.")))
