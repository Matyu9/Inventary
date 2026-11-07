"""Déconnexion avec le fournisseur OIDC (Adhérents) : avis reçus, déconnexion transmise, rattachement des comptes.

Le faux fournisseur de test_oidc.py signe de vrais jetons : chaque avis de déconnexion est vérifié pour de bon.
"""
import base64
import html
import json
import re
import time
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from joserfc import jws, jwt
from joserfc.jwk import RSAKey

from app.extensions import db
from app.models import Activity, LoginSession, Role, User
from app.services import oidc as oidc_service

from .conftest import login, make_user
from .test_oidc import (ALICE, CLIENT_ID, ISSUER, KEY, OTHER_KEY, get_by_sub, idp, make_oidc_app, me,  # noqa: F401
                        oclient, oidc_app, oidc_login)

EVENT = "http://schemas.openid.net/event/backchannel-logout"


@pytest.fixture(autouse=True)
def fresh_requests(monkeypatch):
    """Ces tests gardent un contexte d'application ouvert : Flask y réutilise `g` d'une requête à l'autre, et
    Flask-Login la personne déjà chargée. Sans cette remise à zéro, une session fermée paraîtrait encore ouverte."""
    from flask import g, has_app_context
    from flask.testing import FlaskClient
    original = FlaskClient.open

    def open_(self, *args, **kwargs):
        if has_app_context():
            g.pop("_login_user", None)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FlaskClient, "open", open_)
END_SESSION = f"{ISSUER}/end-session"


def logout_token(claims=None, key=KEY, header=None, drop=()):
    now = int(time.time())
    body = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 120, "jti": f"j{time.time_ns()}",
            "sub": ALICE["sub"], "events": {EVENT: {}}, **(claims or {})}
    for name in drop:
        body.pop(name, None)
    return jwt.encode({"alg": "RS256", "kid": "k1", "typ": "logout+jwt", **(header or {})}, body, key)


def notify(client, token):
    return client.post("/connexion/oidc/deconnexion", data={"logout_token": token})


def login_with_sid(client, idp, sid, claims=ALICE):
    return oidc_login(client, idp, {**claims, "sid": sid})


def sessions_of(sub):
    user = get_by_sub(sub)
    return db.session.scalars(db.select(LoginSession).filter_by(user_id=user.id)).all() if user else []


# ---------------------------------------------------------------------------
# Ce qui est gardé à la connexion
# ---------------------------------------------------------------------------

def test_oidc_login_keeps_sid_and_id_token(oclient, idp):
    login_with_sid(oclient, idp, "sid-1")
    (row,) = sessions_of("alice-1")
    assert row.oidc_sid == "sid-1" and row.oidc_id_token.count(".") == 2


def test_login_without_sid_keeps_the_id_token_only(oclient, idp):
    oidc_login(oclient, idp, ALICE)
    (row,) = sessions_of("alice-1")
    assert row.oidc_sid is None and row.oidc_id_token


@pytest.mark.parametrize("sid", [42, "", None, ["a"]])
def test_strange_sid_is_ignored(oclient, idp, sid):
    oidc_login(oclient, idp, {**ALICE, "sid": sid})
    assert sessions_of("alice-1")[0].oidc_sid is None


def test_password_login_keeps_nothing_oidc(oidc_app):
    user = make_user(Role.PROF, email="prof@ohn.test")
    login(oidc_app.test_client(), user)
    row = db.session.scalar(db.select(LoginSession).filter_by(user_id=user.id))
    assert row.oidc_sid is None and row.oidc_id_token is None


def test_id_token_is_never_in_the_cookie(oclient, idp):
    login_with_sid(oclient, idp, "sid-1")
    token = sessions_of("alice-1")[0].oidc_id_token
    cookies = " ".join(c.value for c in oclient._cookies.values())
    assert token not in cookies and token.split(".")[1] not in cookies


# ---------------------------------------------------------------------------
# Avis de déconnexion reçus
# ---------------------------------------------------------------------------

def test_notice_with_sid_closes_that_session_only(oidc_app, idp):
    laptop, phone = oidc_app.test_client(), oidc_app.test_client()
    login_with_sid(laptop, idp, "sid-portable")
    login_with_sid(phone, idp, "sid-telephone")
    response = notify(oidc_app.test_client(), logout_token({"sid": "sid-portable"}))
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert me(laptop).status_code == 302 and me(phone).status_code == 200


def test_notice_with_sub_only_closes_every_oidc_session_but_not_password_ones(oidc_app, idp):
    laptop, phone, desk = oidc_app.test_client(), oidc_app.test_client(), oidc_app.test_client()
    login_with_sid(laptop, idp, "s1")
    login_with_sid(phone, idp, "s2")
    alice = get_by_sub("alice-1")
    alice.set_password("motdepasse")
    db.session.commit()
    login(desk, alice)
    assert notify(oidc_app.test_client(), logout_token(drop=("sid",))).status_code == 200
    assert me(laptop).status_code == 302 and me(phone).status_code == 302
    assert me(desk).status_code == 200  # ouverte par mot de passe : le fournisseur n'en sait rien


def test_notice_with_sid_only_works(oidc_app, idp):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    assert notify(oidc_app.test_client(), logout_token({"sid": "s1"}, drop=("sub",))).status_code == 200
    assert me(browser).status_code == 302


def test_sid_of_someone_else_closes_nothing(oidc_app, idp):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    bob = {"sub": "bob-1", "email": "bob@ohn.test", "email_verified": True, "given_name": "Bob", "family_name": "B"}
    login_with_sid(oidc_app.test_client(), idp, "s2", claims=bob)
    assert notify(oidc_app.test_client(), logout_token({"sub": "bob-1", "sid": "s1"})).status_code == 200
    assert me(browser).status_code == 200


def test_unknown_sub_is_a_no_op(oidc_app, idp):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    assert notify(oidc_app.test_client(), logout_token({"sub": "inconnu", "sid": "s1"})).status_code == 200
    assert me(browser).status_code == 200


def test_notice_for_a_session_already_closed_is_fine(oidc_app, idp):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    browser.post("/deconnexion")
    assert notify(oidc_app.test_client(), logout_token({"sid": "s1"})).status_code == 200


def test_notice_is_journaled(oidc_app, idp):
    login_with_sid(oidc_app.test_client(), idp, "s1")
    notify(oidc_app.test_client(), logout_token({"sid": "s1"}))
    entry = db.session.scalar(db.select(Activity).filter_by(action="auth.logout"))
    assert entry.message == "Déconnexion de Alice Martin par le fournisseur d'identité"
    assert entry.user_id == get_by_sub("alice-1").id


def test_remember_me_cookie_dies_with_the_session(oidc_app, idp):
    browser = oidc_app.test_client()
    oidc_login(browser, idp, {**ALICE, "sid": "s1"}, remember="1")
    notify(oidc_app.test_client(), logout_token({"sid": "s1"}))
    browser.delete_cookie(oidc_app.config.get("SESSION_COOKIE_NAME", "session"))
    assert me(browser).status_code == 302


def test_audience_list_containing_the_client_is_accepted(oidc_app, idp):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    assert notify(oidc_app.test_client(), logout_token({"sid": "s1", "aud": ["autre", CLIENT_ID]})).status_code == 200
    assert me(browser).status_code == 302


@pytest.mark.parametrize("typ", ["JWT", "logout+jwt", "application/logout+jwt"])
def test_usual_typ_values_are_accepted(oidc_app, idp, typ):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    assert notify(oidc_app.test_client(), logout_token({"sid": "s1"}, header={"typ": typ})).status_code == 200
    assert me(browser).status_code == 302


def test_typ_may_be_absent(oidc_app, idp):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    payload = json.dumps({"iss": ISSUER, "aud": CLIENT_ID, "iat": int(time.time()), "jti": "j", "sid": "s1",
                          "events": {EVENT: {}}})
    token = jws.serialize_compact({"alg": "RS256", "kid": "k1"}, payload, KEY)  # jws : jwt ajouterait typ
    assert "typ" not in json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    assert notify(oidc_app.test_client(), token).status_code == 200 and me(browser).status_code == 302


@pytest.mark.parametrize("label, token", [
    ("autre clé", lambda: logout_token({"sid": "s1"}, key=OTHER_KEY)),
    ("autre émetteur", lambda: logout_token({"sid": "s1", "iss": "https://pirate.test"})),
    ("autre appli", lambda: logout_token({"sid": "s1", "aud": "autre"})),
    ("liste sans l'appli", lambda: logout_token({"sid": "s1", "aud": ["autre"]})),
    ("sans événement", lambda: logout_token({"sid": "s1"}, drop=("events",))),
    ("mauvais événement", lambda: logout_token({"sid": "s1", "events": {"autre": {}}})),
    ("événement mal formé", lambda: logout_token({"sid": "s1", "events": {EVENT: "oui"}})),
    ("avec nonce", lambda: logout_token({"sid": "s1", "nonce": "n"})),
    ("trop ancien", lambda: logout_token({"sid": "s1", "iat": int(time.time()) - 600, "exp": int(time.time()) + 60})),
    ("daté du futur", lambda: logout_token({"sid": "s1", "iat": int(time.time()) + 600})),
    ("sans iat", lambda: logout_token({"sid": "s1"}, drop=("iat",))),
    ("expiré", lambda: logout_token({"sid": "s1", "exp": int(time.time()) - 1})),
    ("ni sub ni sid", lambda: logout_token(drop=("sub", "sid"))),
    ("sub vide", lambda: logout_token({"sub": ""}, drop=("sid",))),
    ("sub numérique", lambda: logout_token({"sub": 42, "sid": "s1"})),
    ("typ inconnu", lambda: logout_token({"sid": "s1"}, header={"typ": "at+jwt"})),
    ("alg none", lambda: "eyJhbGciOiJub25lIiwia2lkIjoiazEifQ." + "e30."),
    ("illisible", lambda: "pas.un.jeton"),
    ("vide", lambda: ""),
    ("énorme", lambda: "a" * 20000),
])
def test_invalid_notices_are_refused_and_close_nothing(oidc_app, idp, label, token):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    response = notify(oidc_app.test_client(), token())
    assert response.status_code == 400, label
    assert response.get_json() == {"error": "invalid_request"} and response.headers["Cache-Control"] == "no-store"
    assert me(browser).status_code == 200, label


def test_id_token_is_not_a_logout_token(oidc_app, idp):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    id_token = sessions_of("alice-1")[0].oidc_id_token
    assert notify(oidc_app.test_client(), id_token).status_code == 400
    assert me(browser).status_code == 200


def test_missing_parameter_and_wrong_method(oidc_app, idp):
    http = oidc_app.test_client()
    assert http.post("/connexion/oidc/deconnexion").status_code == 400
    assert http.get("/connexion/oidc/deconnexion").status_code == 405


def test_disabled_without_oidc(client):
    assert client.post("/connexion/oidc/deconnexion", data={"logout_token": "x"}).status_code == 404


def test_works_with_csrf_enabled(tmp_path, monkeypatch):
    app, ctx, idp_ = make_oidc_app(tmp_path, monkeypatch, WTF_CSRF_ENABLED=True)
    try:
        assert notify(app.test_client(), logout_token({"sid": "s1"})).status_code == 200
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def test_new_provider_key_is_fetched_once(oidc_app, idp, monkeypatch):
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    new_key = RSAKey.generate_key(2048, parameters={"kid": "k2"})
    calls = []

    def fetch(force=False):
        calls.append(force)
        keys = [KEY.as_dict(private=False)] + ([new_key.as_dict(private=False)] if force else [])
        return {"keys": keys}

    monkeypatch.setattr(idp.client, "fetch_jwk_set", fetch)
    monkeypatch.setattr(oidc_service, "_last_jwks_refresh", [0.0])
    token = logout_token({"sid": "s1"}, key=new_key, header={"kid": "k2"})
    assert notify(oidc_app.test_client(), token).status_code == 200 and calls == [False, True]
    assert me(browser).status_code == 302


def test_unknown_keys_cannot_make_us_hammer_the_provider(oidc_app, idp, monkeypatch):
    calls = []
    monkeypatch.setattr(idp.client, "fetch_jwk_set", lambda force=False: calls.append(force) or
                        {"keys": [KEY.as_dict(private=False)]})
    monkeypatch.setattr(oidc_service, "_last_jwks_refresh", [0.0])
    stranger = RSAKey.generate_key(2048, parameters={"kid": "inconnue"})
    for _ in range(5):
        notify(oidc_app.test_client(), logout_token({"sid": "s1"}, key=stranger, header={"kid": "inconnue"}))
    assert calls.count(True) == 1


def test_provider_unreachable_for_keys_answers_503(oidc_app, idp, monkeypatch):
    def boom(force=False):
        raise ConnectionError("injoignable")
    monkeypatch.setattr(idp.client, "fetch_jwk_set", boom)
    response = notify(oidc_app.test_client(), logout_token({"sid": "s1"}))
    assert response.status_code == 503  # le fournisseur réessaiera


# ---------------------------------------------------------------------------
# Déconnexion transmise au fournisseur
# ---------------------------------------------------------------------------

@pytest.fixture
def with_end_session(idp):
    idp.client.server_metadata["end_session_endpoint"] = END_SESSION
    yield
    idp.client.server_metadata.pop("end_session_endpoint", None)


def target_of(response):
    match = re.search(r'http-equiv="refresh" content="0;url=([^"]+)"', response.text)
    return html.unescape(match.group(1)) if match else None


def test_logout_goes_through_the_provider(oclient, idp, with_end_session):
    login_with_sid(oclient, idp, "s1")
    id_token = sessions_of("alice-1")[0].oidc_id_token
    response = oclient.post("/deconnexion")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    target = target_of(response)
    assert target.startswith(END_SESSION + "?")
    query = parse_qs(urlsplit(target).query)
    assert query == {"id_token_hint": [id_token], "client_id": [CLIENT_ID],
                     "post_logout_redirect_uri": ["https://inventaire.test/connexion"]}
    assert me(oclient).status_code == 302 and sessions_of("alice-1") == []
    assert 'name="referrer" content="no-referrer"' in response.text


def test_password_session_logs_out_locally(oidc_app, with_end_session):
    user = make_user(Role.PROF, email="prof@ohn.test")
    http = oidc_app.test_client()
    login(http, user)
    response = http.post("/deconnexion")
    assert response.status_code == 302 and urlsplit(response.headers["Location"]).path == "/connexion"


def test_provider_without_end_session_logs_out_locally(oclient, idp):
    login_with_sid(oclient, idp, "s1")
    response = oclient.post("/deconnexion")
    assert response.status_code == 302 and me(oclient).status_code == 302


def test_provider_logout_can_be_turned_off(tmp_path, monkeypatch):
    app, ctx, idp_ = make_oidc_app(tmp_path, monkeypatch, OIDC_PROVIDER_LOGOUT=False)
    try:
        idp_.client.server_metadata["end_session_endpoint"] = END_SESSION
        http = app.test_client()
        oidc_login(http, idp_, {**ALICE, "sid": "s1"})
        assert http.post("/deconnexion").status_code == 302
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def test_provider_unreachable_still_logs_out(oclient, idp, monkeypatch):
    login_with_sid(oclient, idp, "s1")

    def boom():
        raise ConnectionError("injoignable")
    monkeypatch.setattr(idp.client, "load_server_metadata", boom)
    response = oclient.post("/deconnexion")
    assert response.status_code == 302 and me(oclient).status_code == 302


def test_flash_is_shown_after_coming_back(oclient, idp, with_end_session):
    login_with_sid(oclient, idp, "s1")
    oclient.post("/deconnexion")
    assert "Vous êtes déconnecté" in oclient.get("/connexion").text


# ---------------------------------------------------------------------------
# external_id
# ---------------------------------------------------------------------------

def test_external_id_starts_empty_and_is_unique(app):
    a, b = make_user(Role.PROF, email="a@ohn.test"), make_user(Role.PROF, email="b@ohn.test")
    assert a.external_id is None and b.external_id is None
    a.external_id = b.external_id = "personne-1"
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# ---------------------------------------------------------------------------
# Accès de secours avec OIDC_ONLY
# ---------------------------------------------------------------------------

@pytest.fixture
def oidc_only(tmp_path, monkeypatch):
    app, ctx, idp_ = make_oidc_app(tmp_path, monkeypatch, OIDC_ONLY=True)
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


def rescue_login(http, user, password="motdepasse"):
    return http.post("/connexion?local=1", data={"email": user.email, "password": password})


def test_rescue_page_shows_the_form(oidc_only):
    http = oidc_only.test_client()
    assert 'name="password"' not in http.get("/connexion").text
    page = http.get("/connexion?local=1").text
    assert 'name="password"' in page and "Accès de secours" in page


def test_admin_can_use_the_rescue_login(oidc_only):
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    http = oidc_only.test_client()
    assert rescue_login(http, admin).status_code == 302 and me(http).status_code == 200


@pytest.mark.parametrize("role", [Role.PROF, Role.EMPRUNTEUR])
def test_rescue_login_refuses_everyone_else(oidc_only, role):
    user = make_user(role, email=f"{role}@ohn.test")
    http = oidc_only.test_client()
    response = rescue_login(http, user)
    assert response.status_code == 200 and "Email ou mot de passe incorrect" in response.text
    assert me(http).status_code == 302


def test_rescue_login_is_throttled(oidc_only):
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    http = oidc_only.test_client()
    for _ in range(8):
        rescue_login(http, admin, password="faux")
    assert rescue_login(http, admin).status_code == 429


def test_plain_password_post_still_404_with_oidc_only(oidc_only):
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    assert login(oidc_only.test_client(), admin).status_code == 404
    assert oidc_only.test_client().get("/mot-de-passe-oublie?local=1").status_code == 404


def test_rescue_needs_oidc_only(oidc_app):
    user = make_user(Role.PROF, email="prof@ohn.test")
    assert rescue_login(oidc_app.test_client(), user).status_code == 302  # sans OIDC_ONLY, rien ne change


# ---------------------------------------------------------------------------
# Rattachement des comptes d'Adhérents
# ---------------------------------------------------------------------------

def export(*accounts):
    return json.dumps([{"sub": a[0], "email": a[1], "first_name": a[2], "last_name": a[3],
                        "email_verified": a[4] if len(a) > 4 else True} for a in accounts]).encode()


def upload(http, data, name="comptes.json"):
    return http.post("/utilisateurs/rattachement-oidc", data={"export": (BytesIO(data), name)},
                     content_type="multipart/form-data")


@pytest.fixture
def admin_http(oidc_app):
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    http = oidc_app.test_client()
    login(http, admin)
    http.admin = admin
    return http


def person(first, last, email, **fields):
    user = User(first_name=first, last_name=last, email=email, role=Role.EMPRUNTEUR, active=True, **fields)
    db.session.add(user)
    db.session.commit()
    return user


def rows_of(page):
    """[(coché ?, nom côté Adhérents, statut lisible)] pour chaque ligne du tableau."""
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S)[1:]:
        checkbox = re.search(r'<input type="checkbox"[^>]*>', tr)
        rows.append((None if checkbox is None else "checked" in checkbox.group(0),
                     re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", tr)).strip()))
    return rows


def test_only_admins_reach_the_linking_screen(oidc_app):
    from .conftest import login as log_in
    http = oidc_app.test_client()
    assert http.get("/utilisateurs/rattachement-oidc").status_code == 302
    prof = make_user(Role.PROF, email="prof@ohn.test")
    log_in(http, prof)
    assert http.get("/utilisateurs/rattachement-oidc").status_code == 403


def test_manager_role_with_user_manage_is_still_refused(oidc_app):
    from app.models import Role as R
    db.session.add(R(key="gestion", name="Gestion", permissions=["user.manage", "borrower.create"]))
    db.session.commit()
    manager = make_user("gestion", email="g@ohn.test")
    http = oidc_app.test_client()
    login(http, manager)
    assert http.get("/utilisateurs/rattachement-oidc").status_code == 403
    assert upload(http, export(("s", "g@ohn.test", "Gestion", "Test"))).status_code == 403


def test_screen_explains_and_offers_the_upload(admin_http):
    page = admin_http.get("/utilisateurs/rattachement-oidc").text
    assert "Exporter les comptes" in page and 'type="file"' in page and "Récupérer les comptes" not in page
    assert "Rattacher les comptes OIDC" in admin_http.get("/utilisateurs").text


def test_proposals_and_their_defaults(admin_http):
    person("Élise", "Lefèvre", "elise@ohn.test")
    person("Léo", "Durand", "famille@ohn.test")  # l'enfant porte l'adresse du parent
    person("Inès", "Moreau", "ines@ohn.test")
    person("Jean", "Petit", "jean.ancien@ohn.test")
    person("Zoé", "Déjà", "zoe@ohn.test", oidc_sub="autre-identite")
    person("Paul", "Lié", "paul@ohn.test", oidc_sub="sub-paul")
    page = upload(admin_http, export(
        ("sub-elise", "ELISE@ohn.test", "elise", "LEFEVRE"),          # même adresse, même nom (accents, casse)
        ("sub-parent", "famille@ohn.test", "Sophie", "Durand"),       # adresse partagée
        ("sub-ines", "ines@ohn.test", "Inès", "Moreau", False),       # adresse non vérifiée
        ("sub-jean", "jean@ohn.test", "Jean", "Petit"),               # même nom, autre adresse
        ("sub-zoe", "zoe@ohn.test", "Zoé", "Déjà"),                   # fiche déjà prise
        ("sub-paul", "paul@ohn.test", "Paul", "Lié"),                 # déjà rattaché
        ("sub-nouveau", "nouveau@ohn.test", "Nina", "Neuve"),         # aucune fiche
    )).text
    rows = rows_of(page)
    assert [checked for checked, _ in rows] == [True, False, False, False, None, None, None]
    assert "même adresse vérifiée et même nom" in rows[0][1]
    assert "adresse partagée" in rows[1][1]
    assert "pas encore vérifiée" in rows[2][1]
    assert "même nom, adresse différente" in rows[3][1]
    assert "déjà rattachée à une autre identité" in rows[4][1]
    assert "déjà rattaché à cette fiche" in rows[5][1]
    assert "sera créée à la première connexion" in rows[6][1]
    assert ("1 proposé(s), 3 à vérifier, 1 déjà rattaché(s), 1 sur une fiche déjà prise, 1 sans fiche"
            in re.sub(r"\s+", " ", page))
    assert db.session.scalar(db.select(User).filter_by(email="elise@ohn.test")).oidc_sub is None  # rien de fait


def test_a_fiche_proposed_twice_is_checked_for_nobody(admin_http):
    person("Max", "Roux", "max@ohn.test")
    page = upload(admin_http, export(("a", "max@ohn.test", "Max", "Roux"), ("b", "autre@ohn.test", "Max", "Roux"))).text
    assert [checked for checked, _ in rows_of(page)] == [False, False]
    assert page.count("plusieurs comptes") == 2


def test_namesakes_are_not_guessed(admin_http):
    person("Lucas", "Martin", "lucas1@ohn.test")
    person("Lucas", "Martin", "lucas2@ohn.test")
    page = upload(admin_http, export(("s", "lucas@ohn.test", "Lucas", "Martin"))).text
    assert rows_of(page)[0][0] is None and "aucune fiche" in page


def test_archived_fiches_are_not_proposed(admin_http):
    from datetime import datetime
    person("Ana", "Gris", "ana@ohn.test", archived_at=datetime.now())
    page = upload(admin_http, export(("s", "ana@ohn.test", "Ana", "Gris"))).text
    assert "aucune fiche" in page


def confirm(http, *pairs):
    data = {"step": "confirm", "link": [str(i) for i in range(1, len(pairs) + 1)]}
    for index, (sub, user) in enumerate(pairs, 1):
        data[f"sub-{index}"] = sub
        data[f"user-{index}"] = str(user.id)
    return http.post("/utilisateurs/rattachement-oidc", data=data, follow_redirects=True)


def test_confirm_links_the_checked_rows_and_journals_them(admin_http):
    elise = person("Élise", "Lefèvre", "elise@ohn.test")
    page = confirm(admin_http, ("sub-elise", elise)).text
    assert "1 compte(s) rattaché(s)" in page
    assert db.session.get(User, elise.id).oidc_sub == "sub-elise"
    entry = db.session.scalar(db.select(Activity).filter_by(action="user.oidc_link"))
    assert entry.target_user_id == elise.id and entry.user_id == admin_http.admin.id


def test_unchecked_rows_are_left_alone(admin_http):
    elise = person("Élise", "Lefèvre", "elise@ohn.test")
    admin_http.post("/utilisateurs/rattachement-oidc", data={"step": "confirm", "sub-1": "x", "user-1": str(elise.id)})
    assert db.session.get(User, elise.id).oidc_sub is None


def test_confirm_rechecks_everything(admin_http):
    taken = person("Zoé", "Prise", "zoe@ohn.test", oidc_sub="deja")
    free = person("Ali", "Libre", "ali@ohn.test")
    other = person("Bea", "Autre", "bea@ohn.test")
    page = confirm(admin_http, ("nouveau", taken), ("deja", free), ("s", type("U", (), {"id": 99999})()),
                   ("ok", other)).text
    assert "1 compte(s) rattaché(s)" in page and page.count("Non rattaché") == 3
    assert db.session.get(User, taken.id).oidc_sub == "deja" and db.session.get(User, free.id).oidc_sub is None
    assert db.session.get(User, other.id).oidc_sub == "ok"


def test_linked_fiche_is_found_by_sub_at_login_not_by_email(oidc_app, admin_http, idp):
    """Le cas du plan : l'enfant porte l'adresse du parent. Rattaché d'avance, le parent retrouve sa propre fiche."""
    child = person("Léo", "Durand", "famille@ohn.test")
    parent = person("Sophie", "Durand", None)
    confirm(admin_http, ("sub-parent", parent))
    browser = oidc_app.test_client()
    oidc_login(browser, idp, {"sub": "sub-parent", "email": "famille@ohn.test", "email_verified": True,
                              "given_name": "Sophie", "family_name": "Durand"})
    assert get_by_sub("sub-parent").id == parent.id
    assert db.session.get(User, child.id).oidc_sub is None


@pytest.mark.parametrize("data, message", [
    (b"pas du json", "pas un export JSON"),
    (b'{"x": 1}', "pas de liste de comptes"),
    (b'[{"email": "a@b.c"}]', "identifiant (sub) manquant"),
    (b'[{"sub": "a"}, {"sub": "a"}]', "identifiant en double"),
    (('[{"sub": "' + "a" * 300 + '"}]').encode(), "identifiant trop long"),
    (b"\xff\xfe", "pas un export JSON"),
])
def test_bad_exports_are_explained(admin_http, data, message):
    page = upload(admin_http, data).text
    assert message in html.unescape(page) and "<table" not in page


def test_export_wrapped_in_an_object_is_accepted(admin_http):
    person("Élise", "Lefèvre", "elise@ohn.test")
    data = json.dumps({"accounts": json.loads(export(("s", "elise@ohn.test", "Élise", "Lefèvre")))}).encode()
    assert rows_of(upload(admin_http, data).text)[0][0] is True


def test_missing_file_is_explained(admin_http):
    page = admin_http.post("/utilisateurs/rattachement-oidc", data={}, follow_redirects=True).text
    assert "Choisissez le fichier" in html.unescape(page)


def test_names_in_the_export_are_escaped(admin_http):
    page = upload(admin_http, export(("s", "x@ohn.test", "<script>alert(1)</script>", "X"))).text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_linking_screen_is_never_cached(admin_http):
    assert admin_http.get("/utilisateurs/rattachement-oidc").headers["Cache-Control"] == "no-store"


def test_algorithm_confusion_is_refused(oidc_app, idp):
    """HS256 signé avec la clé publique du fournisseur : l'attaque classique contre une vérification trop permissive."""
    import hashlib
    import hmac
    browser = oidc_app.test_client()
    login_with_sid(browser, idp, "s1")
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "kid": "k1"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps({"iss": ISSUER, "aud": CLIENT_ID, "iat": int(time.time()),
                                                "sid": "s1", "events": {EVENT: {}}}).encode()).rstrip(b"=")
    secret = KEY.as_pem(private=False)
    signature = base64.urlsafe_b64encode(hmac.new(secret, header + b"." + body, hashlib.sha256).digest()).rstrip(b"=")
    token = (header + b"." + body + b"." + signature).decode()
    assert notify(oidc_app.test_client(), token).status_code == 400 and me(browser).status_code == 200


# ---------------------------------------------------------------------------
# OIDC_LINK_BY_EMAIL=0 : plus de rattachement par l'email à la connexion
# ---------------------------------------------------------------------------

@pytest.fixture
def no_email_linking(tmp_path, monkeypatch):
    app, ctx, idp_ = make_oidc_app(tmp_path, monkeypatch, OIDC_LINK_BY_EMAIL=False)
    app.idp = idp_
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


SOPHIE = {"sub": "sub-sophie", "email": "famille@ohn.test", "email_verified": True, "given_name": "Sophie",
          "family_name": "Durand"}


def test_shared_address_is_refused_instead_of_taking_the_childs_fiche(no_email_linking):
    child = person("Léo", "Durand", "famille@ohn.test")
    browser = no_email_linking.test_client()
    response = oidc_login(browser, no_email_linking.idp, SOPHIE)
    assert response.status_code == 302 and me(browser).status_code == 302
    assert "pas encore rattaché" in html.unescape(browser.get("/connexion").text)
    assert db.session.get(User, child.id).oidc_sub is None and get_by_sub("sub-sophie") is None


def test_linked_identity_still_logs_in(no_email_linking):
    parent = person("Sophie", "Durand", None, oidc_sub="sub-sophie")
    browser = no_email_linking.test_client()
    oidc_login(browser, no_email_linking.idp, SOPHIE)
    assert me(browser).status_code == 200 and get_by_sub("sub-sophie").id == parent.id


def test_unknown_address_still_creates_a_new_fiche(no_email_linking):
    browser = no_email_linking.test_client()
    oidc_login(browser, no_email_linking.idp, {**SOPHIE, "email": "sophie@ohn.test"})
    assert me(browser).status_code == 200 and get_by_sub("sub-sophie").email == "sophie@ohn.test"


def test_linking_screen_warns_while_email_linking_is_on(admin_http):
    assert "OIDC_LINK_BY_EMAIL=0" in admin_http.get("/utilisateurs/rattachement-oidc").text


def test_no_warning_once_email_linking_is_off(no_email_linking):
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    http = no_email_linking.test_client()
    login(http, admin)
    assert "OIDC_LINK_BY_EMAIL=0" not in http.get("/utilisateurs/rattachement-oidc").text


def test_linking_opens_a_borrower_fiche_that_never_had_access(oidc_app, admin_http, idp):
    """Trouvé par le test de bout en bout : sans cela, la personne rattachée était refusée (« compte désactivé »)."""
    fiche = User(first_name="Élise", last_name="Test", email="elise@ohn.test", role=Role.EMPRUNTEUR)
    db.session.add(fiche)
    db.session.commit()
    assert fiche.active is False
    page = upload(admin_http, export(("sub-elise", "elise@ohn.test", "Élise", "Test"))).text
    assert "fiche sans accès, elle sera ouverte" in html.unescape(page)
    confirm(admin_http, ("sub-elise", fiche))
    assert db.session.get(User, fiche.id).active is True
    browser = oidc_app.test_client()
    oidc_login(browser, idp, {"sub": "sub-elise", "email": "elise@ohn.test", "email_verified": True,
                              "given_name": "Élise", "family_name": "Test"})
    assert me(browser).status_code == 200


def test_linking_keeps_a_deactivated_account_closed(oidc_app, admin_http, idp):
    closed = make_user(Role.PROF, email="ancien@ohn.test", active=False)
    page = upload(admin_http, export(("sub-ancien", "ancien@ohn.test", "Prof", "Test"))).text
    assert "compte désactivé, il le restera" in html.unescape(page)
    confirm(admin_http, ("sub-ancien", closed))
    assert db.session.get(User, closed.id).active is False
    browser = oidc_app.test_client()
    oidc_login(browser, idp, {"sub": "sub-ancien", "email": "ancien@ohn.test", "email_verified": True})
    assert me(browser).status_code == 302


# ---------------------------------------------------------------------------
# Liste des comptes récupérée directement chez Adhérents (OIDC_ACCOUNTS_URL)
# ---------------------------------------------------------------------------

class Directory:
    """Faux /oidc/comptes sur 127.0.0.1 : note l'en-tête Authorization, répond ce qu'on lui demande."""

    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        self.status, self.body, self.location, self.seen = 200, b"[]", None, []
        directory = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                directory.seen.append(self.headers.get("Authorization"))
                self.send_response(directory.status)
                if directory.location:
                    self.send_header("Location", directory.location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(directory.body)))
                self.end_headers()
                self.wfile.write(directory.body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/oidc/comptes"


@pytest.fixture
def directory():
    server = Directory()
    yield server
    server.server.shutdown()
    server.server.server_close()


@pytest.fixture
def fetching(tmp_path, monkeypatch, directory):
    app, ctx, idp_ = make_oidc_app(tmp_path, monkeypatch, OIDC_ACCOUNTS_URL=directory.url)
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    http = app.test_client()
    login(http, admin)
    yield http
    db.session.remove()
    db.drop_all()
    ctx.pop()


def fetch_page(http):
    return http.post("/utilisateurs/rattachement-oidc", data={"step": "fetch"}, follow_redirects=True).text


def test_button_appears_with_the_url(fetching):
    """Sans l'adresse, pas de bouton : voir test_screen_explains_and_offers_the_upload."""
    assert "Récupérer les comptes depuis Adhérents" in fetching.get("/utilisateurs/rattachement-oidc").text


def test_fetch_shows_the_same_proposals_as_the_file(fetching, directory):
    person("Élise", "Lefèvre", "elise@ohn.test")
    directory.body = json.dumps({"issuer": ISSUER, "application": "Inventaire", "accounts": json.loads(
        export(("sub-elise", "elise@ohn.test", "Élise", "Lefèvre")))}).encode()
    page = fetch_page(fetching)
    assert [checked for checked, _ in rows_of(page)] == [True]
    expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:secret".encode()).decode()
    assert directory.seen == [expected]
    assert db.session.scalar(db.select(User).filter_by(email="elise@ohn.test")).oidc_sub is None  # rien de fait


@pytest.mark.parametrize("status, message", [
    (401, "refuse l'identifiant ou le secret"), (403, "Peut récupérer la liste des comptes"),
    (404, "vérifiez OIDC_ACCOUNTS_URL"), (429, "Trop d'essais"), (500, "Adhérents a répondu 500"),
    (302, "Adhérents a répondu 302"),
])
def test_fetch_errors_are_explained(fetching, directory, status, message):
    directory.status, directory.location = status, ("https://ailleurs.test/" if status == 302 else None)
    page = html.unescape(fetch_page(fetching))
    assert message in page and "<table" not in page


def test_fetch_with_a_bad_body_is_explained(fetching, directory):
    directory.body = b"<html>pas du json</html>"
    assert "pas un export JSON" in html.unescape(fetch_page(fetching))


def test_unreachable_adherents(tmp_path, monkeypatch):
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app, ctx, _ = make_oidc_app(tmp_path, monkeypatch, OIDC_ACCOUNTS_URL=f"http://127.0.0.1:{port}/oidc/comptes")
    try:
        http = app.test_client()
        login(http, make_user(Role.ADMIN, email="admin@ohn.test"))
        assert "injoignable" in html.unescape(fetch_page(http))
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()


@pytest.mark.parametrize("url", ["http://adherents.test/oidc/comptes", "ftp://127.0.0.1/x"])
def test_secret_is_never_sent_in_clear_text_over_the_network(tmp_path, monkeypatch, url):
    app, ctx, _ = make_oidc_app(tmp_path, monkeypatch, OIDC_ACCOUNTS_URL=url)
    try:
        http = app.test_client()
        login(http, make_user(Role.ADMIN, email="admin@ohn.test"))
        assert "doit être en https" in fetch_page(http)
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()


def test_fetch_step_without_url_falls_back_to_the_file(admin_http):
    page = html.unescape(admin_http.post("/utilisateurs/rattachement-oidc", data={"step": "fetch"},
                                         follow_redirects=True).text)
    assert "Choisissez le fichier" in page


def test_fetch_is_for_admins_only(tmp_path, monkeypatch, directory):
    app, ctx, _ = make_oidc_app(tmp_path, monkeypatch, OIDC_ACCOUNTS_URL=directory.url)
    try:
        http = app.test_client()
        login(http, make_user(Role.PROF, email="prof@ohn.test"))
        assert http.post("/utilisateurs/rattachement-oidc", data={"step": "fetch"}).status_code == 403
        assert directory.seen == []
    finally:
        db.session.remove()
        db.drop_all()
        ctx.pop()
