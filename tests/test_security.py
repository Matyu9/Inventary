"""Correctifs de sécurité de la phase 10 : chacun a son test de non-régression."""
import csv
import io
import os

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import create_app
from app.blueprints import auth
from app.config import Config, TestConfig
from app.extensions import db
from app.models import Category, Item, Loan, LoanLine, LoginSession, LoanType, Role, Tag, User
from app.services import stock

from .conftest import login

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.mark.parametrize("target", ["//evil.com", "/\\evil.com", "/\\/evil.com", "https://evil.com", "http:/evil.com",
                                    "javascript:alert(1)", "///evil.com", "evil.com", "", "\\\\evil.com"])
def test_open_redirect_variants_blocked(client, users, target):
    r = client.post("/connexion", query_string={"next": target},
                    data={"email": users[A].email, "password": "motdepasse"})
    assert r.headers["Location"] == "/"


def test_local_next_kept(client, users):
    r = client.post("/connexion?next=/objets%3Fq%3Dsax", data={"email": users[A].email, "password": "motdepasse"})
    assert r.headers["Location"] == "/objets?q=sax"


def test_login_throttled_per_email(client, users):
    for _ in range(auth.MAX_FAILURES):
        assert "incorrect" in login(client, users[P], password="faux").text
    r = login(client, users[P])  # même le bon mot de passe est refusé pendant la fenêtre
    assert r.status_code == 429 and "Trop de tentatives" in r.text


def test_login_throttled_per_ip(client, users):
    for n in range(auth.MAX_FAILURES):
        client.post("/connexion", data={"email": f"inconnu{n}@ohn.test", "password": "x"})
    assert login(client, users[A]).status_code == 429


def test_successful_login_resets_counter(client, users):
    for _ in range(auth.MAX_FAILURES - 1):
        login(client, users[P], password="faux")
    assert login(client, users[P]).status_code == 302
    client.post("/deconnexion")
    for _ in range(auth.MAX_FAILURES - 1):
        login(client, users[P], password="faux")
    assert login(client, users[P]).status_code == 302


def test_unknown_email_still_hashes(client, monkeypatch, app):
    calls = []
    original = auth._DUMMY.check_password
    monkeypatch.setattr(auth._DUMMY, "check_password", lambda pw: calls.append(pw) or original(pw))
    client.post("/connexion", data={"email": "personne@ohn.test", "password": "x"})
    assert calls == ["x"]


def test_default_secret_refused_in_production(monkeypatch, tmp_path):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)

    class Prod(Config):
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "u"

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(Prod)
    monkeypatch.setenv("FLASK_DEBUG", "0")
    with pytest.raises(RuntimeError):
        create_app(Prod)
    monkeypatch.setenv("FLASK_DEBUG", "1")
    assert create_app(Prod)


@pytest.mark.parametrize("secret", ["change-moi", ""])
def test_placeholder_or_empty_secret_refused_in_production(monkeypatch, tmp_path, secret):
    """La valeur du modèle .env.example, recopiée telle quelle, ne doit pas suffire à démarrer."""
    monkeypatch.delenv("FLASK_DEBUG", raising=False)

    class Prod(Config):
        SECRET_KEY = secret
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "u"

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(Prod)


def test_custom_secret_ok_in_production(monkeypatch, tmp_path):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)

    class Prod(Config):
        SECRET_KEY = "x" * 40
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "u"

    assert create_app(Prod)


def test_proxy_fix(tmp_path):
    class Proxied(TestConfig):
        TRUST_PROXY = True
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "u"

    app = create_app(Proxied)
    with app.test_request_context("/", headers={"X-Forwarded-Proto": "https"}, environ_base={"REMOTE_ADDR": "10.0.0.1"}):
        pass
    assert type(app.wsgi_app).__name__ == "ProxyFix"


def test_security_headers(client, users):
    r = client.get("/connexion")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "same-origin" and "camera=(self)" in r.headers["Permissions-Policy"]


def test_password_pages_not_cached(client, as_role, users):
    as_role(A)
    assert client.get("/mot-de-passe").headers["Cache-Control"] == "no-store"
    assert client.post(f"/utilisateurs/{users[P].id}/mot-de-passe").headers["Cache-Control"] == "no-store"
    assert "Cache-Control" not in client.get("/objets").headers or "no-store" not in client.get("/objets").headers.get("Cache-Control", "")


def test_session_cookie_flags(client, users):
    r = login(client, users[A])
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_secure_cookie_config_follows_base_url():
    assert Config.SESSION_COOKIE_SECURE == Config.BASE_URL.startswith("https://")


def test_decompression_bomb_rejected(app, monkeypatch):
    from app.services import photos
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    buf = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buf, "PNG")  # 10 000 px > 2 × 100 : erreur PIL
    buf.seek(0)
    with pytest.raises(photos.InvalidImage, match="trop grande"):
        photos.save_photo(FileStorage(buf, filename="x.png"))


def test_bomb_via_route_is_not_500(client, as_role, app, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    item = Item(code="OHN-0001", name="Sax", category=Category(name="Bois"))
    db.session.add(item)
    db.session.commit()
    buf = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buf, "PNG")
    buf.seek(0)
    as_role(P)
    r = client.post(f"/objets/{item.id}/photos", data={"photos": (buf, "x.png")}, content_type="multipart/form-data",
                    follow_redirects=True)
    assert r.status_code == 200 and "trop grande" in r.text


def test_write_requests_use_immediate_transactions(app):
    from app.extensions import _is_write_request
    with app.test_request_context("/", method="POST"):
        assert _is_write_request()
    with app.test_request_context("/", method="GET"):
        assert not _is_write_request()
    assert not _is_write_request()  # hors requête (CLI, scheduler)


def test_bulk_redirect_ignores_foreign_referrer(client, as_role, app):
    item = Item(code="OHN-0001", name="Sax", category=Category(name="Bois"))
    db.session.add(item)
    db.session.commit()
    as_role(P)
    r = client.post("/objets/actions-groupees", data={"ids": [item.id], "action": "tag", "tag": "x"},
                    headers={"Referer": "https://evil.com/phish"})
    assert r.headers["Location"] == "/objets"
    r = client.post("/objets/actions-groupees", data={"ids": [item.id], "action": "tag", "tag": "x"},
                    headers={"Referer": "http://localhost/objets?q=sax"})
    assert r.headers["Location"] == "/objets?q=sax"


def _item_form(item, **kw):
    data = {"name": item.name, "category_id": item.category_id, "condition": "bon", "status": "disponible",
            "location_id": "0", "tags": "", "tracking": item.tracking, "consumption_mode": ""}
    data.update(kw)
    return data


def test_tracking_change_blocked_for_kit_items(client, as_role, app):
    from app.models import Kit
    item = Item(code="OHN-0001", name="Caisse", category=Category(name="Perc"), kit=Kit(name="Batterie"))
    db.session.add(item)
    db.session.commit()
    as_role(P)
    r = client.post(f"/objets/{item.id}/modifier", data=_item_form(item, tracking="quantite"))
    assert "Retirez d&#39;abord" in r.text or "Retirez d'abord" in r.text
    db.session.refresh(item)
    assert item.tracking == "individuel"


def test_tracking_change_blocked_after_loans(client, as_role, users, app):
    from datetime import datetime
    item = Item(code="OHN-0001", name="Sax", category=Category(name="Bois"))
    loan = Loan(type=LoanType.GARDE, borrower=users[E], requested_by=users[P], start=datetime.now())
    loan.lines = [LoanLine(item=item, quantity=1)]
    db.session.add(loan)
    db.session.commit()
    as_role(P)
    r = client.post(f"/objets/{item.id}/modifier", data=_item_form(item, tracking="quantite"))
    assert "déjà été prêté" in r.text


def test_tracking_change_allowed_otherwise(client, as_role, app):
    item = Item(code="OHN-0001", name="Pupitres", category=Category(name="Matos"))
    db.session.add(item)
    db.session.commit()
    as_role(P)
    assert client.post(f"/objets/{item.id}/modifier", data=_item_form(item, tracking="quantite")).status_code == 302


def test_stale_imports_cleaned(client, as_role, app):
    folder = app.config["DATA_DIR"] / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    stale = folder / "ancien.json"
    stale.write_text("{}")
    os.utime(stale, (0, 0))
    fresh = folder / "recent.json"
    fresh.write_text("{}")
    as_role(A)
    client.post("/import", data={"file": (io.BytesIO(b"Nom\nX\n"), "a.csv")}, content_type="multipart/form-data")
    assert not stale.exists() and fresh.exists()


# ---------------------------------------------------------------------------
# Audit en ligne : sessions révocables, débordements, export d'inventaire, tags orphelins
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated(tmp_path):
    """Appli sans contexte ouvert entre les requêtes : Flask-Login ne garde pas l'utilisateur en cache dans `g`,
    comme en production (indispensable pour tester le rejeu d'un cookie)."""
    class Cfg(TestConfig):
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "uploads"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'inventaire.db'}"

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        for role in (A, P):
            user = User(first_name=role, last_name="Test", email=f"{role}@ohn.test", role=role, active=True)
            user.set_password("motdepasse")
            db.session.add(user)
        db.session.commit()
    return app


def _connect(app, email=f"{P}@ohn.test", password="motdepasse", remember=False):
    client = app.test_client()
    data = {"email": email, "password": password, **({"remember": "y"} if remember else {})}
    assert client.post("/connexion", data=data).status_code == 302
    return client


def _replay(app, client, name="session"):
    """Un autre navigateur qui présente le cookie copié depuis `client`."""
    thief = app.test_client()
    thief.set_cookie(name, client.get_cookie(name).value)
    return thief.get("/objets")


def _count_sessions(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(LoginSession.id)))


def test_stolen_session_works_until_logout(isolated):
    client = _connect(isolated)
    assert _replay(isolated, client).status_code == 200
    stolen = client.get_cookie("session").value
    client.post("/deconnexion")
    thief = isolated.test_client()
    thief.set_cookie("session", stolen)
    assert thief.get("/objets").status_code == 302
    assert _count_sessions(isolated) == 0


def test_remember_cookie_revoked_by_logout(isolated):
    client = _connect(isolated, remember=True)
    assert _replay(isolated, client, "remember_token").status_code == 200
    stolen = client.get_cookie("remember_token").value
    client.post("/deconnexion")
    thief = isolated.test_client()
    thief.set_cookie("remember_token", stolen)
    assert thief.get("/objets").status_code == 302


def test_logout_only_ends_this_device(isolated):
    phone, laptop = _connect(isolated), _connect(isolated)
    phone.post("/deconnexion")
    assert laptop.get("/objets").status_code == 200


def test_password_change_revokes_other_sessions(isolated):
    other, current = _connect(isolated, remember=True), _connect(isolated)
    r = current.post("/mot-de-passe", data={"current": "motdepasse", "new": "nouveau-mdp", "confirm": "nouveau-mdp"})
    assert r.status_code == 302
    assert current.get("/objets").status_code == 200  # l'appareil qui a changé le mot de passe reste connecté
    assert other.get("/objets").status_code == 302
    assert _replay(isolated, other, "remember_token").status_code == 302
    assert _count_sessions(isolated) == 1


def test_admin_password_reset_revokes_all_sessions(isolated):
    prof = _connect(isolated)
    admin = _connect(isolated, email=f"{A}@ohn.test")
    with isolated.app_context():
        prof_id = db.session.scalar(db.select(User.id).filter_by(email=f"{P}@ohn.test"))
    assert admin.post(f"/utilisateurs/{prof_id}/mot-de-passe").status_code == 200
    # Déconnecté (renvoyé à la connexion), pas seulement poussé vers le changement de mot de passe.
    assert prof.get("/objets").headers["Location"].startswith("/connexion")
    assert _count_sessions(isolated) == 1


def test_deactivation_revokes_sessions(isolated):
    prof = _connect(isolated)
    admin = _connect(isolated, email=f"{A}@ohn.test")
    with isolated.app_context():
        prof_id = db.session.scalar(db.select(User.id).filter_by(email=f"{P}@ohn.test"))
    r = admin.post(f"/utilisateurs/{prof_id}/modifier", data={
        "first_name": "prof", "last_name": "Test", "email": f"{P}@ohn.test", "role": P, "member_status": ""})
    assert r.status_code == 302
    assert prof.get("/objets").headers["Location"].startswith("/connexion")
    assert _count_sessions(isolated) == 1  # reste celle de l'admin


@pytest.mark.parametrize("user_id", ["1", "1:", ":x", "abc:def", "1:jeton-inconnu", "9" * 40 + ":x"])
def test_forged_or_legacy_session_ids_rejected(isolated, user_id):
    """Ancien cookie (« 1 », sans jeton) ou identifiant fabriqué : visiteur anonyme, jamais d'erreur."""
    client = isolated.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"], sess["_fresh"] = user_id, True
    assert client.get("/objets").status_code == 302


def test_session_of_another_user_rejected(isolated):
    """Un jeton valide ne vaut que pour son propriétaire : changer l'identifiant devant ne sert à rien."""
    client = _connect(isolated)
    with client.session_transaction() as sess:
        _, token = sess["_user_id"].split(":", 1)
        sess["_user_id"] = f"1:{token}"  # 1 = l'admin
    assert client.get("/objets").status_code == 302


def test_expired_sessions_pruned_at_login(isolated):
    from datetime import datetime, timedelta
    with isolated.app_context():
        db.session.add(LoginSession(id="vieux", user_id=1, created_at=datetime.now() - timedelta(days=400)))
        db.session.commit()
    _connect(isolated)
    with isolated.app_context():
        assert db.session.get(LoginSession, "vieux") is None


@pytest.mark.parametrize("url", ["/objets/" + "9" * 30, "/prets/" + "9" * 30, "/kits/" + "9" * 30,
                                 "/objets?page=" + "9" * 30, "/prets/nouveau?item_id=" + "9" * 30,
                                 "/calendrier/evenements?start=9999-12-31T00:00:00&end=9999-12-31T23:00:00"])
def test_out_of_range_values_are_not_500(client, as_role, url):
    as_role(A)
    assert client.get(url).status_code in (200, 404)


@pytest.mark.parametrize("data", [{"ids": "9" * 30, "action": "move"},
                                  {"ids": "1", "action": "move", "location_id": "9" * 30}])
def test_out_of_range_bulk_is_not_500(client, as_role, data):
    db.session.add(Item(id=1, code="OHN-0001", name="Sax", category=Category(name="Bois")))
    db.session.commit()
    as_role(A)
    assert client.post("/objets/actions-groupees", data=data).status_code == 404
    assert client.get("/objets").status_code == 200  # la session SQL a été remise en état


@pytest.mark.parametrize("url", ["/rapports", "/rapports/valeur.csv", "/rapports/utilisation.csv"])
def test_reports_extreme_dates(client, as_role, url):
    as_role(A)
    assert client.get(url, query_string={"debut": "0001-01-01", "fin": "9999-12-31"}).status_code == 200


def _stock_item(quantity=5):
    item = Item(code="OHN-0001", name="Anches", category=Category(name="Conso"), tracking="quantite",
                quantity=quantity)
    db.session.add(item)
    db.session.commit()
    return item


@pytest.mark.parametrize("amount", ["9" * 30, str(2 ** 62), str(stock.MAX_QUANTITY + 1)])
def test_huge_stock_amount_refused(client, as_role, amount):
    item = _stock_item()
    as_role(A)
    r = client.post(f"/objets/{item.id}/stock", data={"reason": "achat", "amount": amount}, follow_redirects=True)
    assert r.status_code == 200 and "Quantité invalide" in r.text
    db.session.refresh(item)
    assert item.quantity == 5


def test_stock_total_capped(client, as_role):
    item = _stock_item(stock.MAX_QUANTITY - 1)
    as_role(A)
    r = client.post(f"/objets/{item.id}/stock", data={"reason": "achat", "amount": "2"}, follow_redirects=True)
    assert "ne peut pas dépasser" in r.text
    assert client.post(f"/objets/{item.id}/stock", data={"reason": "achat", "amount": "1"}).status_code == 302
    db.session.refresh(item)
    assert item.quantity == stock.MAX_QUANTITY


def test_audit_csv_neutralises_formulas(client, as_role, users):
    from app.models import InventoryAudit
    from app.services import audits
    item = Item(code="OHN-0001", name='=HYPERLINK("https://evil.test";"clic")', category=Category(name="@cat"))
    audit = InventoryAudit(name="Rentrée", opened_by=users[A])
    db.session.add_all([item, audit])
    db.session.commit()
    audits.scan(audit, "OHN-0001", users[A])
    db.session.commit()
    as_role(A)
    text = client.get(f"/inventaires/{audit.id}/export.csv").data.decode("utf-8-sig")
    row = list(csv.reader(io.StringIO(text), delimiter=";"))[1]
    assert row[1] == "'=HYPERLINK(\"https://evil.test\";\"clic\")" and row[2] == "'@cat"


def _tag_names():
    return sorted(db.session.scalars(db.select(Tag.name)))


def test_tag_removed_when_no_item_uses_it(client, as_role):
    item = Item(code="OHN-0001", name="Sax", category=Category(name="Bois"))
    db.session.add(item)
    db.session.commit()
    as_role(P)
    client.post(f"/objets/{item.id}/modifier", data=_item_form(item, tags="concert, prêt"))
    assert _tag_names() == ["concert", "prêt"]
    client.post(f"/objets/{item.id}/modifier", data=_item_form(item, tags="concert"))
    assert _tag_names() == ["concert"]


def test_shared_tag_kept_until_last_item_deleted(client, as_role):
    cat = Category(name="Bois")
    sax, flute = Item(code="OHN-0001", name="Sax", category=cat), Item(code="OHN-0002", name="Flûte", category=cat)
    db.session.add_all([sax, flute])
    db.session.commit()
    as_role(A)
    for item in (sax, flute):
        client.post(f"/objets/{item.id}/modifier", data=_item_form(item, tags="concert"))
    client.post(f"/objets/{sax.id}/supprimer")
    assert _tag_names() == ["concert"]
    client.post(f"/objets/{flute.id}/supprimer")
    assert _tag_names() == []


# ---------------------------------------------------------------------------
# Déconnexion de tout le monde
# ---------------------------------------------------------------------------

def test_revoke_all_sessions(isolated):
    prof = _connect(isolated, remember=True)
    admin = _connect(isolated, email=f"{A}@ohn.test")
    admin_phone = _connect(isolated, email=f"{A}@ohn.test")
    assert "3 (2 personnes)" in admin.get("/parametres").text
    r = admin.post("/parametres/deconnecter-tout", follow_redirects=True)
    assert "2 session(s) fermée(s)" in r.text
    assert admin.get("/parametres").status_code == 200  # l'admin qui clique reste connecté
    assert prof.get("/objets").headers["Location"].startswith("/connexion")
    assert _replay(isolated, prof, "remember_token").status_code == 302
    assert admin_phone.get("/objets").status_code == 302  # ses autres appareils aussi
    with isolated.app_context():
        from app.models import Activity
        assert db.session.scalar(db.select(Activity.action).order_by(Activity.id.desc())) == "sessions.revoke_all"


def test_revoke_all_admin_only(isolated):
    prof = _connect(isolated)
    assert prof.post("/parametres/deconnecter-tout").status_code == 403
    assert _count_sessions(isolated) == 1


# ---------------------------------------------------------------------------
# Pages d'erreur
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method, url, code, title", [
    ("get", "/nexiste-pas", 404, "Page introuvable"),
    ("get", "/parametres", 403, "Accès refusé"),
    ("get", "/deconnexion", 405, "Action impossible"),
    ("get", "/objets/" + "9" * 30, 404, "Page introuvable"),
])
def test_error_pages(client, as_role, method, url, code, title):
    as_role(P)
    r = getattr(client, method)(url)
    assert r.status_code == code
    assert f"Erreur {code}" in r.text and title in r.text and "error-card" in r.text
    assert "Tableau de bord" in r.text and 'class="sidebar"' in r.text  # menu de l'appli conservé


def test_error_page_back_link_only_for_local_referrer(client, as_role):
    as_role(P)
    assert 'href="/objets?q=sax">Page précédente' in client.get(
        "/nexiste-pas", headers={"Referer": "http://localhost/objets?q=sax"}).text
    assert "Page précédente" not in client.get("/nexiste-pas", headers={"Referer": "https://evil.test/x"}).text


def test_error_page_for_guest(client):
    r = client.get("/nexiste-pas")
    assert r.status_code == 404 and "Se connecter" in r.text and 'class="sidebar"' not in r.text


def test_file_too_large_page(client, as_role, app):
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    as_role(A)
    r = client.post("/import", data={"file": (io.BytesIO(b"x" * (2 * 1024 * 1024)), "gros.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 413 and "Fichier trop volumineux" in r.text and "dépasse 1 Mo" in r.text


def test_csrf_expired_page(tmp_path):
    class Cfg(TestConfig):
        WTF_CSRF_ENABLED = True
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "uploads"

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        r = app.test_client().post("/connexion", data={"email": "x@ohn.test", "password": "x"})
    assert r.status_code == 400 and "Page expirée" in r.text and "rien n&#39;a été enregistré" in r.text


@pytest.fixture
def crashing(tmp_path):
    """Appli « de production » (exceptions non propagées) avec une route qui plante."""
    class Cfg(TestConfig):
        TESTING = False
        PROPAGATE_EXCEPTIONS = False
        SECRET_KEY = "cle-de-test-pour-la-page-500"
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "uploads"

    app = create_app(Cfg)

    @app.route("/boum", methods=["GET", "POST"])
    def boum():
        db.session.execute(db.text("SELECT * FROM table_inexistante"))

    with app.app_context():
        db.create_all()
    return app


def test_500_page(crashing, caplog):
    r = crashing.test_client().get("/boum?x=1")
    assert r.status_code == 500
    assert "Erreur 500" in r.text and "Quelque chose s'est mal passé" in r.text
    assert 'href="/boum?x=1">Réessayer' in r.text
    ref = r.text.split("Référence <code>")[1].split("</code>")[0]
    assert len(ref) == 8 and ref in caplog.text  # la référence permet de retrouver l'erreur dans les logs
    assert "table_inexistante" not in r.text  # aucun détail technique affiché


def test_500_page_after_post_has_no_retry(crashing):
    r = crashing.test_client().post("/boum")
    assert r.status_code == 500 and "Réessayer" not in r.text


def test_500_page_survives_broken_templates(crashing, monkeypatch):
    import app.errors as errors

    def broken(*_a, **_k):
        raise RuntimeError("gabarit cassé")
    monkeypatch.setattr(errors, "render_template", broken)
    r = crashing.test_client().get("/boum")
    assert r.status_code == 500 and "Erreur interne (référence" in r.text
