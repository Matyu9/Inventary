"""Filtres, journal d'activité, CLI, pages d'erreur, CSRF."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.filters import date_fr, euros
from app.models import Activity, Category, Item, Location, Role, User
from app.services.activity import diff, log

from .conftest import login, make_user


def test_date_fr():
    assert date_fr(date(2026, 3, 5)) == "5 mars 2026"
    assert date_fr(datetime(2026, 12, 25, 18, 30), with_time=True) == "25 déc. 2026 à 18:30"
    assert date_fr(None) == ""


def test_euros():
    assert euros(Decimal("1234.5")) == "1 234,50 €"
    assert euros(0) == "0,00 €"
    assert euros(None) == ""


def test_diff_applies_and_reports_changes():
    loc = Location(name="Ancien", description="x")
    changes = diff(loc, ["name", "description"], {"name": "Nouveau", "description": "x", "parent_id": 3})
    assert loc.name == "Nouveau"
    assert changes == {"name": ["Ancien", "Nouveau"]}


def test_diff_stringifies_complex_values():
    item = Item(purchase_price=Decimal("10.00"))
    assert diff(item, ["purchase_price"], {"purchase_price": Decimal("12.50")}) == {
        "purchase_price": ["10.00", "12.50"]}


def test_log_without_request_user(app):
    cat = Category(name="Bois")
    item = Item(code="OHN-0001", name="Sax", category=cat)
    db.session.add(item)
    db.session.flush()
    log("item.create", "Création", item=item)
    db.session.commit()
    entry = db.session.scalar(db.select(Activity))
    assert entry.item_id == item.id and entry.user is None and entry.changes is None


def test_cli_seed_idempotent(app):
    runner = app.test_cli_runner()
    assert "élément(s) créé(s)" in runner.invoke(args=["seed"]).output
    first = db.session.scalar(db.select(db.func.count(Category.id)))
    assert "0 élément(s)" in runner.invoke(args=["seed"]).output
    assert db.session.scalar(db.select(db.func.count(Category.id))) == first
    assert db.session.scalar(db.select(db.func.count(Location.id))) == 1


def test_cli_create_admin_and_promote(app):
    runner = app.test_cli_runner()
    make_user(Role.PROF, email="chef@ohn.test")
    result = runner.invoke(args=["create-admin", "--email", "Chef@OHN.test", "--first-name", "A",
                                 "--last-name", "B", "--password", "nouveau123"])
    assert result.exit_code == 0
    users = db.session.scalars(db.select(User)).all()
    assert len(users) == 1 and users[0].is_admin and users[0].check_password("nouveau123")


def test_cli_run_reminders(app):
    assert app.test_cli_runner().invoke(args=["run-reminders"]).exit_code == 0


def test_404_page(client, users):
    login(client, users[Role.ADMIN])
    r = client.get("/nexiste-pas")
    assert r.status_code == 404 and "Page introuvable" in r.text


def test_logout_requires_post(client, users):
    login(client, users[Role.ADMIN])
    assert client.get("/deconnexion").status_code == 405
    client.post("/deconnexion")
    assert client.get("/").status_code == 302


def test_change_password(client, users):
    admin = users[Role.ADMIN]
    login(client, admin)
    r = client.post("/mot-de-passe", data={"current": "faux", "new": "nouveau123", "confirm": "nouveau123"})
    assert "incorrect" in r.text
    r = client.post("/mot-de-passe", data={"current": "motdepasse", "new": "court", "confirm": "court"})
    assert r.status_code == 200 and not admin.check_password("court")
    r = client.post("/mot-de-passe", data={"current": "motdepasse", "new": "nouveau123", "confirm": "autre123"})
    assert "ne correspondent pas" in r.text
    r = client.post("/mot-de-passe", data={"current": "motdepasse", "new": "nouveau123", "confirm": "nouveau123"})
    assert r.status_code == 302 and admin.check_password("nouveau123")


def test_login_case_insensitive_email(client, users):
    r = client.post("/connexion", data={"email": "  ADMIN@ohn.test ", "password": "motdepasse"})
    assert r.status_code == 302


def test_logged_in_user_redirected_from_login(client, users):
    login(client, users[Role.ADMIN])
    assert client.get("/connexion").status_code == 302


def test_csrf_enforced_when_enabled(tmp_path):
    class Cfg(TestConfig):
        WTF_CSRF_ENABLED = True
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "uploads"

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        make_user(Role.ADMIN)
        r = app.test_client().post("/connexion", data={"email": "admin@ohn.test", "password": "motdepasse"})
        assert r.status_code == 400


def test_fold():
    from app.extensions import fold
    assert fold("Flûte Traversière") == "flute traversiere"
    assert fold("ÉTUDE") == "etude" and fold(None) is None


@pytest.mark.parametrize("text,q,found", [
    ("Flûte", "flu", True), ("Flûte", "_", False), ("Flûte", "%", False), ("100 %", "0 %", True),
    ("a_b", "a_b", True), ("axb", "a_b", False), ("c:\\x", "\\", True), ("cx", "\\", False),
])
def test_fold_contains_escapes_wildcards(app, text, q, found):
    from app.extensions import fold_contains
    assert db.session.scalar(db.select(fold_contains(db.literal(text), q))) == found


def test_fold_available_in_sql(app):
    assert db.session.scalar(db.select(db.func.fold("Œuvre Élégante"))) == "œuvre elegante"
