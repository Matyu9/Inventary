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

    downgrade(MIGRATIONS, revision="c4d8e1f2a6b9")  # avant les rôles personnalisés
    users, sessions = run_sql(path, "SELECT role FROM user", "SELECT count(*) FROM login_session")
    assert users == [("emprunteur",)] and sessions == [(1,)]
    with pytest.raises(sqlite3.IntegrityError):
        run_sql(path, "UPDATE user SET role = 'regie' WHERE id = 1")
    upgrade(MIGRATIONS)


def test_account_management_migration_keeps_linked_rows(migrated_app):
    """Ajouter les colonnes de `user` et `login_session` ne recrée pas `user` : les données liées restent."""
    _app, path = migrated_app
    upgrade(MIGRATIONS, revision="d5f9a2b3c7e1")
    run_sql(path,
            "INSERT INTO user (id, first_name, last_name, email, role, active, ical_token) "
            "VALUES (1, 'Alice', 'Martin', 'alice@ohn.test', 'admin', 1, 'tok')",
            "INSERT INTO login_session (id, user_id, created_at) VALUES ('session-1', 1, '2026-10-31')",
            "INSERT INTO activity (user_id, action, message) VALUES (1, 'test', 'référence user')")
    upgrade(MIGRATIONS)
    rows = run_sql(path, "SELECT id, last_login_at, archived_at FROM user",
                   "SELECT id, last_seen_at, user_agent, ip FROM login_session",
                   "SELECT user_id, target_user_id FROM activity", "PRAGMA foreign_key_check")
    assert rows == [[(1, None, None)], [("session-1", None, None, None)], [(1, None)], []]
    run_sql(path, "INSERT INTO account_token (id, user_id, purpose, created_at, expires_at) "
                  "VALUES ('h', 1, 'invite', '2026-10-31', '2026-11-07')",
            "UPDATE activity SET target_user_id = 1")
    downgrade(MIGRATIONS, revision="d5f9a2b3c7e1")
    users, sessions, activity = run_sql(path, "SELECT id FROM user", "SELECT id FROM login_session",
                                        "SELECT user_id FROM activity")
    assert users == [(1,)] and sessions == [("session-1",)] and activity == [(1,)]
    upgrade(MIGRATIONS)


def test_trash_migration_keeps_linked_rows(migrated_app):
    """Les colonnes de la corbeille s'ajoutent sans recréer `item` ni `kit` (référencées par les photos, prêts…)."""
    _app, path = migrated_app
    upgrade(MIGRATIONS, revision="f3b7d9e1a2c4")
    run_sql(path,
            "INSERT INTO category (id, name, type) VALUES (1, 'Bois', 'materiel')",
            "INSERT INTO kit (id, name, qr_token) VALUES (1, 'Valise', 'k1')",
            "INSERT INTO item (id, code, name, category_id, condition, status, qr_token, tracking, quantity, kit_id) "
            "VALUES (1, 'OHN-0001', 'Sax', 1, 'bon', 'disponible', 'i1', 'individuel', 1, 1)",
            "INSERT INTO photo (item_id, filename, thumbnail) VALUES (1, 'p.jpg', 'p_t.jpg')")
    upgrade(MIGRATIONS, revision="a9d3e5f7b2c8")
    items, kits, photos, fk_errors = run_sql(
        path, "SELECT id, kit_id, deleted_at, deleted_by_id FROM item",
        "SELECT id, deleted_at, trashed_item_ids FROM kit", "SELECT item_id FROM photo", "PRAGMA foreign_key_check")
    assert items == [(1, 1, None, None)] and kits == [(1, None, None)]
    assert photos == [(1,)] and fk_errors == []
    downgrade(MIGRATIONS, revision="f3b7d9e1a2c4")
    items, photos, columns = run_sql(path, "SELECT id, kit_id FROM item", "SELECT item_id FROM photo",
                                     "SELECT name FROM pragma_table_info('item') WHERE name = 'deleted_at'")
    assert items == [(1, 1)] and photos == [(1,)] and columns == []
    upgrade(MIGRATIONS)


def test_documents_migration_grants_prof_rights(migrated_app):
    """Le rôle Professeur enregistré reçoit les droits sur les documents ; le retour arrière les retire partout."""
    import json
    _app, path = migrated_app
    upgrade(MIGRATIONS, revision="a9d3e5f7b2c8")
    run_sql(path, "INSERT INTO role (key, name, permissions) VALUES ('regie', 'Régie', '[\"inventory.view\"]')")
    upgrade(MIGRATIONS, revision="b4e8c2d6f9a1")
    (prof,), = run_sql(path, "SELECT permissions FROM role WHERE key = 'prof'")
    assert {"document.view", "document.edit"} <= set(json.loads(prof[0]))
    run_sql(path, "UPDATE role SET permissions = '[\"inventory.view\", \"document.view\"]' WHERE key = 'regie'")
    downgrade(MIGRATIONS, revision="a9d3e5f7b2c8")
    rows = run_sql(path, "SELECT key, permissions FROM role WHERE key IN ('prof', 'regie')")[0]
    for _key, perms in rows:
        assert not {"document.view", "document.edit"} & set(json.loads(perms))
    upgrade(MIGRATIONS)
