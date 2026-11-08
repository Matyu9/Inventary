"""Zones réservées à certaines adresses IP : administration, connexion de secours, avis de déconnexion d'Adhérents."""
import html
import ipaddress

import pytest

from app.extensions import db
from app.models import Activity, AppSetting, Role
from app.services import ip_access

from .conftest import login, make_user

INSIDE, OUTSIDE = "192.168.1.20", "203.0.113.9"
ADMIN_PAGES = ["/parametres", "/utilisateurs", "/roles", "/journal", "/sante", "/health", "/champs-perso",
               "/utilisateurs/rattachement-oidc"]


def restrict(zone, text):
    ip_access.save(zone, text)
    db.session.commit()


def from_ip(client, ip):
    client.environ_base["REMOTE_ADDR"] = ip
    return client


@pytest.fixture
def admin_client(client, as_role):
    as_role(Role.ADMIN)
    return client


# ---------------------------------------------------------------------------
# Lecture des listes
# ---------------------------------------------------------------------------

def test_parse_accepts_addresses_ranges_separators_and_comments():
    networks = ip_access.parse("192.168.1.0/24, 10.0.0.1  # bureau\n\n2001:db8::/32\n10.0.0.1\n192.168.1.7/24")
    assert networks == [ipaddress.ip_network("192.168.1.0/24"), ipaddress.ip_network("10.0.0.1/32"),
                        ipaddress.ip_network("2001:db8::/32")]


def test_normalize_one_entry_per_line_without_comments():
    assert ip_access.normalize(" 10.0.0.1 ,192.168.1.9/24 # local \n # rien\n") == "10.0.0.1\n192.168.1.0/24"
    assert ip_access.normalize("  \n# seulement un commentaire") == ""


@pytest.mark.parametrize("text", ["localhost", "300.1.1.1", "10.0.0.0/33", "192.168.1.*", "10.0.0.1-10.0.0.5"])
def test_parse_rejects_what_is_not_an_address(text):
    with pytest.raises(ip_access.InvalidEntry, match="ni une adresse IP"):
        ip_access.parse(text)


def test_parse_limits_the_number_of_entries():
    with pytest.raises(ip_access.InvalidEntry, match="au maximum"):
        ip_access.parse("\n".join(f"10.0.{i // 250}.{i % 250}" for i in range(ip_access.MAX_ENTRIES + 1)))


@pytest.mark.parametrize("ip,expected", [
    ("192.168.1.200", True), ("192.168.2.1", False), ("10.0.0.1", True), ("10.0.0.2", False),
    ("::ffff:192.168.1.5", True),  # IPv4 vue par un serveur IPv6
    ("2001:db8::1", True), ("2001:db9::1", False), ("fe80::1%eth0", False),
    ("", False), (None, False), ("pas-une-ip", False),
])
def test_matches(ip, expected):
    assert ip_access.matches(ip_access.parse("192.168.1.0/24\n10.0.0.1\n2001:db8::/32"), ip) is expected


@pytest.mark.parametrize("endpoint,zone", [
    ("users.index", "admin"), ("users.edit", "admin"), ("users.oidc_links", "admin"), ("roles.index", "admin"),
    ("settings.index", "admin"), ("settings.save_ip_access", "admin"), ("journal.index", "admin"),
    ("health.page", "admin"), ("health.as_json", "admin"), ("referentiel.custom_fields", "admin"),
    ("referentiel.delete_custom_field", "admin"),
    ("users.notifications", None), ("users.avatar_file", None), ("settings.logo_file", None),
    ("referentiel.categories", None), ("items.index", None), ("auth.login", None), ("api.items", None),
    ("static", None), (None, None),
])
def test_zone_of(endpoint, zone):
    assert ip_access.zone_of(endpoint) == zone


def test_every_admin_endpoint_really_exists(app):
    endpoints = set(app.view_functions)
    assert ip_access.ADMIN_ENDPOINTS <= endpoints and ip_access.NOT_ADMIN <= endpoints
    assert {e.split(".")[0] for e in endpoints} >= ip_access.ADMIN_BLUEPRINTS


def test_empty_list_means_open(app):
    with app.test_request_context(environ_base={"REMOTE_ADDR": OUTSIDE}):
        assert all(ip_access.allowed(zone) for zone in ip_access.ZONES)


def test_damaged_stored_value_closes_the_zone(app):
    db.session.add(AppSetting(key="ip_allow.admin", value="n'importe quoi"))
    db.session.commit()
    with app.test_request_context(environ_base={"REMOTE_ADDR": INSIDE}):
        assert not ip_access.allowed("admin") and ip_access.allowed("rescue")


def test_save_rejects_unknown_zone(app):
    with pytest.raises(KeyError):
        ip_access.save("api", "10.0.0.1")


# ---------------------------------------------------------------------------
# Zone Administration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_open_without_restriction(admin_client, path):
    assert from_ip(admin_client, OUTSIDE).get(path).status_code == 200


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_refused_from_other_addresses(admin_client, path):
    restrict("admin", "192.168.1.0/24")
    assert from_ip(admin_client, OUTSIDE).get(path).status_code == 403
    assert from_ip(admin_client, INSIDE).get(path).status_code == 200


def test_admin_actions_refused_too(admin_client, users):
    restrict("admin", "192.168.1.0/24")
    prof = users[Role.PROF]
    response = from_ip(admin_client, OUTSIDE).post(f"/utilisateurs/{prof.id}/archiver")
    assert response.status_code == 403
    assert db.session.get(type(prof), prof.id).active
    assert from_ip(admin_client, OUTSIDE).post("/parametres/acces-ip", data={"admin": ""}).status_code == 403
    assert ip_access.lists()["admin"] == "192.168.1.0/24"


def test_rest_of_the_app_stays_reachable(admin_client):
    restrict("admin", "192.168.1.0/24")
    client = from_ip(admin_client, OUTSIDE)
    for path in ("/", "/objets", "/notifications", "/categories", "/mon-compte", "/prets"):
        assert client.get(path).status_code == 200, path


def test_admin_menu_hidden_outside(admin_client):
    restrict("admin", "192.168.1.0/24")
    outside = from_ip(admin_client, OUTSIDE).get("/objets").text
    assert ">Administration<" not in outside and 'href="/parametres"' not in outside
    inside = from_ip(admin_client, INSIDE).get("/objets").text
    assert ">Administration<" in inside and 'href="/parametres"' in inside


def test_new_borrower_link_hidden_outside(admin_client):
    restrict("admin", "192.168.1.0/24")
    assert "Nouvelle fiche emprunteur" not in from_ip(admin_client, OUTSIDE).get("/prets/nouveau").text
    assert "Nouvelle fiche emprunteur" in from_ip(admin_client, INSIDE).get("/prets/nouveau").text


def test_prof_people_pages_follow_the_zone(client, as_role):
    as_role(Role.PROF)
    restrict("admin", "192.168.1.0/24")
    assert from_ip(client, OUTSIDE).get("/utilisateurs").status_code == 403
    assert from_ip(client, INSIDE).get("/utilisateurs").status_code == 200


def test_signed_in_person_sees_why_access_is_refused(admin_client):
    restrict("admin", "192.168.1.0/24")
    response = from_ip(admin_client, OUTSIDE).get("/parametres")
    page = html.unescape(response.text)
    assert response.status_code == 403
    assert "Accès refusé depuis ce réseau" in page and "« Administration »" in page and f"({OUTSIDE})" in page
    assert "Tableau de bord" in page  # page d'erreur habituelle, menu compris
    assert "Accès par adresse IP" not in page  # rien de la page protégée


def test_refusal_page_for_ipv6_address(admin_client):
    restrict("admin", "192.168.1.0/24")
    response = from_ip(admin_client, "2001:db8::1").get("/journal")
    assert response.status_code == 403 and "(2001:db8::1)" in response.text


def test_anonymous_visitor_gets_404_not_the_login_page(client):
    restrict("admin", "192.168.1.0/24")
    assert from_ip(client, OUTSIDE).get("/utilisateurs").status_code == 404
    assert from_ip(client, INSIDE).get("/utilisateurs").status_code == 302


def test_refusal_logged_for_signed_in_people_only(client, as_role):
    restrict("admin", "192.168.1.0/24")
    from_ip(client, OUTSIDE).get("/journal")
    assert not db.session.scalars(db.select(Activity).filter_by(action="security.ip_denied")).all()
    as_role(Role.ADMIN)
    from_ip(client, OUTSIDE).get("/journal?q=x")
    (entry,) = db.session.scalars(db.select(Activity).filter_by(action="security.ip_denied")).all()
    assert OUTSIDE in entry.message and "Administration" in entry.message and "/journal" in entry.message


def test_refusals_listed_under_security_in_the_journal(admin_client):
    restrict("admin", "192.168.1.0/24")
    from_ip(admin_client, OUTSIDE).get("/roles")
    page = from_ip(admin_client, INSIDE).get("/journal?type=securite").text
    assert "Sécurité" in page and "refusé depuis 203.0.113.9" in page


def test_ipv6_address(admin_client):
    restrict("admin", "2001:db8::/32")
    assert from_ip(admin_client, "2001:db8::42").get("/parametres").status_code == 200
    assert from_ip(admin_client, "2001:db9::42").get("/parametres").status_code == 403


# ---------------------------------------------------------------------------
# Écran Paramètres
# ---------------------------------------------------------------------------

def test_settings_page_shows_the_section_and_current_address(admin_client):
    restrict("rescue", "10.0.0.1")
    page = from_ip(admin_client, INSIDE).get("/parametres").text
    assert "Accès par adresse IP" in page and f"<code>{INSIDE}</code>" in page
    assert "10.0.0.1</textarea>" in page and "TRUST_PROXY=1" in page


def test_save_lists(admin_client):
    response = from_ip(admin_client, INSIDE).post("/parametres/acces-ip", data={
        "admin": "192.168.1.0/24  # local\n203.0.113.4", "rescue": "203.0.113.4", "backchannel": ""})
    assert response.status_code == 302 and response.headers["Location"].endswith("#acces-ip")
    assert ip_access.lists() == {"admin": "192.168.1.0/24\n203.0.113.4", "rescue": "203.0.113.4", "backchannel": ""}
    (entry,) = db.session.scalars(db.select(Activity).filter_by(action="settings.ip_access")).all()
    assert "Administration : 192.168.1.0/24, 203.0.113.4" in entry.message
    assert "Connexion de secours : 203.0.113.4" in entry.message and "Adhérents" not in entry.message


def test_save_without_change_is_not_logged(admin_client):
    data = {"admin": "", "rescue": "", "backchannel": ""}
    assert from_ip(admin_client, INSIDE).post("/parametres/acces-ip", data=data).status_code == 302
    assert not db.session.scalars(db.select(Activity).filter_by(action="settings.ip_access")).all()


def test_clearing_a_list_reopens_the_zone(admin_client):
    restrict("admin", "192.168.1.0/24")
    restrict("backchannel", "198.51.100.7")
    from_ip(admin_client, INSIDE).post("/parametres/acces-ip", data={"admin": "", "rescue": "", "backchannel": ""})
    assert db.session.get(AppSetting, "ip_allow.admin") is None
    assert db.session.get(AppSetting, "ip_allow.backchannel") is None
    assert from_ip(admin_client, OUTSIDE).get("/parametres").status_code == 200


def test_invalid_entry_is_explained_and_nothing_saved(admin_client):
    response = from_ip(admin_client, INSIDE).post("/parametres/acces-ip", data={
        "admin": "192.168.1.0/24", "rescue": "", "backchannel": "adherents.example.org"})
    assert response.status_code == 400
    assert "« adherents.example.org » n'est ni une adresse IP" in html.unescape(response.text)
    assert ip_access.lists() == {"admin": "", "rescue": "", "backchannel": ""}


def test_cannot_lock_oneself_out(admin_client):
    response = from_ip(admin_client, OUTSIDE).post("/parametres/acces-ip", data={
        "admin": "192.168.1.0/24", "rescue": "", "backchannel": ""})
    assert response.status_code == 400
    assert f"Votre adresse actuelle ({OUTSIDE}) n'est pas dans la liste" in html.unescape(response.text)
    assert ip_access.lists()["admin"] == ""


def test_other_zones_may_exclude_the_current_address(admin_client):
    response = from_ip(admin_client, OUTSIDE).post("/parametres/acces-ip", data={
        "admin": "", "rescue": "192.168.1.0/24", "backchannel": "198.51.100.7"})
    assert response.status_code == 302 and ip_access.lists()["rescue"] == "192.168.1.0/24"


def test_save_requires_settings_permission(client, as_role):
    as_role(Role.PROF)
    data = {"admin": "", "rescue": "", "backchannel": "10.0.0.1"}
    assert client.post("/parametres/acces-ip", data=data).status_code == 403
    assert ip_access.lists()["backchannel"] == ""


# ---------------------------------------------------------------------------
# Commande de secours
# ---------------------------------------------------------------------------

def test_cli_shows_and_clears(app):
    restrict("admin", "192.168.1.0/24")
    restrict("rescue", "10.0.0.1")
    runner = app.test_cli_runner()
    shown = runner.invoke(args=["acces-ip"])
    assert shown.exit_code == 0 and "Administration : 192.168.1.0/24" in shown.output
    cleared = runner.invoke(args=["acces-ip", "admin", "--effacer"])
    assert cleared.exit_code == 0 and "Administration : ouverte à tous" in cleared.output
    assert ip_access.lists() == {"admin": "", "rescue": "10.0.0.1", "backchannel": ""}
    assert db.session.scalar(db.select(Activity).filter_by(action="settings.ip_access"))
    runner.invoke(args=["acces-ip", "--effacer"])
    assert not any(ip_access.lists().values())


def test_cli_rejects_unknown_zone(app):
    assert app.test_cli_runner().invoke(args=["acces-ip", "api"]).exit_code != 0


# ---------------------------------------------------------------------------
# Connexion de secours et avis de déconnexion (OIDC)
# ---------------------------------------------------------------------------

from .test_oidc import ALICE, make_oidc_app, me, oidc_login  # noqa: E402
from .test_oidc_logout import fresh_requests, logout_token, notify  # noqa: E402,F401


@pytest.fixture
def oidc_only(tmp_path, monkeypatch):
    app, ctx, idp = make_oidc_app(tmp_path, monkeypatch, OIDC_ONLY=True)
    app.idp = idp
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


def test_rescue_login_hidden_from_other_addresses(oidc_only):
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    restrict("rescue", "192.168.1.0/24")
    client = from_ip(oidc_only.test_client(), OUTSIDE)
    assert client.get("/connexion?local=1").status_code == 404
    data = {"email": admin.email, "password": "motdepasse"}
    assert client.post("/connexion?local=1", data=data).status_code == 404
    assert me(client).status_code == 302
    assert client.get("/connexion").status_code == 200  # la connexion OIDC reste proposée


def test_rescue_login_works_from_allowed_address(oidc_only):
    admin = make_user(Role.ADMIN, email="admin@ohn.test")
    restrict("rescue", "192.168.1.0/24")
    client = from_ip(oidc_only.test_client(), INSIDE)
    assert 'name="password"' in client.get("/connexion?local=1").text
    response = client.post("/connexion?local=1", data={"email": admin.email, "password": "motdepasse"})
    assert response.status_code == 302 and me(client).status_code == 200


def test_rescue_zone_ignored_when_password_login_is_normal(app, client, users):
    restrict("rescue", "192.168.1.0/24")
    assert login(from_ip(client, OUTSIDE), users[Role.ADMIN]).status_code == 302


def test_backchannel_refused_from_other_addresses(oidc_only):
    browser = oidc_only.test_client()
    oidc_login(browser, oidc_only.idp, {**ALICE, "sid": "s1"})
    restrict("backchannel", "198.51.100.7")
    response = notify(from_ip(oidc_only.test_client(), OUTSIDE), logout_token({"sid": "s1"}))
    assert response.status_code == 404 and me(browser).status_code == 200
    assert not db.session.scalars(db.select(Activity).filter_by(action="security.ip_denied")).all()


def test_backchannel_accepted_from_adherents(oidc_only):
    browser = oidc_only.test_client()
    oidc_login(browser, oidc_only.idp, {**ALICE, "sid": "s1"})
    restrict("backchannel", "198.51.100.7")
    response = notify(from_ip(oidc_only.test_client(), "198.51.100.7"), logout_token({"sid": "s1"}))
    assert response.status_code == 200 and me(browser).status_code == 302
