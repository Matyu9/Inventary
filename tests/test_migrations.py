"""Migrations Alembic jouées sur une base qui contient déjà des données liées entre elles.

Les autres tests créent le schéma avec create_all : ils ne voient pas une migration qui, sous SQLite,
recrée une table référencée par des clés étrangères (refusé avec PRAGMA foreign_keys=ON).
"""
import sqlite3
from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade

from app import create_app
from app.config import TestConfig

MIGRATIONS = str(Path(__file__).resolve().parent.parent / "migrations")


@pytest.fixture
def migrated_app(tmp_path):
    class Cfg(TestConfig):
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "uploads"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'inventaire.db'}"

    app = create_app(Cfg)
    with app.app_context():
        yield app, tmp_path / "inventaire.db"


def run_sql(path, *statements):
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        rows = [con.execute(s).fetchall() for s in statements]
        con.commit()
        return rows
    finally:
        con.close()


def test_upgrade_and_downgrade_with_referenced_users(migrated_app):
    _app, path = migrated_app
    upgrade(MIGRATIONS, revision="ecf59aed6e07")
    run_sql(path,
            "INSERT INTO user (id, first_name, last_name, email, role, active, ical_token) "
            "VALUES (1, 'Alice', 'Martin', 'alice@ohn.test', 'admin', 1, 'tok')",
            "INSERT INTO activity (user_id, action, message) VALUES (1, 'test', 'référence user')")

    upgrade(MIGRATIONS)
    downgrade(MIGRATIONS, revision="-1")
    upgrade(MIGRATIONS)

    users, fk_errors, columns = run_sql(
        path, "SELECT id, email, role_from_oidc FROM user", "PRAGMA foreign_key_check",
        "SELECT name FROM pragma_table_info('user') WHERE name = 'oidc_sub'")
    assert users == [(1, "alice@ohn.test", 0)]
    assert fk_errors == [] and columns == [("oidc_sub",)]


def test_oidc_sub_is_unique(migrated_app):
    _app, path = migrated_app
    upgrade(MIGRATIONS)
    insert = ("INSERT INTO user (first_name, last_name, role, active, ical_token, oidc_sub) "
              "VALUES ('A', 'B', 'emprunteur', 1, '{}', 'meme-sub')")
    run_sql(path, insert.format("t1"))
    with pytest.raises(sqlite3.IntegrityError):
        run_sql(path, insert.format("t2"))


def test_custom_roles_migration_keeps_linked_rows(migrated_app):
    """Le retrait de la contrainte sur user.role ne recrée pas `user` : sessions et notifications restent."""
    _app, path = migrated_app
    upgrade(MIGRATIONS, revision="c4d8e1f2a6b9")
    run_sql(path,
            "INSERT INTO user (id, first_name, last_name, email, role, active, ical_token) "
            "VALUES (1, 'Alice', 'Martin', 'alice@ohn.test', 'prof', 1, 'tok')",
            "INSERT INTO login_session (id, user_id, created_at) VALUES ('session-1', 1, '2026-10-31')",
            "INSERT INTO notification (user_id, message, read, emailed) VALUES (1, 'bonjour', 0, 0)")
    with pytest.raises(sqlite3.IntegrityError):
        run_sql(path, "UPDATE user SET role = 'regie' WHERE id = 1")

    upgrade(MIGRATIONS)
    roles, = run_sql(path, "SELECT key, name FROM role ORDER BY key")
    assert roles == [("admin", "Administrateur"), ("emprunteur", "Emprunteur"), ("prof", "Professeur")]
    run_sql(path, "INSERT INTO role (key, name, permissions) VALUES ('regie', 'Régie', '[]')",
            "UPDATE user SET role = 'regie' WHERE id = 1")
    counts = run_sql(path, "SELECT count(*) FROM login_session", "SELECT count(*) FROM notification",
                     "PRAGMA integrity_check", "PRAGMA foreign_key_check")
    assert counts == [[(1,)], [(1,)], [("ok",)], []]

    downgrade(MIGRATIONS, revision="-1")
    users, sessions = run_sql(path, "SELECT role FROM user", "SELECT count(*) FROM login_session")
    assert users == [("emprunteur",)] and sessions == [(1,)]
    with pytest.raises(sqlite3.IntegrityError):
        run_sql(path, "UPDATE user SET role = 'regie' WHERE id = 1")
    upgrade(MIGRATIONS)
