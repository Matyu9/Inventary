"""Connexion OIDC : enregistrement du client et rattachement de l'identité à un compte local."""
from authlib.integrations.flask_client import OAuth
from flask import current_app

from ..extensions import db
from ..models import Role, User
from ..permissions import permissions_of
from .activity import log


class OidcRefused(Exception):
    """Connexion refusée : le message est affiché tel quel à l'utilisateur."""


def init_oidc(app):
    if not enabled(app):
        return
    oauth = OAuth(app)
    app.extensions["oidc_client"] = oauth.register(
        "oidc",
        server_metadata_url=app.config["OIDC_DISCOVERY_URL"],
        client_id=app.config["OIDC_CLIENT_ID"],
        client_secret=app.config["OIDC_CLIENT_SECRET"] or None,
        client_kwargs={"scope": app.config["OIDC_SCOPES"], "code_challenge_method": "S256"},
    )


def enabled(app=None):
    config = (app or current_app).config
    return bool(config.get("OIDC_DISCOVERY_URL") and config.get("OIDC_CLIENT_ID"))


def client():
    return current_app.extensions["oidc_client"]


def role_from_groups(groups):
    """Rôle donné par les groupes OIDC, None si aucun ne correspond.

    Groupes du .env (OIDC_ADMIN_GROUPS, OIDC_PROF_GROUPS) et groupes saisis sur chaque rôle : l'administrateur
    l'emporte, puis le rôle qui a le plus de droits.
    """
    if isinstance(groups, str):
        groups = [groups]
    groups = set(groups or [])
    config = current_app.config
    candidates = set()
    if groups & set(config["OIDC_ADMIN_GROUPS"]):
        candidates.add(Role.ADMIN)
    if groups & set(config["OIDC_PROF_GROUPS"]):
        candidates.add(Role.PROF)
    candidates |= {r.key for r in _roles_with_groups() if groups & set(r.oidc_group_list)}
    if not candidates:
        return None
    if Role.ADMIN in candidates:
        return Role.ADMIN
    return max(sorted(candidates), key=lambda key: len(permissions_of(key)))


def _roles_with_groups():
    return db.session.scalars(db.select(Role).where(Role.oidc_groups.is_not(None), Role.oidc_groups != "")).all()


def resolve_user(claims):
    """Retrouve (ou crée) le compte correspondant aux claims OIDC et synchronise son rôle (sans commit)."""
    sub = claims.get("sub")
    if not sub:
        raise OidcRefused("Le fournisseur d'identité n'a pas transmis d'identifiant.")
    email = (claims.get("email") or "").strip().lower() or None
    email_trusted = bool(email) and (claims.get("email_verified") is True
                                     or not current_app.config["OIDC_REQUIRE_VERIFIED_EMAIL"])

    user = db.session.scalar(db.select(User).filter_by(oidc_sub=sub))
    if user is None and email:
        by_email = db.session.scalar(db.select(User).where(db.func.lower(User.email) == email))
        if by_email is not None:
            if not email_trusted:
                raise OidcRefused("Un compte existe déjà avec cet email, mais le fournisseur d'identité "
                                  "ne l'a pas vérifié : demandez à un responsable de lier votre compte.")
            if by_email.oidc_sub:
                raise OidcRefused("Ce compte est déjà lié à une autre identité.")
            user = by_email
            # Une fiche emprunteur sans accès (jamais connectée) devient un compte actif ; un compte
            # qui avait un accès et a été désactivé par un admin le reste.
            if not user.active and not user.password_hash:
                user.active = True
            user.oidc_sub = sub
            log("user.oidc_link", f"Compte lié à l'identité OIDC : {user.full_name}", user=user)

    if user is None:
        user = _create(claims, sub, email if email_trusted else None)

    if not user.active:
        raise OidcRefused("Ce compte est désactivé : contactez un responsable de l'association.")
    if user.must_change_password:
        # Le mot de passe provisoire (connu de l'admin) ne sert plus : la connexion passe par l'OIDC.
        user.password_hash = None
        user.must_change_password = False
    _sync_role(user, claims)
    return user


def _create(claims, sub, email):
    first = (claims.get("given_name") or "").strip()
    last = (claims.get("family_name") or "").strip()
    if not (first and last):
        parts = (claims.get("name") or claims.get("preferred_username") or "").strip().split(None, 1)
        first = first or (parts[0] if parts else "") or "Utilisateur"
        last = last or (parts[1] if len(parts) > 1 else "") or "OIDC"
    user = User(first_name=first[:80], last_name=last[:80], email=email, role=Role.EMPRUNTEUR,
                active=True, oidc_sub=sub)
    db.session.add(user)
    db.session.flush()
    log("user.create", f"Compte créé à la connexion OIDC : {user.full_name}", user=user)
    return user


def _sync_role(user, claims):
    config = current_app.config
    if not (config["OIDC_ADMIN_GROUPS"] or config["OIDC_PROF_GROUPS"] or _roles_with_groups()):
        return
    target = role_from_groups(claims.get(config["OIDC_GROUPS_CLAIM"]))
    if target is None:
        if not user.role_from_oidc:
            return  # rôle choisi par un admin : on n'y touche pas
        target = Role.EMPRUNTEUR
    user.role_from_oidc = True
    if user.role != target:
        old = user.role_label
        user.role = target
        log("user.role", f"Rôle de {user.full_name} : {old} → {user.role_label} (groupes OIDC)", user=user)
