"""Connexion OIDC, avec un faux fournisseur qui signe de vrais id_token (signature, iss, aud, nonce vérifiés)."""
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import Activity, Role, User

from .conftest import login, make_user

ISSUER = "https://idp.test"
CLIENT_ID = "inventaire"
KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})
OTHER_KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})


def oidc_config(tmp_path, **overrides):
    attrs = {
        "DATA_DIR": tmp_path, "UPLOAD_DIR": tmp_path / "uploads", "BASE_URL": "https://inventaire.test",
        "OIDC_DISCOVERY_URL": f"{ISSUER}/.well-known/openid-configuration",
        "OIDC_CLIENT_ID": CLIENT_ID, "OIDC_CLIENT_SECRET": "secret", "OIDC_PROVIDER_NAME": "Authentik",
        "OIDC_ADMIN_GROUPS": [], "OIDC_PROF_GROUPS": [], "OIDC_ONLY": False,
    }
    attrs.update(overrides)
    return type("Cfg", (TestConfig,), attrs)


class FakeIdp:
    def __init__(self, client):
        self.client = client
        self.claims = {}
        self.userinfo = None
        self.token_requests = []
        self.sign_key = KEY
        client.server_metadata.update({
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "userinfo_endpoint": f"{ISSUER}/userinfo",
            "jwks": {"keys": [KEY.as_dict(private=False)]},
            "_loaded_at": time.time(),  # pas de requête réseau vers la découverte
        })

    def fetch_access_token(self, **kwargs):
        self.token_requests.append(kwargs)
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300, **self.claims}
        return {"access_token": "at", "token_type": "Bearer",
                "id_token": jwt.encode({"alg": "RS256", "kid": "k1"}, claims, self.sign_key)}

    def fetch_userinfo(self, **_kwargs):
        if isinstance(self.userinfo, Exception):
            raise self.userinfo
        return self.userinfo or {}


def make_oidc_app(tmp_path, monkeypatch, **overrides):
    app = create_app(oidc_config(tmp_path, **overrides))
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    idp = FakeIdp(app.extensions["oidc_client"])
    monkeypatch.setattr(idp.client, "fetch_access_token", idp.fetch_access_token)
    monkeypatch.setattr(idp.client, "userinfo", idp.fetch_userinfo)
    return app, ctx, idp


@pytest.fixture
def oidc_app(tmp_path, monkeypatch):
    app, ctx, idp = make_oidc_app(tmp_path, monkeypatch, OIDC_ADMIN_GROUPS=["inventaire-admins"],
                                  OIDC_PROF_GROUPS=["profs"])
    app.idp = idp
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture
def idp(oidc_app):
    return oidc_app.idp


@pytest.fixture
def oclient(oidc_app):
    return oidc_app.test_client()


def start(client, **params):
    r = client.get("/connexion/oidc" + ("?" + urlencode(params) if params else ""))
    assert r.status_code == 302, r.text
    return r, parse_qs(urlsplit(r.headers["Location"]).query)


def oidc_login(client, idp, claims, nonce=None, **params):
    _r, query = start(client, **params)
    idp.claims = {"nonce": nonce or query["nonce"][0], **claims}
    return client.get("/connexion/oidc/retour?" + urlencode({"code": "abc", "state": query["state"][0]}))


def me(client):
    return client.get("/")


ALICE = {"sub": "alice-1", "email": "alice@ohn.test", "email_verified": True,
         "given_name": "Alice", "family_name": "Martin"}


def get_by_sub(sub):
    return db.session.scalar(db.select(User).filter_by(oidc_sub=sub))


# --- Activation -------------------------------------------------------------

def test_disabled_without_configuration(client):
    assert client.get("/connexion/oidc").status_code == 404
    assert client.get("/connexion/oidc/retour?code=x&state=y").status_code == 404
    assert "Se connecter avec" not in client.get("/connexion").text


def test_login_page_shows_button(oclient):
    page = oclient.get("/connexion").text
    assert "Se connecter avec Authentik" in page and 'name="password"' in page


def test_login_page_keeps_next(oclient):
    assert 'name="next" value="/prets"' in oclient.get("/connexion?next=/prets").text


def test_partial_configuration_stays_disabled(tmp_path):
    app = create_app(oidc_config(tmp_path, OIDC_CLIENT_ID=""))
    assert "oidc_client" not in app.extensions
    assert app.test_client().get("/connexion/oidc").status_code == 404


# --- Redirection vers le fournisseur ---------------------------------------

def test_authorize_redirect(oclient, idp):
    r, query = start(oclient)
    assert r.headers["Location"].startswith(f"{ISSUER}/authorize?")
    assert query["client_id"] == [CLIENT_ID]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["https://inventaire.test/connexion/oidc/retour"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
    assert query["state"][0] and query["nonce"][0]


def test_state_and_nonce_change_each_time(oclient, idp):
    _r, q1 = start(oclient)
    _r, q2 = start(oclient)
    assert q1["state"] != q2["state"] and q1["nonce"] != q2["nonce"]


def test_token_request_sends_pkce_verifier_and_redirect_uri(oclient, idp):
    oidc_login(oclient, idp, ALICE)
    request = idp.token_requests[-1]
    assert request["code"] == "abc" and request["code_verifier"]
    assert request["redirect_uri"] == "https://inventaire.test/connexion/oidc/retour"


def test_provider_unreachable(oclient, idp, monkeypatch):
    def boom():
        raise ConnectionError("injoignable")
    monkeypatch.setattr(idp.client, "load_server_metadata", boom)
    r = oclient.get("/connexion/oidc", follow_redirects=True)
    assert "injoignable" in r.text and r.request.path == "/connexion"


def test_already_logged_in_goes_to_dashboard(oclient, idp):
    oidc_login(oclient, idp, ALICE)
    r = oclient.get("/connexion/oidc")
    assert r.status_code == 302 and r.headers["Location"] == "/"


# --- Création et liaison des comptes ---------------------------------------

def test_new_user_created_as_borrower(oclient, idp):
    r = oidc_login(oclient, idp, ALICE)
    assert r.status_code == 302 and r.headers["Location"] == "/"
    user = get_by_sub("alice-1")
    assert (user.first_name, user.last_name, user.email) == ("Alice", "Martin", "alice@ohn.test")
    assert user.role == Role.EMPRUNTEUR and user.active and not user.password_hash
    assert not user.role_from_oidc
    assert "Mes prêts en cours" in me(oclient).text
    assert db.session.scalar(db.select(Activity).filter_by(action="user.create", user_id=user.id))


def test_unverified_email_not_stored_on_new_account(oclient, idp):
    oidc_login(oclient, idp, {**ALICE, "email_verified": False})
    assert get_by_sub("alice-1").email is None


def test_unverified_email_accepted_when_check_disabled(tmp_path, monkeypatch):
    app, ctx, idp = make_oidc_app(tmp_path, monkeypatch, OIDC_REQUIRE_VERIFIED_EMAIL=False)
    try:
        existing = make_user(Role.PROF, email="alice@ohn.test")
        oidc_login(app.test_client(), idp, {**ALICE, "email_verified": None})
        assert get_by_sub("alice-1").id == existing.id
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()


@pytest.mark.parametrize("claims, expected", [
    ({"name": "Jean-Pierre De la Tour"}, ("Jean-Pierre", "De la Tour")),
    ({"name": "Cher"}, ("Cher", "OIDC")),
    ({"preferred_username": "jdupont"}, ("jdupont", "OIDC")),
    ({}, ("Utilisateur", "OIDC")),
    ({"given_name": "Léa", "name": "Léa Roux"}, ("Léa", "Roux")),
    ({"given_name": "x" * 200, "family_name": "y" * 200}, ("x" * 80, "y" * 80)),
])
def test_name_fallbacks(oclient, idp, claims, expected):
    oidc_login(oclient, idp, {"sub": "s", **claims})
    user = get_by_sub("s")
    assert (user.first_name, user.last_name) == expected


def test_existing_account_linked_by_verified_email(oclient, idp):
    prof = make_user(Role.PROF, email="alice@ohn.test")
    oidc_login(oclient, idp, {**ALICE, "email": "Alice@OHN.test"})
    db.session.refresh(prof)
    assert prof.oidc_sub == "alice-1" and prof.role == Role.PROF
    assert prof.password_hash  # le mot de passe reste utilisable
    assert db.session.scalar(db.select(db.func.count(User.id))) == 1
    assert "Tableau de bord" in me(oclient).text
    assert prof.check_password("motdepasse")


def test_existing_account_not_linked_with_unverified_email(oclient, idp):
    admin = make_user(Role.ADMIN, email="alice@ohn.test")
    r = oidc_login(oclient, idp, {**ALICE, "email_verified": False})
    assert r.headers["Location"] == "/connexion"
    db.session.refresh(admin)
    assert admin.oidc_sub is None
    assert me(oclient).status_code == 302
    assert "pas vérifié" in oclient.get("/connexion").text
    assert db.session.scalar(db.select(db.func.count(User.id))) == 1


def test_email_already_linked_to_other_identity(oclient, idp):
    user = make_user(Role.PROF, email="alice@ohn.test")
    user.oidc_sub = "quelqu-un-d-autre"
    db.session.commit()
    oidc_login(oclient, idp, ALICE)
    assert me(oclient).status_code == 302
    assert "déjà lié" in oclient.get("/connexion").text
    assert get_by_sub("alice-1") is None


def test_same_sub_reuses_account_even_if_email_changes(oclient, idp):
    oidc_login(oclient, idp, ALICE)
    oclient.post("/deconnexion")
    oidc_login(oclient, idp, {**ALICE, "email": "nouvelle@ohn.test"})
    assert db.session.scalar(db.select(db.func.count(User.id))) == 1
    assert me(oclient).status_code == 200


def test_borrower_record_without_access_is_activated(oclient, idp):
    fiche = make_user(Role.EMPRUNTEUR, email="alice@ohn.test", active=False)
    fiche.password_hash = None  # fiche créée par un prof, jamais connectée
    db.session.commit()
    oidc_login(oclient, idp, ALICE)
    db.session.refresh(fiche)
    assert fiche.active and fiche.oidc_sub == "alice-1"
    assert me(oclient).status_code == 200


def test_deactivated_account_stays_deactivated(oclient, idp):
    user = make_user(Role.PROF, email="alice@ohn.test", active=False)  # avait un mot de passe
    oidc_login(oclient, idp, ALICE)
    db.session.refresh(user)
    assert not user.active and user.oidc_sub is None
    assert me(oclient).status_code == 302
    assert "désactivé" in oclient.get("/connexion").text


def test_deactivated_linked_account_refused(oclient, idp):
    oidc_login(oclient, idp, ALICE)
    oclient.post("/deconnexion")
    user = get_by_sub("alice-1")
    user.active = False
    db.session.commit()
    oidc_login(oclient, idp, ALICE)
    assert me(oclient).status_code == 302


def test_deactivated_while_logged_in_loses_session(oclient, idp):
    oidc_login(oclient, idp, ALICE)
    get_by_sub("alice-1").active = False
    db.session.commit()
    assert me(oclient).status_code == 302


def test_missing_sub_refused(oclient, idp, monkeypatch):
    monkeypatch.setattr(idp.client, "authorize_access_token", lambda: {"userinfo": {"email": "x@ohn.test"}})
    r = oclient.get("/connexion/oidc/retour?code=abc&state=s", follow_redirects=True)
    assert "identifiant" in r.text and r.request.path == "/connexion"
    assert db.session.scalar(db.select(db.func.count(User.id))) == 0


def test_temporary_password_dropped_after_oidc_login(oclient, idp):
    user = make_user(Role.PROF, email="alice@ohn.test")
    user.must_change_password = True
    db.session.commit()
    oidc_login(oclient, idp, ALICE)
    db.session.refresh(user)
    assert not user.must_change_password and user.password_hash is None
    assert me(oclient).status_code == 200  # pas renvoyé vers /mot-de-passe
    assert not user.check_password("motdepasse")  # le provisoire ne fonctionne plus


# --- Rôles d'après les groupes ---------------------------------------------

@pytest.mark.parametrize("groups, role", [
    (["inventaire-admins"], Role.ADMIN),
    (["profs"], Role.PROF),
    (["profs", "inventaire-admins"], Role.ADMIN),
    ("profs", Role.PROF),
    (["autre"], Role.EMPRUNTEUR),
    ([], Role.EMPRUNTEUR),
    (None, Role.EMPRUNTEUR),
])
def test_role_from_groups_on_creation(oclient, idp, groups, role):
    oidc_login(oclient, idp, {**ALICE, "groups": groups})
    assert get_by_sub("alice-1").role == role


def test_role_follows_groups_at_each_login(oclient, idp):
    oidc_login(oclient, idp, {**ALICE, "groups": ["profs"]})
    user = get_by_sub("alice-1")
    assert user.role == Role.PROF and user.role_from_oidc
    for groups, role in ((["inventaire-admins"], Role.ADMIN), (["profs"], Role.PROF), ([], Role.EMPRUNTEUR)):
        oclient.post("/deconnexion")
        oidc_login(oclient, idp, {**ALICE, "groups": groups})
        db.session.refresh(user)
        assert user.role == role
    # 4 changements : Emprunteur → Prof à la création, puis les 3 connexions suivantes.
    assert db.session.scalar(db.select(db.func.count(Activity.id)).filter_by(action="user.role")) == 4


def test_manual_role_kept_without_matching_group(oclient, idp):
    admin = make_user(Role.ADMIN, email="alice@ohn.test")
    oidc_login(oclient, idp, {**ALICE, "groups": ["autre"]})
    db.session.refresh(admin)
    assert admin.role == Role.ADMIN and not admin.role_from_oidc


def test_matching_group_overrides_manual_role(oclient, idp):
    user = make_user(Role.EMPRUNTEUR, email="alice@ohn.test")
    oidc_login(oclient, idp, {**ALICE, "groups": ["profs"]})
    db.session.refresh(user)
    assert user.role == Role.PROF and user.role_from_oidc


def test_admin_edit_takes_role_back_from_groups(oclient, idp):
    oidc_login(oclient, idp, {**ALICE, "groups": ["profs"]})
    user = get_by_sub("alice-1")
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    oclient.post("/deconnexion")
    login(oclient, admin)
    r = oclient.post(f"/utilisateurs/{user.id}/modifier", data={
        "first_name": "Alice", "last_name": "Martin", "email": "alice@ohn.test", "member_status": "",
        "role": Role.ADMIN, "active": "y"})
    assert r.status_code == 302
    db.session.refresh(user)
    assert user.role == Role.ADMIN and not user.role_from_oidc
    oclient.post("/deconnexion")
    oidc_login(oclient, idp, {**ALICE, "groups": []})
    db.session.refresh(user)
    assert user.role == Role.ADMIN


def test_admin_edit_without_role_change_keeps_group_sync(oclient, idp):
    oidc_login(oclient, idp, {**ALICE, "groups": ["profs"]})
    user = get_by_sub("alice-1")
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    oclient.post("/deconnexion")
    login(oclient, admin)
    oclient.post(f"/utilisateurs/{user.id}/modifier", data={
        "first_name": "Alice", "last_name": "Martin", "email": "alice@ohn.test", "phone": "0600000000",
        "member_status": "", "role": Role.PROF, "active": "y"})
    db.session.refresh(user)
    assert user.role_from_oidc


def test_no_group_mapping_never_touches_roles(tmp_path, monkeypatch):
    app, ctx, idp = make_oidc_app(tmp_path, monkeypatch)
    try:
        prof = make_user(Role.PROF, email="alice@ohn.test")
        oidc_login(app.test_client(), idp, {**ALICE, "groups": ["inventaire-admins"]})
        db.session.refresh(prof)
        assert prof.role == Role.PROF and not prof.role_from_oidc
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def test_custom_groups_claim(tmp_path, monkeypatch):
    app, ctx, idp = make_oidc_app(tmp_path, monkeypatch, OIDC_GROUPS_CLAIM="roles", OIDC_PROF_GROUPS=["prof"])
    try:
        oidc_login(app.test_client(), idp, {**ALICE, "roles": ["prof"], "groups": ["autre"]})
        assert get_by_sub("alice-1").role == Role.PROF
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()


# --- /userinfo ---------------------------------------------------------------

def test_groups_and_email_from_userinfo(oclient, idp):
    idp.userinfo = {"sub": "bob", "email": "bob@ohn.test", "email_verified": True, "groups": ["profs"]}
    oidc_login(oclient, idp, {"sub": "bob", "name": "Bob Leroy"})
    user = get_by_sub("bob")
    assert user.email == "bob@ohn.test" and user.role == Role.PROF


def test_userinfo_for_other_subject_ignored(oclient, idp):
    idp.userinfo = {"sub": "mallory", "groups": ["inventaire-admins"]}
    oidc_login(oclient, idp, {"sub": "bob", "name": "Bob Leroy"})
    assert get_by_sub("bob").role == Role.EMPRUNTEUR


def test_id_token_claims_win_over_userinfo(oclient, idp):
    idp.userinfo = {"sub": "alice-1", "email": "autre@ohn.test", "groups": ["profs"]}
    oidc_login(oclient, idp, {k: v for k, v in ALICE.items()})
    assert get_by_sub("alice-1").email == "alice@ohn.test"


def test_userinfo_failure_does_not_block_login(oclient, idp):
    idp.userinfo = ConnectionError("timeout")
    oidc_login(oclient, idp, ALICE)
    assert me(oclient).status_code == 200


def test_userinfo_skipped_when_id_token_complete(oclient, idp):
    idp.userinfo = AssertionError("ne devrait pas être appelé")
    oidc_login(oclient, idp, {**ALICE, "groups": []})
    assert me(oclient).status_code == 200


# --- Attaques et erreurs -----------------------------------------------------

def refused(client):
    assert me(client).status_code == 302
    assert db.session.scalar(db.select(db.func.count(User.id))) == 0


def test_wrong_nonce_refused(oclient, idp):
    r = oidc_login(oclient, idp, ALICE, nonce="rejoue")
    assert r.headers["Location"] == "/connexion"
    refused(oclient)


def test_forged_signature_refused(oclient, idp):
    idp.sign_key = OTHER_KEY
    oidc_login(oclient, idp, ALICE)
    refused(oclient)


@pytest.mark.parametrize("override", [
    {"aud": "autre-appli"},
    {"iss": "https://pirate.test"},
    {"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200},
])
def test_invalid_id_token_claims_refused(oclient, idp, override):
    oidc_login(oclient, idp, {**ALICE, **override})
    refused(oclient)


def test_callback_without_started_login_refused(oclient, idp):
    idp.claims = {**ALICE, "nonce": "n"}
    r = oclient.get("/connexion/oidc/retour?code=abc&state=invente", follow_redirects=True)
    assert "a échoué" in r.text
    refused(oclient)


def test_state_cannot_be_replayed(oclient, idp):
    _r, query = start(oclient)
    idp.claims = {**ALICE, "nonce": query["nonce"][0]}
    url = "/connexion/oidc/retour?" + urlencode({"code": "abc", "state": query["state"][0]})
    oclient.get(url)
    oclient.post("/deconnexion")
    oclient.get(url)
    assert me(oclient).status_code == 302


def test_state_bound_to_browser_session(oidc_app, idp):
    victim, attacker = oidc_app.test_client(), oidc_app.test_client()
    _r, query = start(attacker)
    idp.claims = {**ALICE, "nonce": query["nonce"][0]}
    victim.get("/connexion/oidc/retour?" + urlencode({"code": "abc", "state": query["state"][0]}))
    assert me(victim).status_code == 302


def test_user_cancels_at_provider(oclient, idp):
    start(oclient)
    r = oclient.get("/connexion/oidc/retour?error=access_denied&error_description=refus", follow_redirects=True)
    assert "annulée" in r.text and r.request.path == "/connexion"


def test_unexpected_error_at_token_endpoint(oclient, idp, monkeypatch):
    def boom(**_kwargs):
        raise ConnectionError("timeout")
    monkeypatch.setattr(idp.client, "fetch_access_token", boom)
    r = oidc_login(oclient, idp, ALICE)
    assert r.headers["Location"] == "/connexion"
    refused(oclient)


def test_callback_not_cached(oclient, idp):
    assert oidc_login(oclient, idp, ALICE).headers["Cache-Control"] == "no-store"


# --- Redirection après connexion, « rester connecté » ------------------------

def test_next_preserved(oclient, idp):
    assert oidc_login(oclient, idp, ALICE, next="/notifications").headers["Location"] == "/notifications"


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "/\\evil.example"])
def test_open_redirect_blocked(oclient, idp, target):
    assert oidc_login(oclient, idp, ALICE, next=target).headers["Location"] == "/"


def test_remember_me(oclient, idp):
    r = oidc_login(oclient, idp, ALICE, remember="1")
    assert "remember_token=" in " ".join(r.headers.getlist("Set-Cookie"))


def test_no_remember_by_default(oclient, idp):
    r = oidc_login(oclient, idp, ALICE)
    assert "remember_token=" not in " ".join(r.headers.getlist("Set-Cookie"))


# --- Mot de passe et mode OIDC seul -----------------------------------------

def test_oidc_only_account_has_no_password_page(oclient, idp):
    oidc_login(oclient, idp, ALICE)
    assert 'href="/mot-de-passe"' not in me(oclient).text
    r = oclient.get("/mot-de-passe", follow_redirects=True)
    assert "pas de mot de passe" in r.text


def test_linked_account_with_password_keeps_password_page(oclient, idp):
    make_user(Role.PROF, email="alice@ohn.test")
    oidc_login(oclient, idp, ALICE)
    assert 'href="/mot-de-passe"' in me(oclient).text


def test_password_login_still_works_alongside(oclient, idp):
    user = make_user(Role.PROF, email="prof@ohn.test")
    assert login(oclient, user).headers["Location"] == "/"


def test_oidc_only_mode(tmp_path, monkeypatch):
    app, ctx, idp = make_oidc_app(tmp_path, monkeypatch, OIDC_ONLY=True)
    try:
        client = app.test_client()
        page = client.get("/connexion").text
        assert "Se connecter avec Authentik" in page and 'name="password"' not in page
        user = make_user(Role.ADMIN, email="admin@ohn.test")
        assert login(client, user).status_code == 404
        assert me(client).status_code == 302
        oidc_login(client, idp, ALICE)
        assert me(client).status_code == 200
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def test_oidc_only_ignored_when_oidc_not_configured(tmp_path):
    app = create_app(oidc_config(tmp_path, OIDC_ONLY=True, OIDC_DISCOVERY_URL=""))
    with app.app_context():
        db.create_all()
        assert 'name="password"' in app.test_client().get("/connexion").text
        user = make_user(Role.ADMIN, email="admin@ohn.test")
        assert login(app.test_client(), user).status_code == 302
        db.session.remove()
        db.drop_all()


# --- Groupes saisis sur les rôles ------------------------------------------------

def add_role(key, name, permissions, oidc_groups):
    db.session.add(Role(key=key, name=name, permissions=permissions, oidc_groups=oidc_groups))
    db.session.commit()


def test_custom_role_from_its_groups(oclient, idp):
    add_role("regie", "Régie", ["inventory.view"], "regie, technique")
    oidc_login(oclient, idp, {**ALICE, "groups": ["technique"]})
    user = get_by_sub("alice-1")
    assert user.role == "regie" and user.role_from_oidc


def test_role_with_most_rights_wins(oclient, idp):
    add_role("regie", "Régie", ["inventory.view", "item.edit", "scan"], "regie")
    add_role("lecture", "Lecture", ["inventory.view"], "lecture")
    oidc_login(oclient, idp, {**ALICE, "groups": ["lecture", "regie"]})
    assert get_by_sub("alice-1").role == "regie"


def test_custom_role_loses_to_prof_with_more_rights(oclient, idp):
    add_role("lecture", "Lecture", ["inventory.view"], "lecture")
    oidc_login(oclient, idp, {**ALICE, "groups": ["lecture", "profs"]})
    assert get_by_sub("alice-1").role == Role.PROF


def test_admin_always_wins(oclient, idp):
    add_role("regie", "Régie", ["inventory.view"], "regie")
    oidc_login(oclient, idp, {**ALICE, "groups": ["regie", "inventaire-admins"]})
    assert get_by_sub("alice-1").role == Role.ADMIN


def test_admin_role_group_saved_on_role(oclient, idp):
    add_role(Role.ADMIN, "Administrateur", [], "bureau")
    oidc_login(oclient, idp, {**ALICE, "groups": ["bureau"]})
    assert get_by_sub("alice-1").role == Role.ADMIN


def test_role_groups_alone_enable_sync(tmp_path, monkeypatch):
    app, ctx, idp = make_oidc_app(tmp_path, monkeypatch)  # aucun groupe dans le .env
    try:
        add_role("regie", "Régie", ["inventory.view"], "regie")
        client = app.test_client()
        oidc_login(client, idp, {**ALICE, "groups": ["regie"]})
        user = get_by_sub("alice-1")
        assert user.role == "regie"
        client.post("/deconnexion")
        oidc_login(client, idp, {**ALICE, "groups": []})
        db.session.refresh(user)
        assert user.role == Role.EMPRUNTEUR
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()
