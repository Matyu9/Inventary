"""Régénère les captures d'écran de la documentation (docs/images/) à partir des données de démo.

    .venv/bin/python scripts/captures.py

Base temporaire, serveur sur 127.0.0.1 uniquement, Chromium via Playwright (requirements-dev.txt).
"""
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Loan  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from demo_data import PASSWORD, populate  # noqa: E402

OUT = ROOT / "docs" / "images"
DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 390, "height": 844}

# (fichier, compte, chemin, mobile, page entière)
SHOTS = [
    ("connexion", None, "/connexion", False, False),
    ("tableau-de-bord", "claire.dubois", "/", False, False),
    ("inventaire", "claire.dubois", "/objets", False, False),
    ("fiche-objet", "marc.lefevre", "/objets/1", False, True),
    ("prets", "claire.dubois", "/prets", False, False),
    ("pret-concert", "claire.dubois", "/prets/{concert}", False, True),
    ("nouveau-pret", "marc.lefevre", "/prets/nouveau", False, False),
    ("calendrier", "claire.dubois", "/calendrier", False, False),
    ("kit", "claire.dubois", "/kits/1", False, False),
    ("consommables", "marc.lefevre", "/consommables", False, False),
    ("inventaire-physique", "claire.dubois", "/inventaires/1", False, True),
    ("rapports", "claire.dubois", "/rapports", False, True),
    ("utilisateurs", "claire.dubois", "/utilisateurs", False, False),
    ("mobile-mes-prets", "lucas.martin", "/", True, False),
    ("mobile-prets", "marc.lefevre", "/prets", True, False),
]


class DemoConfig(Config):
    SECRET_KEY = "captures-uniquement"
    BASE_URL = "http://localhost:8000"
    OIDC_DISCOVERY_URL = ""


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pupitre-captures-"))
    DemoConfig.DATA_DIR = tmp
    DemoConfig.UPLOAD_DIR = tmp / "uploads"
    DemoConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp / 'demo.db'}"
    app = create_app(DemoConfig)
    with app.app_context():
        db.create_all()
        with app.test_request_context():
            populate()
        concert = db.session.scalar(db.select(Loan.id).filter_by(title="Concert de printemps"))

    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--lang=fr-FR"])
        for name, account, path, mobile, full in SHOTS:
            ctx = browser.new_context(viewport=MOBILE if mobile else DESKTOP, device_scale_factor=2 if mobile else 1,
                                      locale="fr-FR", timezone_id="Europe/Paris", base_url=base,
                                      color_scheme="light", is_mobile=mobile, has_touch=mobile)
            page = ctx.new_page()
            if account:
                page.goto("/connexion")
                page.fill("#email", f"{account}@exemple.org")
                page.fill("#password", PASSWORD)
                page.click("button[type=submit]")
                page.wait_for_url(lambda url: "/connexion" not in url)
            page.goto(path.format(concert=concert))
            page.wait_for_load_state("networkidle")
            if full:
                # Agrandir la fenêtre plutôt que full_page : la barre latérale (100vh) suit alors la page.
                height = page.evaluate("document.documentElement.scrollHeight")
                page.set_viewport_size({**DESKTOP, "height": height})
            page.screenshot(path=str(OUT / f"{name}.png"))
            print(f"docs/images/{name}.png")
            ctx.close()
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main()
