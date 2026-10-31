"""Tests de bout en bout dans un vrai navigateur (Chromium via Playwright).

Le serveur tourne dans un thread, sur 127.0.0.1 uniquement, avec une base SQLite temporaire
et la protection CSRF active (comme en production). Ignorés si Playwright n'est pas installé.
"""
import re
import threading
from datetime import datetime, timedelta

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.config import TestConfig  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Category, Item, Kit, Location, Role, User  # noqa: E402

pytestmark = pytest.mark.e2e
PASSWORD = "motdepasse-e2e"
APP = {}  # appli du serveur de test, pour lire la base depuis un test


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")

    class E2EConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp / 'e2e.db'}"
        WTF_CSRF_ENABLED = True
        DATA_DIR = tmp
        UPLOAD_DIR = tmp / "uploads"

    app = create_app(E2EConfig)
    with app.app_context():
        db.create_all()
        for role, first in ((Role.ADMIN, "Alice"), (Role.PROF, "Paul"), (Role.EMPRUNTEUR, "Emma")):
            u = User(first_name=first, last_name="Test", email=f"{role}@e2e.test", role=role, active=True)
            u.set_password(PASSWORD)
            db.session.add(u)
        bois = Category(name="Bois")
        local = Location(name="Local")
        kit = Kit(name="Sono", location=local)
        db.session.add_all([
            Item(code="OHN-0001", name="Saxophone alto", brand="Selmer", category=bois, location=local),
            Item(code="OHN-0002", name="Flûte traversière", category=bois, location=local),
            Item(code="OHN-0003", name="Enceinte", category=bois, kit=kit),
        ])
        db.session.commit()
    httpd = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    app.config["BASE_URL"] = f"http://127.0.0.1:{httpd.server_port}"
    APP["app"] = app
    yield app.config["BASE_URL"]
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page_for(browser, server):
    contexts = []

    def make(role, mobile=False):
        ctx = browser.new_context(viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 900},
                                  base_url=server, locale="fr-FR", timezone_id="Europe/Paris")
        contexts.append(ctx)
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        page.goto("/connexion")
        page.fill("#email", f"{role}@e2e.test")
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(re.compile(r"/$"))
        return page

    yield make
    for ctx in contexts:
        assert all("Failed to load resource" in e for e in ctx.pages[0].errors) if ctx.pages else True
        ctx.close()


def pick_borrower(page, text):
    value = page.locator("#borrower_id option", has_text=text).first.get_attribute("value")
    page.select_option("#borrower_id", value)


def local_dt(days, hour=10):
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0).strftime("%Y-%m-%dT%H:%M")


def test_login_rejects_bad_password(browser, server):
    page = browser.new_page(base_url=server)
    page.goto("/connexion")
    page.fill("#email", "prof@e2e.test")
    page.fill("#password", "faux")
    page.click("button[type=submit]")
    assert page.locator(".flash-error").inner_text() == "Email ou mot de passe incorrect."
    page.close()


def test_inventory_live_search(page_for):
    page = page_for("prof")
    page.goto("/objets")
    assert page.locator("tbody tr").count() == 3
    page.fill("#filters input[name=q]", "flute")
    page.wait_for_function("document.querySelectorAll('#item-results tbody tr').length === 1")
    assert "OHN-0002" in page.locator("#item-results").inner_text()
    assert "q=flute" in page.url  # hx-push-url
    assert not page.errors


def test_note_added_without_reload(page_for):
    page = page_for("prof")
    page.goto("/objets")
    page.click("text=OHN-0001")
    page.evaluate("window.__marker = 42")
    page.fill("#notes-list input[name=text]", "Tampon de sol à surveiller")
    page.click("#notes-list button[type=submit]")
    page.wait_for_selector("text=Tampon de sol à surveiller")
    assert page.evaluate("window.__marker") == 42  # pas de rechargement : HTMX a fait le travail
    assert not page.errors


def test_full_loan_cycle_in_browser(page_for):
    prof = page_for("prof")
    prof.goto("/prets/nouveau")
    prof.check("input[value=reservation]")
    pick_borrower(prof, "TEST Emma")
    prof.fill("#title", "Concert de printemps")
    prof.fill("#start", local_dt(3))
    prof.fill("#end", local_dt(4))
    prof.fill("#loan-form input[name=q]", "saxo")
    prof.wait_for_selector("#equipment-results >> text=OHN-0001")
    prof.click("#equipment-results button[data-add-line]")
    prof.wait_for_selector("#selected-lines .line")
    prof.wait_for_selector("#availability .flash-success")
    prof.fill("#loan-form input[name=q]", "sono")
    prof.wait_for_selector("#equipment-results button[data-kind=kit]")
    prof.click("#equipment-results button[data-kind=kit]")
    assert prof.locator("#selected-lines .line").count() == 2
    prof.locator("#selected-lines .line").first.locator("[data-remove-line]").click()
    assert prof.locator("#selected-lines .line").count() == 1
    prof.fill("#loan-form input[name=q]", "saxo")
    prof.wait_for_selector("#equipment-results >> text=OHN-0001")
    prof.click("#equipment-results button[data-add-line]")
    prof.wait_for_function("document.querySelectorAll('#selected-lines .line').length === 2")
    prof.keyboard.press("Enter")  # Entrée dans la recherche ne doit pas envoyer le formulaire
    assert "/prets/nouveau" in prof.url
    prof.click("button:has-text('Envoyer la demande')")
    prof.wait_for_selector("text=Demande envoyée")
    loan_url = prof.url
    assert "Valider" not in prof.locator(".page-head").inner_text()
    assert not prof.errors

    admin = page_for("admin")
    admin.goto(loan_url)
    admin.click("button:has-text('Valider')")
    admin.wait_for_selector("text=Prêt validé.")
    admin.click("button:has-text('Remettre le matériel')")
    admin.wait_for_selector("text=Remise enregistrée.")
    first = admin.locator("input[name=line_ids]").first
    first.check()
    admin.locator("select[name^=condition_]").first.select_option("usé")
    admin.click("button:has-text('Enregistrer le retour')")
    admin.wait_for_selector("text=Retour enregistré.")
    assert admin.locator(".badge.status-en_cours").count() == 1, admin.inner_text("main")  # retour partiel
    admin.locator("input[name=line_ids]").first.check()
    admin.click("button:has-text('Enregistrer le retour')")
    admin.wait_for_selector(".badge.status-rendu")
    assert not admin.errors

    borrower = page_for("emprunteur")
    borrower.goto("/mes-prets")
    assert "n°" in borrower.locator("table").inner_text()
    borrower.goto("/notifications")
    assert "validé" in borrower.locator(".notifications").inner_text()


def test_conflict_shown_live(page_for):
    prof = page_for("prof")
    prof.goto("/prets/nouveau?item_id=2")
    prof.check("input[value=reservation]")
    pick_borrower(prof, "TEST Emma")
    prof.fill("#start", local_dt(10))
    prof.fill("#end", local_dt(11))
    prof.click("button:has-text('Envoyer la demande')")
    prof.wait_for_selector("text=Demande envoyée")
    admin = page_for("admin")
    admin.goto(prof.url)
    admin.click("button:has-text('Valider')")
    prof.goto("/prets/nouveau?item_id=2")
    prof.check("input[value=reservation]")
    prof.fill("#start", local_dt(10, 15))
    prof.fill("#end", local_dt(12))
    prof.locator("#end").dispatch_event("change")
    prof.wait_for_selector("#availability .flash-error")
    assert "déjà pris" in prof.locator("#availability").inner_text()


def test_mobile_navigation(page_for):
    page = page_for("prof", mobile=True)
    assert page.locator(".tabbar").is_visible()
    assert not page.locator(".sidebar").is_visible() or page.locator(".sidebar").bounding_box()["x"] < -100
    page.click(".tabbar button[data-open-nav]")
    page.wait_for_function("document.body.classList.contains('nav-open')")
    page.wait_for_timeout(300)
    assert page.locator(".sidebar").bounding_box()["x"] >= 0
    page.keyboard.press("Escape")
    page.wait_for_function("!document.body.classList.contains('nav-open')")
    page.click(".tabbar .tab-scan")
    page.wait_for_url(re.compile("/scan$"))
    # Sans HTTPS (127.0.0.1 compte comme contexte sûr) : la caméra est proposée.
    assert page.locator("#scan-start").is_visible()
    page.fill("#code", "ohn-0001")
    page.click("button:has-text('Ouvrir')")
    page.wait_for_url(re.compile(r"/objets/\d+$"))
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390, "débordement horizontal sur mobile"


def test_mobile_tables_become_cards(page_for):
    page = page_for("prof", mobile=True)
    page.goto("/objets")
    assert not page.locator("table.responsive thead").is_visible()
    assert page.locator("td[data-label=Code]").first.is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_mobile_loan_detail_fits_screen(page_for):
    """Le champ date + bouton « Enregistrer » élargissait la fiche prêt au-delà de l'écran."""
    page = page_for("admin", mobile=True)
    page.goto("/prets/nouveau?item_id=1")
    page.check("input[value=reservation]")
    pick_borrower(page, "TEST Emma")
    page.fill("#start", local_dt(20))
    page.fill("#end", local_dt(21))
    page.click("button:has-text('Envoyer la demande')")
    page.wait_for_selector("text=Modifier la date de fin")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.on("dialog", lambda d: d.accept())
    page.click(".page-head button:has-text('Annuler')")  # prêt sans garant : ne doit pas planter
    page.wait_for_selector(".badge.status-annule")


def test_short_sidebar_stays_compact(page_for):
    """Pico met `justify-content: space-between` sur nav : 3 liens s'étalaient sur toute la hauteur."""
    page = page_for("emprunteur")
    boxes = [page.locator(".sidebar .nav-link").nth(i).bounding_box() for i in range(2)]
    assert boxes[1]["y"] - boxes[0]["y"] < 60


def test_mobile_cards_keep_cell_content_together(page_for):
    """Une cellule-carte doit avoir un seul bloc de valeur (sinon « Garde » flotte loin du motif)."""
    page = page_for("admin", mobile=True)
    page.goto("/objets")
    assert page.locator("table.responsive tbody td").count() > 0
    split = page.evaluate("""() => [...document.querySelectorAll('table.responsive tbody td')].filter(td =>
        [...td.childNodes].filter(n => n.nodeType === 1 || n.textContent.trim()).length > 1).map(td => td.dataset.label)""")
    assert split == []
    backgrounds = page.evaluate("""() => [...document.querySelectorAll('table.responsive tbody td')]
        .map(td => getComputedStyle(td).backgroundColor)""")
    assert set(backgrounds) == {"rgba(0, 0, 0, 0)"}


def test_audit_scanning_flow(page_for):
    admin = page_for("admin")
    admin.goto("/inventaires/nouveau")
    admin.fill("#name", "Inventaire e2e")
    admin.click("button:has-text('Ouvrir')")
    admin.wait_for_selector("text=Inventaire ouvert")
    admin.fill("#audit-scan-form input[name=code]", "OHN-0002")
    admin.press("#audit-scan-form input[name=code]", "Enter")
    admin.wait_for_selector("#scan-feedback >> text=trouvé")
    assert admin.locator("#audit-scan-form input[name=code]").input_value() == ""  # prêt pour le scan suivant
    admin.fill("#audit-scan-form input[name=code]", "OHN-0002")
    admin.press("#audit-scan-form input[name=code]", "Enter")
    admin.wait_for_selector("#scan-feedback >> text=déjà scanné")
    admin.fill("#audit-scan-form input[name=code]", "XYZ")
    admin.press("#audit-scan-form input[name=code]", "Enter")
    admin.wait_for_selector("#scan-feedback >> text=Code inconnu")
    admin.once("dialog", lambda d: d.accept())
    admin.click("button:has-text('Clôturer')")
    admin.wait_for_selector("text=Inventaire clôturé")
    assert "Manquant" in admin.inner_text("main")
    assert not admin.errors


def test_loan_verification_flow(page_for):
    """Fin de concert : on scanne le matériel du prêt jusqu'à « Tout est là », puis on ouvre la liste imprimable."""
    admin = page_for("admin")
    admin.goto("/prets/nouveau?item_id=2&kit_id=1")
    admin.check("input[value=reservation]")
    pick_borrower(admin, "TEST Emma")
    admin.fill("#title", "Concert vérif e2e")
    admin.fill("#start", local_dt(40))
    admin.fill("#end", local_dt(41))
    admin.check("#auto_validate")
    admin.wait_for_selector("#availability .flash-success")
    admin.click("button:has-text('Envoyer la demande')")
    admin.wait_for_selector("text=Prêt enregistré et validé.")
    admin.click("button:has-text('Remettre le matériel')")
    admin.wait_for_selector("text=Remise enregistrée.")
    admin.click("a:has-text('Vérifier le matériel')")
    admin.wait_for_selector("text=Rien oublié ?")
    code = "#loan-check-form input[name=code]"
    admin.fill(code, "OHN-0002")
    admin.press(code, "Enter")
    admin.wait_for_selector("#scan-feedback >> text=pointé ✓")
    assert admin.locator(code).input_value() == ""
    admin.fill(code, "OHN-0001")
    admin.press(code, "Enter")
    admin.wait_for_selector("#scan-feedback >> text=ne fait pas partie de ce prêt")
    assert "Tout est là" not in admin.inner_text("#loan-check-list")
    admin.fill(code, "OHN-0003")
    admin.press(code, "Enter")
    admin.wait_for_selector("#loan-check-list >> text=Tout est là ✓")
    with admin.expect_popup() as popup:
        admin.click("a:has-text('Liste à imprimer')")
    sheet = popup.value
    sheet.wait_for_load_state()
    assert "Kit Sono" in sheet.inner_text("table") and "OHN-0002" in sheet.inner_text("table")
    assert not admin.errors


def test_link_buttons_keep_link_size(page_for):
    """Pico met `button[type=submit] { width: 100% }` : le « ✕ » d'un scan prenait toute la ligne (et débordait)."""
    page = page_for("admin", mobile=True)
    page.goto("/inventaires/nouveau")
    page.fill("#name", "Inventaire boutons e2e")
    page.click("main button:has-text('Ouvrir')")
    page.wait_for_selector("text=Inventaire ouvert")
    page.fill("#audit-scan-form input[name=code]", "OHN-0001")
    page.press("#audit-scan-form input[name=code]", "Enter")
    page.wait_for_selector("#scan-feedback >> text=trouvé")
    page.reload()
    assert page.locator("button[aria-label='Annuler ce scan']").first.bounding_box()["width"] < 60
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_calendar_renders_events(page_for):
    page = page_for("admin")
    page.goto("/calendrier")
    page.wait_for_selector(".fc")
    page.click(".fc-listMonth-button") if page.locator(".fc-listMonth-button").count() else None
    page.wait_for_timeout(500)
    assert not page.errors


def test_labels_and_qr(page_for):
    page = page_for("prof")
    page.goto("/etiquettes?format=petit")
    assert page.locator(".label svg").count() == 3
    page.select_option("select[name=format]", "grand")
    page.wait_for_url(re.compile("format=grand"))
    assert page.locator(".label").count() == 3


def test_dark_mode_has_readable_text(browser, server):
    ctx = browser.new_context(base_url=server, color_scheme="dark")
    page = ctx.new_page()
    page.goto("/connexion")
    def luminance(css):
        r, g, b = (int(x) for x in re.findall(r"\d+", css)[:3])
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    bg = luminance(page.evaluate("getComputedStyle(document.body).backgroundColor"))
    fg = luminance(page.evaluate("getComputedStyle(document.body).color"))
    assert bg < 60 and fg > 180  # fond sombre, texte clair
    ctx.close()


def test_every_staff_page_without_js_errors(page_for):
    page = page_for("admin")
    for path in ("/", "/objets", "/objets/1", "/objets/nouveau", "/kits", "/kits/1", "/consommables", "/lieux",
                 "/categories", "/prets", "/prets/nouveau", "/mes-prets", "/calendrier", "/scan", "/inventaires",
                 "/rapports", "/import-export", "/utilisateurs", "/champs-perso", "/parametres", "/notifications"):
        page.goto(path)
        assert page.locator("h1").count() >= 1, path
    assert not page.errors, page.errors


def test_anonymous_report_from_qr_then_handled(browser, server, page_for):
    """Une personne sans compte scanne le QR, signale un objet trouvé ; un prof le voit et le traite."""
    from app.models import PublicReport
    with APP["app"].app_context():
        token = db.session.scalar(db.select(Item.qr_token).where(Item.code == "OHN-0002"))
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, base_url=server, locale="fr-FR")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"/q/{token}")
    assert "Flûte traversière" in page.inner_text("h1")
    page.click("summary:has-text('trouvé cet objet')")
    hp = page.locator("details[open] .hp input").bounding_box()
    assert hp is None or hp["x"] + hp["width"] < 0  # piège à robots hors de l'écran
    form = page.locator("details[open] form")
    form.locator("textarea[name=message]").fill("Oubliée dans la salle des fêtes, déposée à l'accueil")
    form.locator("input[name=name]").fill("Camille")
    form.locator("button[type=submit]").click()
    assert "bien été transmis" in page.locator(".flash-success").inner_text()
    ctx.close()
    assert errors == []
    with APP["app"].app_context():
        assert db.session.scalar(db.select(PublicReport.name).where(PublicReport.kind == "trouve")) == "Camille"

    prof = page_for("prof")
    prof.click(".sidebar a[href='/signalements']")
    assert "Oubliée dans la salle des fêtes" in prof.inner_text("main")
    prof.fill("input[name=note]", "Récupérée")
    prof.click("text=Marquer comme traité")
    assert "Aucun signalement à traiter" in prof.inner_text("main")


def test_borrower_request_then_guarantor_accepts(page_for):
    """L'emprunteur demande du matériel en choisissant son garant ; le prof accepte depuis son tableau de bord."""
    borrower = page_for("emprunteur", mobile=True)
    borrower.click("text=Demander du matériel")
    borrower.wait_for_url(re.compile(r"/prets/nouveau"))
    assert borrower.locator("#borrower_id").count() == 0
    value = borrower.locator("#guarantor_id option", has_text="Paul").first.get_attribute("value")
    borrower.select_option("#guarantor_id", value)
    borrower.check("input[value=reservation]")
    borrower.fill("#start", local_dt(20))
    borrower.fill("#end", local_dt(21))
    borrower.fill("#loan-form input[name=q]", "flûte")
    borrower.wait_for_selector("#equipment-results >> text=OHN-0002")
    borrower.click("#equipment-results button[data-add-line]")
    borrower.wait_for_selector("#availability .flash-success")
    borrower.click("button:has-text('Envoyer au garant')")
    borrower.wait_for_selector("text=votre garant")
    assert "En attente du garant" in borrower.locator(".page-head").inner_text()
    loan_url = borrower.url
    assert not borrower.errors

    prof = page_for("prof")
    assert "Demandes à confirmer comme garant" in prof.inner_text("main")
    prof.goto(loan_url)
    prof.click("button:has-text('être garant')")
    prof.wait_for_selector("text=attend maintenant la validation")
    assert "Demandé" in prof.locator(".page-head .badge").inner_text()

    admin = page_for("admin")
    admin.goto(loan_url)
    admin.click(".page-head button:has-text('Valider')")
    admin.wait_for_selector("text=Prêt validé.")
