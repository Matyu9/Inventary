"""Correctifs de sécurité de la phase 10 : chacun a son test de non-régression."""
import io
import os

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import create_app
from app.blueprints import auth
from app.config import Config, TestConfig
from app.extensions import db
from app.models import Category, Item, Loan, LoanLine, LoanType, Role

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
