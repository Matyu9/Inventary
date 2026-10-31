"""Page d'état de l'installation (/sante) et sa version JSON (/health), réservées aux admins."""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Notification, Role
from app.services import health

from .conftest import login, make_user, stamp

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def healthy(app):
    health.record_scheduler_run()


@pytest.mark.parametrize("url", ["/health", "/sante"])
def test_hidden_from_anonymous(client, url):
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("role", [P, E])
@pytest.mark.parametrize("url", ["/health", "/sante"])
def test_hidden_from_non_admins(as_role, client, role, url):
    as_role(role)
    assert client.get(url).status_code == 404


def test_admin_gets_json(as_role, client, healthy):
    as_role(A)
    r = client.get("/health")
    assert r.status_code == 200 and r.is_json
    data = r.get_json()
    assert data["status"] in (health.OK, health.WARN)
    assert data["version"]
    names = {c["name"] for c in data["checks"]}
    assert names == {"database", "migrations", "storage", "scheduler", "email", "trash"}
    by_name = {c["name"]: c for c in data["checks"]}
    assert by_name["migrations"]["status"] == health.OK
    assert by_name["scheduler"]["status"] == health.OK
    assert by_name["storage"]["status"] in (health.OK, health.WARN)


def test_admin_page(as_role, client, healthy):
    as_role(A)
    r = client.get("/sante")
    assert r.status_code == 200
    assert "État de l'installation" in r.text and "Migrations" in r.text and "/health" in r.text


def test_settings_links_to_health(as_role, client):
    as_role(A)
    assert "/sante" in client.get("/parametres").text


def test_custom_role_with_settings_right(app, client, healthy):
    from app.models import Role as RoleModel
    db.session.add(RoleModel(key="regie", name="Régie", permissions=["settings.manage"]))
    db.session.commit()
    login(client, make_user("regie", email="regie@ohn.test"))
    assert client.get("/health").status_code == 200


def test_never_migrated_is_error(as_role, client):
    stamp([])
    as_role(A)
    r = client.get("/health")
    assert r.status_code == 503
    check = {c["name"]: c for c in r.get_json()["checks"]}["migrations"]
    assert check["status"] == health.ERROR and "jamais été migrée" in check["message"]


def test_outdated_migration_is_error(as_role, client, healthy):
    stamp(["682c4e10c226"])  # schéma initial
    as_role(A)
    r = client.get("/health")
    assert r.status_code == 503
    check = {c["name"]: c for c in r.get_json()["checks"]}["migrations"]
    assert "682c4e10c226" in check["message"] and "flask db upgrade" in check["message"]


def test_migration_heads_match_repository(app):
    _, heads = health.migration_heads()
    assert len(heads) == 1


def test_scheduler_missing_or_stale(app, healthy):
    assert health.check_scheduler()["status"] == health.OK
    later = datetime.now() + timedelta(hours=2)
    stale = health.check_scheduler(now=later)
    assert stale["status"] == health.WARN and "arrêté" in stale["message"]
    health._heartbeat_path().unlink()
    assert health.check_scheduler()["status"] == health.WARN


def test_corrupt_heartbeat_is_ignored(app):
    health._heartbeat_path().write_text("pas une date")
    assert health.last_scheduler_run() is None


def test_run_reminders_cli_records_heartbeat(app):
    result = app.test_cli_runner().invoke(args=["run-reminders"])
    assert result.exit_code == 0
    assert health.last_scheduler_run() is not None


def test_unwritable_storage_is_error(app, monkeypatch, tmp_path):
    blocker = tmp_path / "fichier"
    blocker.write_text("x")
    monkeypatch.setitem(app.config, "UPLOAD_DIR", blocker / "uploads")  # parent = un fichier
    assert health.check_storage()["status"] == health.ERROR


def test_low_disk_is_warning(app, monkeypatch):
    class Usage:
        free = 10 * 1024 ** 2
    monkeypatch.setattr(health.shutil, "disk_usage", lambda _: Usage)
    check = health.check_storage()
    assert check["status"] == health.WARN and "10.0 Mo" in check["message"]


def test_email_checks(app, users, monkeypatch):
    assert health.check_email()["status"] == health.WARN
    monkeypatch.setitem(app.config, "SMTP_HOST", "smtp.ohn.test")
    monkeypatch.setitem(app.config, "SMTP_FROM", "inventaire@ohn.test")
    assert health.check_email()["status"] == health.OK
    db.session.add_all(Notification(user_id=users[P].id, message=f"n{i}") for i in range(21))
    db.session.commit()
    check = health.check_email()
    assert check["status"] == health.WARN and "21 emails" in check["message"]


def test_database_error_reported(app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disque débranché")
    monkeypatch.setattr(db.session, "execute", boom)
    check = health.check_database()
    assert check["status"] == health.ERROR and "disque débranché" in check["message"]


def test_overall_status_is_worst(app, healthy, monkeypatch):
    assert health.report()["status"] in (health.OK, health.WARN)
    monkeypatch.setattr(health, "check_email", lambda: health._check("email", "Emails", health.ERROR, "x"))
    assert health.report()["status"] == health.ERROR


def test_size_formatting():
    assert health._size(512) == "512 o"
    assert health._size(2048) == "2.0 Ko"
    assert health._size(5 * 1024 ** 3) == "5.0 Go"


def test_trash_count(app, users):
    from app.models import Category, Item
    from app.services import trash
    assert health.check_trash()["message"] == "Vide"
    item = Item(code="OHN-0001", name="Sax", category=Category(name="Bois"))
    db.session.add(item)
    trash.trash_item(item, users[A])
    db.session.commit()
    assert health.check_trash()["message"].startswith("1 élément,")
