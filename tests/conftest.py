import os
from functools import partial

from hypothesis import settings
from sqlalchemy import text

import pytest
from werkzeug.security import generate_password_hash

import app.models as models

# HYPOTHESIS_PROFILE=deep pytest tests/test_properties.py : 1000 cas par propriété.
settings.register_profile("deep", max_examples=1000, deadline=None)
settings.register_profile("default", max_examples=60, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import Role, User


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """scrypt est volontairement lent : inutile en test."""
    monkeypatch.setattr(models, "generate_password_hash", partial(generate_password_hash, method="pbkdf2:sha256:1"))


@pytest.fixture(autouse=True)
def reset_login_throttle():
    from app.services import throttle
    throttle.reset_all()
    yield
    throttle.reset_all()


_HEADS = []


def stamp(revisions):
    """La base de test est créée sans Alembic (create_all) : on y inscrit la version voulue."""
    db.session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
    db.session.execute(text("DELETE FROM alembic_version"))
    for rev in revisions:
        db.session.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": rev})
    db.session.commit()


def stamp_head():
    """create_all produit le schéma le plus récent : la base est donc « à jour » pour la page de santé."""
    if not _HEADS:
        from app.services.health import migration_heads
        _HEADS.extend(migration_heads()[1])
    stamp(_HEADS)


@pytest.fixture
def app(tmp_path):
    class Cfg(TestConfig):
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "uploads"
        ITEM_CODE_PREFIX = "OHN"

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        stamp_head()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(role, email=None, active=True, password="motdepasse"):
    user = User(first_name=role.capitalize(), last_name="Test", email=email or f"{role}@ohn.test",
                role=role, active=active)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def users(app):
    return {r: make_user(r) for r in Role.BUILTIN}


def login(client, user, password="motdepasse"):
    return client.post("/connexion", data={"email": user.email, "password": password})


@pytest.fixture
def as_role(client, users):
    """as_role("prof") : connecte le client avec le compte du rôle et renvoie l'utilisateur."""
    def _login(role):
        client.post("/deconnexion")
        login(client, users[role])
        return users[role]
    return _login
