"""Personnalisation de l'interface : nom, sous-titre, couleur et logo de l'association, thème de chacun."""
import io
import re
from pathlib import Path

import pytest
from flask import g
from PIL import Image
from sqlalchemy import event, text

from app.extensions import db
from app.models import Activity, AppSetting, Role, User
from app.services import branding, mailer

from .conftest import login
from .test_calendar_reminders import smtp  # noqa: F401 (fixture)

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR
CSS = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text()


def png(width=600, height=300, mode="RGBA", color=(20, 80, 160, 0), fmt="PNG"):
    buf = io.BytesIO()
    Image.new(mode, (width, height), color).save(buf, fmt)
    buf.seek(0)
    return buf


def upload_logo(client, data=None, name="logo.png"):
    return client.post("/parametres/logo", data={"logo": (data or png(), name)},
                       content_type="multipart/form-data", follow_redirects=True)


def save(client, **data):
    return client.post("/parametres/apparence", data={"name": "", "subtitle": "", "palette": "bordeaux", **data},
                       follow_redirects=True)


def settings_rows():
    return dict(db.session.execute(db.select(AppSetting.key, AppSetting.value)).all())


def fresh():
    """Relit les réglages comme le ferait une nouvelle requête."""
    g.pop("branding", None)
    return branding.current()


# ---------------------------------------------------------------------------
# Valeurs par défaut
# ---------------------------------------------------------------------------

def test_defaults_without_settings(app):
    brand = branding.current()
    assert brand.name == app.config["ASSO_NAME"]
    assert brand.subtitle == "Inventaire"
    assert brand.palette == "bordeaux" and brand.theme_color == "#8b1e3f"
    assert brand.logo is None and brand.logo_url is None


def test_default_page_markup(client, users):
    login(client, users[A])
    html = client.get("/").get_data(as_text=True)
    assert '<html lang="fr" data-palette="bordeaux">' in html
    assert '<meta name="theme-color" content="#8b1e3f">' in html
    assert 'class="brand-mark"' in html and "brand-logo" not in html
    assert "<small>Inventaire</small>" in html


def test_unknown_stored_palette_falls_back(app):
    db.session.add(AppSetting(key="palette", value="fuchsia"))
    db.session.commit()
    assert fresh().palette == "bordeaux"


def test_unreadable_table_falls_back_to_defaults(app):
    db.session.execute(text("DROP TABLE app_setting"))
    db.session.commit()
    brand = fresh()
    assert brand.name == app.config["ASSO_NAME"] and brand.palette == "bordeaux"
    db.session.execute(text("SELECT 1"))  # la session reste utilisable après l'échec
    AppSetting.__table__.create(db.session.connection())
    db.session.commit()


def test_read_once_per_request(client, users, app):
    login(client, users[A])
    queries = []

    def count(conn, cursor, statement, *args):
        if "app_setting" in statement:
            queries.append(statement)

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        g.pop("branding", None)
        client.get("/")
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    assert len(queries) == 1


# ---------------------------------------------------------------------------
# Nom, sous-titre et couleur
# ---------------------------------------------------------------------------

def test_admin_sees_appearance_form(client, users):
    login(client, users[A])
    html = client.get("/parametres").get_data(as_text=True)
    assert 'id="apparence"' in html
    for key in branding.PALETTES:
        assert f'class="swatch" data-palette="{key}"' in html
    assert re.search(r'value="bordeaux"[^>]*checked|checked[^>]*value="bordeaux"', html)


def test_save_name_subtitle_palette(client, users, app):
    login(client, users[A])
    r = save(client, name="  Harmonie de Nulle-Part ", subtitle="Matériel", palette="vert")
    assert "Apparence enregistrée." in r.get_data(as_text=True)
    assert settings_rows() == {"name": "Harmonie de Nulle-Part", "subtitle": "Matériel", "palette": "vert"}

    html = client.get("/").get_data(as_text=True)
    assert 'data-palette="vert"' in html
    assert '<meta name="theme-color" content="#1f6b4a">' in html
    assert "<strong>Harmonie de Nulle-Part</strong><small>Matériel</small>" in html
    assert "· Harmonie de Nulle-Part</title>" in html
    entry = db.session.scalar(db.select(Activity).where(Activity.action == "settings.branding"))
    assert "Harmonie de Nulle-Part" in entry.message and "Vert sapin" in entry.message


def test_empty_values_restore_defaults(client, users, app):
    login(client, users[A])
    save(client, name="Autre nom", subtitle="Autre", palette="bleu")
    save(client, name="   ", subtitle="", palette="bordeaux")
    assert settings_rows() == {}
    brand = fresh()
    assert brand.name == app.config["ASSO_NAME"] and brand.subtitle == "Inventaire"


@pytest.mark.parametrize("data", [{"palette": "fuchsia"}, {"palette": ""}, {"name": "x" * 81}, {"subtitle": "y" * 61}])
def test_invalid_input_is_rejected(client, users, data):
    login(client, users[A])
    r = save(client, **data)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Apparence enregistrée." not in html and 'id="apparence"' in html
    assert settings_rows() == {}


def test_save_rejects_unknown_palette_in_service(app):
    with pytest.raises(ValueError):
        branding.save("Nom", "", "fuchsia")


def test_name_used_on_public_pages(client, users):
    login(client, users[A])
    save(client, name="Fanfare du Lac", palette="violet")
    client.post("/deconnexion")
    html = client.get("/connexion").get_data(as_text=True)
    assert "<strong>Fanfare du Lac</strong>" in html and 'data-palette="violet"' in html
    assert "data-theme" not in html  # pas de thème choisi sans connexion


def test_manifest_follows_branding(client, users):
    login(client, users[A])
    save(client, name="Fanfare du Lac", palette="terracotta")
    data = client.get("/manifest.webmanifest").get_json(force=True)
    assert data["name"] == "Inventaire — Fanfare du Lac"
    assert data["theme_color"] == "#a3441f"


def test_email_uses_custom_name(client, users, smtp):
    login(client, users[A])
    save(client, name="Fanfare du Lac")
    mailer.send(users[A].email, *mailer.compose_test(users[A]))
    message = smtp.sent[-1]
    assert message["From"].addresses[0].display_name == "Inventaire Fanfare du Lac"
    assert "Fanfare du Lac" in message.get_body("plain").get_content()


def test_ical_uses_custom_name(client, users):
    login(client, users[A])
    save(client, name="Fanfare du Lac")
    feed = client.get(f"/cal/{users[A].ical_token}.ics")
    assert "X-WR-CALNAME:Prêts — Fanfare du Lac" in feed.get_data(as_text=True)


@pytest.mark.parametrize("role", [P, E])
def test_only_settings_managers_can_change_appearance(client, as_role, role):
    as_role(role)
    assert client.post("/parametres/apparence", data={"name": "Pirate", "palette": "bleu"}).status_code == 403
    assert client.post("/parametres/logo", data={"logo": (png(), "l.png")},
                       content_type="multipart/form-data").status_code == 403
    assert client.post("/parametres/logo/supprimer").status_code == 403
    assert settings_rows() == {}


def test_anonymous_cannot_change_appearance(client, users):
    r = client.post("/parametres/apparence", data={"name": "Pirate", "palette": "bleu"})
    assert r.status_code == 302 and "/connexion" in r.headers["Location"]
    assert settings_rows() == {}


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------

def logo_path(app):
    return Path(app.config["UPLOAD_DIR"]) / fresh().logo


def logo_url():
    return "/logo/" + fresh().logo.rsplit("/", 1)[-1]


def test_upload_logo_keeps_transparency_and_is_reduced(client, users, app):
    login(client, users[A])
    r = upload_logo(client)
    assert "Logo enregistré." in r.get_data(as_text=True)
    path = logo_path(app)
    with Image.open(path) as image:
        assert image.format == "PNG" and image.mode == "RGBA"
        assert image.size == (256, 128)  # proportions conservées
        assert image.getpixel((10, 10))[3] == 0
    assert db.session.scalar(db.select(Activity).where(Activity.action == "settings.logo"))


def test_small_logo_is_not_enlarged(client, users, app):
    login(client, users[A])
    upload_logo(client, png(64, 64))
    with Image.open(logo_path(app)) as image:
        assert image.size == (64, 64)


def test_jpeg_logo_accepted(client, users, app):
    login(client, users[A])
    upload_logo(client, png(300, 300, mode="RGB", color=(10, 10, 10), fmt="JPEG"), "logo.jpg")
    assert logo_path(app).suffix == ".png"


def test_logo_shown_everywhere(client, users, app):
    login(client, users[A])
    upload_logo(client)
    url = logo_url()
    html = client.get("/").get_data(as_text=True)
    assert f'<img class="brand-logo" src="{url}"' in html
    assert f'<link rel="icon" href="{url}">' in html
    assert 'class="brand-mark"' not in html
    client.post("/deconnexion")
    assert f'src="{url}"' in client.get("/connexion").get_data(as_text=True)


def test_logo_served_publicly_with_long_cache(client, users, app):
    login(client, users[A])
    upload_logo(client)
    url = logo_url()
    client.post("/deconnexion")
    r = client.get(url)
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert "public" in r.headers["Cache-Control"] and "max-age=2592000" in r.headers["Cache-Control"]


def test_logo_not_blocked_by_pending_password_change(client, users, app):
    login(client, users[A])
    upload_logo(client)
    url = logo_url()
    users[E].must_change_password = True
    db.session.commit()
    client.post("/deconnexion")
    login(client, users[E])
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("name", ["../inventaire.db", "abc.png", "0" * 32 + ".jpg", "0" * 32 + ".png"])
def test_logo_route_rejects_bad_or_missing_names(client, name):
    assert client.get(f"/logo/{name}").status_code == 404


def test_replace_logo_deletes_old_file(client, users, app):
    login(client, users[A])
    upload_logo(client)
    old = logo_path(app)
    upload_logo(client, png(100, 100))
    new = logo_path(app)
    assert new != old and new.exists() and not old.exists()


def test_remove_logo(client, users, app):
    login(client, users[A])
    upload_logo(client)
    path = logo_path(app)
    r = client.post("/parametres/logo/supprimer", follow_redirects=True)
    assert "Logo retiré." in r.get_data(as_text=True)
    assert not path.exists() and fresh().logo is None and "logo" not in settings_rows()


def test_remove_logo_without_logo_is_harmless(client, users):
    login(client, users[A])
    assert client.post("/parametres/logo/supprimer").status_code == 302


@pytest.mark.parametrize("data,name", [(io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'/>"), "logo.svg"),
                                       (io.BytesIO(b"pas une image"), "logo.png")])
def test_invalid_logo_rejected(client, users, app, data, name):
    login(client, users[A])
    r = upload_logo(client, data, name)
    assert "image valide" in r.get_data(as_text=True)
    assert fresh().logo is None
    folder = Path(app.config["UPLOAD_DIR"]) / "logo"
    assert not folder.exists() or not any(folder.iterdir())


def test_missing_logo_file_warns(client, users):
    login(client, users[A])
    r = client.post("/parametres/logo", data={}, content_type="multipart/form-data", follow_redirects=True)
    assert "Choisissez une image." in r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Thème de chacun (Mon compte)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", [A, P, E])
def test_choose_dark_then_light_then_auto(client, as_role, role):
    user = as_role(role)
    html = client.get("/mon-compte").get_data(as_text=True)
    assert 'id="affichage"' in html and "data-theme" not in html

    r = client.post("/mon-compte/affichage", data={"theme": "dark"}, follow_redirects=True)
    assert "Affichage enregistré." in r.get_data(as_text=True)
    assert db.session.get(User, user.id).theme == "dark"
    assert 'data-palette="bordeaux" data-theme="dark"' in client.get("/").get_data(as_text=True)

    client.post("/mon-compte/affichage", data={"theme": "light"})
    assert 'data-theme="light"' in client.get("/").get_data(as_text=True)

    client.post("/mon-compte/affichage", data={"theme": "auto"})
    assert db.session.get(User, user.id).theme is None
    assert "data-theme" not in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("value", ["sepia", "", "<script>"])
def test_invalid_theme_rejected(client, users, value):
    login(client, users[E])
    client.post("/mon-compte/affichage", data={"theme": "dark"})
    r = client.post("/mon-compte/affichage", data={"theme": value}, follow_redirects=True)
    assert "Choix de thème invalide." in r.get_data(as_text=True)
    assert db.session.get(User, users[E].id).theme == "dark"


def test_theme_is_per_user(client, users):
    login(client, users[E])
    client.post("/mon-compte/affichage", data={"theme": "dark"})
    client.post("/deconnexion")
    login(client, users[P])
    assert "data-theme" not in client.get("/").get_data(as_text=True)


def test_current_choice_is_checked(client, users):
    login(client, users[E])
    client.post("/mon-compte/affichage", data={"theme": "dark"})
    html = client.get("/mon-compte").get_data(as_text=True)
    assert re.search(r'<input[^>]*checked[^>]*value="dark"|<input[^>]*value="dark"[^>]*checked', html)


def test_anonymous_cannot_set_theme(client):
    r = client.post("/mon-compte/affichage", data={"theme": "dark"})
    assert r.status_code == 302 and "/connexion" in r.headers["Location"]


# ---------------------------------------------------------------------------
# Feuille de style : palettes et thème sombre
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", list(branding.PALETTES))
def test_each_palette_defined_in_css_for_both_themes(key):
    light = re.search(rf'html:root\[data-palette="{key}"\]:not\(\[data-theme="dark"\]\).*?--brand: (#[0-9a-f]+)', CSS)
    assert light and light.group(1) == branding.PALETTES[key][1]
    assert f'html:root[data-palette="{key}"]:not([data-theme="light"])' in CSS
    assert f'html:root[data-palette="{key}"][data-theme="dark"]' in CSS


def test_no_css_palette_without_python_entry():
    assert set(re.findall(r'data-palette="([a-z]+)"', CSS)) == set(branding.PALETTES)


def test_explicit_dark_tokens_match_system_dark_tokens():
    """Le thème sombre choisi dans Mon compte et celui du système doivent rester identiques."""
    media = re.search(r'@media \(prefers-color-scheme: dark\) \{\n  html:root:not\(\[data-theme="light"\]\) \{\n(.*?)\n  \}',
                      CSS, re.S).group(1)
    explicit = re.search(r'\nhtml:root\[data-theme="dark"\] \{\n(.*?)\n\}', CSS, re.S).group(1)
    normalize = lambda block: [line.strip() for line in block.splitlines()]  # noqa: E731
    assert normalize(media) == normalize(explicit)
    assert len(normalize(explicit)) > 20


def test_brand_color_only_in_tokens():
    """Ailleurs, la couleur passe par var(--brand) : sinon elle ne suivrait pas la palette choisie."""
    assert "139, 30, 63" not in CSS
    assert CSS.count("#8b1e3f") == 2  # jeton par défaut et palette « bordeaux »


def test_layout_tokens_apply_in_every_theme():
    """Largeurs, rayons et police ne sont déclarés qu'une fois : leur bloc ne doit exclure aucun thème."""
    base = re.search(r'\n(html:root[^{]*) \{\n  --brand: #8b1e3f;(.*?)\n\}', CSS, re.S)
    assert "data-theme" not in base.group(1)
    for token in ("--sidebar-w", "--radius", "--font", "--pico-spacing"):
        assert f"{token}:" in base.group(2)
