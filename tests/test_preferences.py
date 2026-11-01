"""Préférences d'affichage : page d'accueil après connexion et taille du texte."""
import re
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Role, User
from app.services import preferences

from .conftest import login

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR
CSS = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text()


def display(client, **data):
    return client.post("/mon-compte/affichage", data=data, follow_redirects=True)


def reload(user):
    return db.session.get(User, user.id)


def set_home(user, key):
    user.home = key
    db.session.commit()


# ---------------------------------------------------------------------------
# Page d'accueil
# ---------------------------------------------------------------------------

def test_home_choices_follow_permissions(users):
    assert [k for k, _ in preferences.home_choices(users[A])] == list(preferences.HOME_PAGES)
    assert [k for k, _ in preferences.home_choices(users[E])] == ["dashboard", "mine", "calendar"]


@pytest.mark.parametrize("key,url", [("dashboard", "/"), ("loans", "/prets"), ("mine", "/mes-prets"),
                                     ("scan", "/scan"), ("items", "/objets"), ("calendar", "/calendrier")])
def test_login_lands_on_home(client, users, key, url):
    set_home(users[A], key)
    r = login(client, users[A])
    assert r.status_code == 302 and r.headers["Location"] == url
    assert client.get(url).status_code == 200


def test_default_home_is_dashboard(client, users):
    assert login(client, users[P]).headers["Location"] == "/"


def test_next_wins_over_home(client, users):
    set_home(users[A], "scan")
    r = client.post("/connexion?next=/notifications", data={"email": users[A].email, "password": "motdepasse"})
    assert r.headers["Location"] == "/notifications"


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "/\\evil.example"])
def test_unsafe_next_falls_back_to_home(client, users, target):
    set_home(users[A], "scan")
    r = client.post(f"/connexion?next={target}", data={"email": users[A].email, "password": "motdepasse"})
    assert r.headers["Location"] == "/scan"


@pytest.mark.parametrize("stored", ["scan", "items", "pirate", ""])
def test_forbidden_or_unknown_home_falls_back_to_dashboard(client, users, stored):
    set_home(users[E], stored)
    assert login(client, users[E]).headers["Location"] == "/"


def test_lost_permission_falls_back(client, users):
    set_home(users[P], "scan")
    users[P].role = E
    db.session.commit()
    assert login(client, users[P]).headers["Location"] == "/"


def test_start_route_redirects_to_home(client, users):
    set_home(users[A], "loans")
    login(client, users[A])
    r = client.get("/accueil")
    assert r.status_code == 302 and r.headers["Location"] == "/prets"


def test_start_route_through_login(client, users):
    """Appli installée lancée sans session : connexion, puis la page d'accueil choisie."""
    set_home(users[A], "scan")
    r = client.get("/accueil")
    assert r.status_code == 302 and "/connexion" in r.headers["Location"]
    r = client.post("/connexion?next=/accueil", data={"email": users[A].email, "password": "motdepasse"},
                    follow_redirects=True)
    assert r.request.path == "/scan"


def test_login_page_when_logged_in_goes_home(client, users):
    set_home(users[A], "items")
    login(client, users[A])
    assert client.get("/connexion").headers["Location"] == "/objets"


def test_dashboard_link_still_dashboard(client, users):
    set_home(users[A], "scan")
    login(client, users[A])
    assert client.get("/").status_code == 200  # « / » reste le tableau de bord


def test_manifest_starts_on_home(client):
    assert client.get("/manifest.webmanifest").get_json(force=True)["start_url"] == "/accueil"


# ---------------------------------------------------------------------------
# Formulaire Mon compte → Affichage
# ---------------------------------------------------------------------------

def test_account_page_lists_allowed_homes_only(client, as_role):
    as_role(E)
    html = client.get("/mon-compte").get_data(as_text=True)
    select = re.search(r'<select[^>]*name="home".*?</select>', html, re.S).group(0)
    assert re.findall(r'value="([a-z]+)"', select) == ["dashboard", "mine", "calendar"]
    assert re.search(r'value="dashboard"\s+selected|selected[^>]*value="dashboard"', select)


def test_save_home_and_text_size(client, as_role):
    user = as_role(P)
    r = display(client, theme="auto", text_size="large", home="scan")
    assert "Affichage enregistré." in r.get_data(as_text=True)
    user = reload(user)
    assert (user.text_size, user.home, user.theme) == ("large", "scan", None)
    html = client.get("/mon-compte").get_data(as_text=True)
    assert re.search(r'<option selected value="scan">|<option value="scan" selected>', html)


def test_defaults_stored_as_none(client, as_role):
    user = as_role(A)
    display(client, theme="dark", text_size="xlarge", home="loans")
    display(client, theme="auto", text_size="normal", home="dashboard")
    user = reload(user)
    assert (user.theme, user.text_size, user.home) == (None, None, None)


def test_borrower_cannot_choose_forbidden_home(client, as_role):
    user = as_role(E)
    r = display(client, theme="auto", text_size="normal", home="scan")
    assert "Choix d&#39;affichage invalide." in r.get_data(as_text=True)
    assert reload(user).home is None


@pytest.mark.parametrize("size", ["huge", "", "<b>"])
def test_invalid_text_size_rejected(client, as_role, size):
    user = as_role(A)
    display(client, theme="auto", text_size="large", home="dashboard")
    display(client, theme="auto", text_size=size, home="dashboard")
    assert reload(user).text_size == "large"


def test_missing_fields_keep_current_values(client, as_role):
    """Envoyer seulement le thème ne remet pas à zéro la taille ni l'accueil."""
    user = as_role(A)
    display(client, theme="auto", text_size="xlarge", home="items")
    display(client, theme="dark")
    user = reload(user)
    assert (user.theme, user.text_size, user.home) == ("dark", "xlarge", "items")


# ---------------------------------------------------------------------------
# Taille du texte
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", ["large", "xlarge"])
def test_text_size_on_html(client, as_role, size):
    as_role(E)
    display(client, theme="auto", text_size=size, home="dashboard")
    assert f'data-text="{size}"' in client.get("/").get_data(as_text=True)


def test_no_text_attribute_by_default_or_logged_out(client, as_role):
    as_role(A)
    assert "data-text" not in client.get("/").get_data(as_text=True)
    display(client, theme="auto", text_size="large", home="dashboard")
    client.post("/deconnexion")
    assert "data-text" not in client.get("/connexion").get_data(as_text=True)


def test_text_size_is_per_user(client, users):
    login(client, users[A])
    display(client, theme="auto", text_size="xlarge", home="dashboard")
    client.post("/deconnexion")
    login(client, users[P])
    assert "data-text" not in client.get("/").get_data(as_text=True)


def font_px(size):
    match = re.search(rf'html:root\[lang\]\[data-text="{size}"\] {{ --pico-font-size: (\d+)px;', CSS)
    return int(match.group(1))


def test_css_defines_each_size_growing():
    base = int(re.search(r"--pico-font-size: (\d+)px;", CSS).group(1))
    sizes = [k for k in preferences.TEXT_SIZES if k != preferences.DEFAULT_TEXT_SIZE]
    assert set(re.findall(r'data-text="([a-z]+)"', CSS)) == set(sizes)
    assert base < font_px("large") < font_px("xlarge")


def test_preview_classes_exist_for_each_size():
    for size in preferences.TEXT_SIZES:
        if size != preferences.DEFAULT_TEXT_SIZE:
            assert f".choice.text-sample-{size}" in CSS
