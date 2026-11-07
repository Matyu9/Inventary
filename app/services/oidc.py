"""Connexion OIDC : enregistrement du client et rattachement de l'identité à un compte local.

Aussi la déconnexion : avis du fournisseur (OpenID Connect Back-Channel Logout 1.0) et déconnexion transmise au
fournisseur (RP-Initiated Logout 1.0).
"""
import time

from authlib.common.urls import add_params_to_uri
from authlib.integrations.flask_client import OAuth
from flask import current_app, url_for
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry

from ..extensions import db, fold
from ..models import LoginSession, Role, User
from ..permissions import permissions_of
from .activity import log

LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
# Un avis plus ancien (ou daté du futur) est refusé : il a pu être intercepté puis rejoué.
LOGOUT_MAX_AGE = 5 * 60
# Signatures acceptées : jamais « none » ni une signature symétrique, qu'un client pourrait fabriquer.
ASYMMETRIC = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]
# Une clé inconnue fait recharger la liste des clés, mais pas plus d'une fois par minute.
JWKS_REFRESH_EVERY = 60
_last_jwks_refresh = [0.0]


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
            if not current_app.config["OIDC_LINK_BY_EMAIL"]:
                raise OidcRefused("Votre compte n'est pas encore rattaché à votre fiche de l'Inventaire : "
                                  "demandez à un responsable de l'association de le faire.")
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


# ---------------------------------------------------------------------------
# Déconnexion
# ---------------------------------------------------------------------------

class InvalidLogoutToken(Exception):
    """Avis de déconnexion refusé : le motif part dans les journaux, pas au fournisseur."""


def _key_set(force=False):
    return KeySet.import_key_set(client().fetch_jwk_set(force=force))


def _decode(raw, keys):
    return jwt.decode(raw, keys, registry=JWSRegistry(algorithms=ASYMMETRIC, strict_check_header=False))


def verify_logout_token(raw):
    """Contenu d'un avis de déconnexion vérifié comme le demande la norme (section 2.6), sinon InvalidLogoutToken."""
    if not raw or not isinstance(raw, str) or len(raw) > 16384:
        raise InvalidLogoutToken("logout_token absent")
    try:
        try:
            token = _decode(raw, _key_set())
        except InvalidKeyIdError:  # clé renouvelée chez le fournisseur : recharger, sans le laisser imposer le rythme
            if time.monotonic() - _last_jwks_refresh[0] < JWKS_REFRESH_EVERY:
                raise
            _last_jwks_refresh[0] = time.monotonic()
            token = _decode(raw, _key_set(force=True))
    except (JoseError, ValueError, TypeError) as error:
        raise InvalidLogoutToken(f"signature invalide ({error.__class__.__name__})") from error
    header, claims = token.header, token.claims
    # « JWT » reste accepté : beaucoup de fournisseurs le mettent par défaut. Ce qui distingue un avis d'un jeton
    # d'identité, c'est `events` sans `nonce`, vérifiés plus bas.
    typ = str(header.get("typ") or "").lower()
    if typ and typ not in ("jwt", "logout+jwt", "application/logout+jwt"):
        raise InvalidLogoutToken(f"typ inattendu : {typ}")
    metadata = client().load_server_metadata()
    if claims.get("iss") != metadata.get("issuer"):
        raise InvalidLogoutToken("iss inattendu")
    audience = claims.get("aud")
    if not (audience == current_app.config["OIDC_CLIENT_ID"]
            or isinstance(audience, list) and current_app.config["OIDC_CLIENT_ID"] in audience):
        raise InvalidLogoutToken("aud inattendu")
    now = time.time()
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or abs(now - iat) > LOGOUT_MAX_AGE:
        raise InvalidLogoutToken("iat absent ou trop éloigné")
    exp = claims.get("exp")
    if exp is not None and (not isinstance(exp, (int, float)) or exp < now):
        raise InvalidLogoutToken("avis expiré")
    events = claims.get("events")
    if not isinstance(events, dict) or not isinstance(events.get(LOGOUT_EVENT), dict):
        raise InvalidLogoutToken("événement de déconnexion absent")
    if "nonce" in claims:
        raise InvalidLogoutToken("nonce interdit")
    sub, sid = claims.get("sub"), claims.get("sid")
    if not (isinstance(sub, str) and sub or isinstance(sid, str) and sid):
        raise InvalidLogoutToken("ni sub ni sid")
    if (sub is not None and not isinstance(sub, str)) or (sid is not None and not isinstance(sid, str)):
        raise InvalidLogoutToken("sub ou sid illisible")
    return claims


def end_sessions(claims):
    """Ferme les sessions que désigne un avis vérifié. Renvoie leur nombre. Sans commit.

    Avec `sid`, seulement cette session-là (et seulement si elle appartient à `sub`, quand il est donné). Sans `sid`,
    toutes les sessions ouvertes via le fournisseur par ce compte : celles ouvertes par mot de passe ne le concernent pas.
    """
    sub, sid = claims.get("sub"), claims.get("sid")
    user = db.session.scalar(db.select(User).filter_by(oidc_sub=sub)) if sub else None
    if sub and user is None:
        return 0
    query = db.select(LoginSession)
    if sid:
        query = query.where(LoginSession.oidc_sid == sid)
        if user is not None:
            query = query.where(LoginSession.user_id == user.id)
    else:
        query = query.where(LoginSession.user_id == user.id, LoginSession.oidc_id_token.is_not(None))
    closed = db.session.scalars(query).all()
    for login in closed:
        log("auth.logout", f"Déconnexion de {login.user.full_name} par le fournisseur d'identité", user=login.user)
        db.session.delete(login)
    return len(closed)


def logout_url(login):
    """Adresse où envoyer la personne pour qu'elle soit aussi déconnectée du fournisseur, sinon None.

    Seulement pour une session ouverte via le fournisseur, et s'il sait le faire (`end_session_endpoint`). Le retour
    se fait sur la page de connexion, qu'il faut déclarer chez lui comme adresse après déconnexion.
    """
    config = current_app.config
    if login is None or not login.oidc_id_token or not enabled() or not config["OIDC_PROVIDER_LOGOUT"]:
        return None
    try:
        endpoint = client().load_server_metadata().get("end_session_endpoint")
    except Exception:  # noqa: BLE001  fournisseur injoignable : la déconnexion locale suffit
        current_app.logger.warning("Découverte OIDC injoignable : déconnexion locale seulement", exc_info=True)
        return None
    if not endpoint:
        return None
    return add_params_to_uri(endpoint, [("id_token_hint", login.oidc_id_token), ("client_id", config["OIDC_CLIENT_ID"]),
                                        ("post_logout_redirect_uri", config["BASE_URL"] + url_for("auth.login"))])


# ---------------------------------------------------------------------------
# Rattachement des comptes avant la bascule (export d'Adhérents)
# ---------------------------------------------------------------------------

LINKED, PROPOSED, DOUBTFUL, TAKEN, NEW = "rattaché", "proposé", "à vérifier", "déjà pris", "nouveau"


class InvalidExport(Exception):
    """Fichier d'export illisible : le message est affiché tel quel."""


def parse_export(data):
    """Liste de comptes exportée par `flask oidc-export-accounts` côté Adhérents, vérifiée champ par champ."""
    import json
    try:
        rows = json.loads(data)
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidExport("Ce fichier n'est pas un export JSON d'Adhérents.") from error
    if isinstance(rows, dict):
        rows = rows.get("accounts")
    if not isinstance(rows, list):
        raise InvalidExport("Ce fichier ne contient pas de liste de comptes.")
    accounts, seen = [], set()
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("sub"), str) or not row["sub"].strip():
            raise InvalidExport(f"Compte n° {number} : identifiant (sub) manquant.")
        sub = row["sub"].strip()
        if len(sub) > 255:
            raise InvalidExport(f"Compte n° {number} : identifiant trop long.")
        if sub in seen:
            raise InvalidExport(f"Compte n° {number} : identifiant en double.")
        seen.add(sub)
        texts = {key: row.get(key) if isinstance(row.get(key), str) else "" for key in
                 ("email", "first_name", "last_name")}
        accounts.append({"sub": sub, "email": texts["email"].strip().lower()[:255],
                         "first_name": texts["first_name"].strip()[:80], "last_name": texts["last_name"].strip()[:80],
                         "email_verified": row.get("email_verified") is True})
    return accounts


def _same_name(account, user):
    return (fold(account["first_name"]).strip(), fold(account["last_name"]).strip()) == \
        (fold(user.first_name).strip(), fold(user.last_name).strip())


def link_proposals(accounts):
    """Pour chaque compte d'Adhérents : la fiche de l'Inventaire qui lui correspond, et à quel point c'est sûr.

    L'email seul ne suffit pas : la fiche d'un enfant porte souvent l'adresse de son parent. Seuls l'email (vérifié)
    et le nom ensemble donnent une proposition cochée d'office ; tout le reste attend la décision d'un responsable.
    """
    by_sub = {u.oidc_sub: u for u in db.session.scalars(db.select(User).where(User.oidc_sub.is_not(None)))}
    users = db.session.scalars(db.select(User).where(User.archived_at.is_(None))).all()
    by_email = {}
    for user in users:
        if user.email:
            by_email.setdefault(user.email.lower(), user)
    proposals = []
    for account in accounts:
        if account["sub"] in by_sub:
            proposals.append({**account, "status": LINKED, "user": by_sub[account["sub"]], "checked": False,
                              "reason": "déjà rattaché à cette fiche"})
            continue
        user = by_email.get(account["email"]) if account["email"] else None
        reason = None
        if user is None:
            namesakes = [u for u in users if not u.oidc_sub and _same_name(account, u)]
            if len(namesakes) == 1:
                user, reason = namesakes[0], "même nom, adresse différente"
        if user is None:
            proposals.append({**account, "status": NEW, "user": None, "checked": False,
                              "reason": "aucune fiche : elle sera créée à la première connexion"})
        elif user.oidc_sub:
            proposals.append({**account, "status": TAKEN, "user": user, "checked": False,
                              "reason": "cette fiche est déjà rattachée à une autre identité"})
        elif reason:
            proposals.append({**account, "status": DOUBTFUL, "user": user, "checked": False, "reason": reason})
        elif not _same_name(account, user):
            proposals.append({**account, "status": DOUBTFUL, "user": user, "checked": False,
                              "reason": "même adresse, nom différent : adresse partagée dans une famille ?"})
        elif not account["email_verified"]:
            proposals.append({**account, "status": DOUBTFUL, "user": user, "checked": False,
                              "reason": "même adresse et même nom, mais adresse pas encore vérifiée dans Adhérents"})
        else:
            proposals.append({**account, "status": PROPOSED, "user": user, "checked": True,
                              "reason": "même adresse vérifiée et même nom"})
    for proposal in proposals:
        user = proposal["user"]
        if proposal["status"] in (PROPOSED, DOUBTFUL) and not user.active:
            proposal["reason"] += (" ; fiche sans accès, elle sera ouverte" if not user.password_hash
                                   else " ; compte désactivé, il le restera")
    # Une fiche proposée à deux comptes : aucun des deux n'est coché.
    counts = {}
    for proposal in proposals:
        if proposal["status"] in (PROPOSED, DOUBTFUL):
            counts[proposal["user"].id] = counts.get(proposal["user"].id, 0) + 1
    for proposal in proposals:
        if proposal["status"] in (PROPOSED, DOUBTFUL) and counts[proposal["user"].id] > 1:
            proposal.update(status=DOUBTFUL, checked=False, reason="fiche proposée à plusieurs comptes")
    return proposals


def link(user, sub, by):
    """Rattache `user` à l'identité `sub`. Renvoie le motif d'un refus, sinon None. Sans commit."""
    if user is None or user.archived_at:
        return "fiche introuvable"
    if user.oidc_sub:
        return f"{user.full_name} est déjà rattaché à une identité"
    if db.session.scalar(db.select(User.id).filter_by(oidc_sub=sub)):
        return "cette identité est déjà rattachée à une autre fiche"
    user.oidc_sub = sub
    if not user.active and not user.password_hash:
        # Comme au rattachement par email : une fiche emprunteur sans accès devient un compte. Un compte qui avait un
        # mot de passe et que l'on a désactivé, lui, le reste.
        user.active = True
    log("user.oidc_link", f"Compte de {user.full_name} rattaché à son identité Adhérents par {by.full_name}",
        user=by, target=user)
    return None
