"""Balayage systématique : toutes les routes, tous les rôles, tous les liens."""
import re
from collections import deque
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

import pytest

from app.extensions import db
from app.models import (Category, CategoryType, CustomField, InventoryAudit, Item, Kit, LoanLine, Location,
                        Maintenance, Note, Notification, Reminder, Role, Tag)
from app.services import loans as svc

from .conftest import login

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR

# Routes accessibles sans connexion (le reste doit rediriger vers la page de connexion).
PUBLIC = {"auth.login", "auth.forgot", "auth.token_link", "static", "scan.manifest", "scan.service_worker", "calendar.ical",
          "csp.report", "settings.logo_file"}
# Routes ouvertes à l'emprunteur.
BORROWER = PUBLIC | {"dashboard.index", "auth.logout", "auth.password", "loans.mine", "loans.detail", "loans.checklist",
                     "users.notifications", "calendar.index", "account.index", "account.change_email",
                     "account.revoke_session", "account.revoke_others", "account.set_avatar",
                     "account.remove_avatar", "account.display", "users.avatar_file", "calendar.events", "calendar.reset_token",
                     "scan.resolve", "loans.create", "loans.search_equipment", "loans.check"}


@pytest.fixture
def rich(app, users):
    """Une base représentative : objets, kit, consommable, prêts dans tous les états, audit, notes…"""
    with app.test_request_context():
        bois = Category(name="Bois")
        conso = Category(name="Conso", type=CategoryType.CONSOMMABLE)
        local = Location(name="Local")
        shelf = Location(name="Étagère", parent=local)
        kit = Kit(name="Sono", location=local)
        sax = Item(code="OHN-0001", name="Sax <b>", category=bois, location=shelf, estimated_value=Decimal("900"))
        flute = Item(code="OHN-0002", name="Flûte", category=bois, location=local)
        enceinte = Item(code="OHN-0003", name="Enceinte", category=bois, kit=kit)
        reeds = Item(code="OHN-0004", name="Anches", category=conso, tracking="quantite", quantity=5,
                     low_stock_threshold=8, consumption_mode="consomme")
        sax.tags = [Tag(name="étude")]
        db.session.add_all([sax, flute, enceinte, reeds, kit, shelf,
                            CustomField(label="Tonalité", type="liste", options=["Sib", "Mib"])])
        db.session.flush()
        with db.session.no_autoflush:
            db.session.add_all([Note(item=sax, author=users[P], text="Tampon fatigué"),
                                Maintenance(item=sax, date=date.today(), kind="révision", cost=Decimal("50"),
                                            next_due=date.today() + timedelta(days=5), recorded_by_id=users[P].id),
                                Reminder(item=flute, due_date=date.today(), message="Garantie")])
        now = datetime.now().replace(microsecond=0)
        borrower, prof, admin = users[E], users[P], users[A]

        def loan(kind, lines, start, end=None, validate=True, checkout=False):
            l, _ = svc.create_request(loan_type=kind, borrower=borrower, guarantor=prof, requested_by=prof,
                                      start=start, end=end, lines=lines)
            if validate:
                svc.validate(l, admin)
            if checkout:
                svc.checkout(l, admin)
            return l

        loan("garde", [LoanLine(item=sax, quantity=1)], now - timedelta(days=10), checkout=True)
        loan("reservation", [LoanLine(kit=kit, quantity=1)], now + timedelta(days=2), now + timedelta(days=3))
        loan("reservation", [LoanLine(item=flute, quantity=1)], now + timedelta(days=5), now + timedelta(days=6),
             validate=False)
        done = loan("reservation", [LoanLine(item=reeds, quantity=1)], now - timedelta(days=3),
                    now - timedelta(days=2), checkout=True)
        refused = loan("reservation", [LoanLine(item=flute, quantity=1)], now + timedelta(days=20),
                       now + timedelta(days=21), validate=False)
        svc.refuse(refused, admin, "non")
        audit = InventoryAudit(name="Rentrée", opened_by=admin, locations=[local])
        closed = InventoryAudit(name="Ancien", opened_by=admin)
        db.session.add_all([audit, closed])
        db.session.flush()
        from app.services import audits
        audits.scan(audit, "OHN-0002", prof)
        audits.close(closed, admin)
        db.session.add(Notification(user_id=borrower.id, message="Coucou", link="/mes-prets"))
        db.session.commit()
        return {"done": done}


def build_url(app, rule):
    values = {}
    for arg in rule.arguments:
        converter = rule._converters[arg].__class__.__name__
        values[arg] = 1 if converter == "IntegerConverter" else {"fmt": "csv", "name": "valeur"}.get(arg, "x")
    with app.test_request_context():
        from flask import url_for
        return url_for(rule.endpoint, **values)


def routes(app):
    return [r for r in app.url_map.iter_rules() if r.endpoint != "static"]


def test_every_route_requires_login(client, app, rich):
    unprotected = []
    for rule in routes(app):
        url = build_url(app, rule)
        for method in sorted(rule.methods & {"GET", "POST"}):
            r = client.open(url, method=method)
            if r.status_code == 200 and rule.endpoint not in PUBLIC:
                unprotected.append((rule.endpoint, method))
            if r.status_code == 302 and rule.endpoint not in PUBLIC:
                assert "/connexion" in r.headers["Location"], (rule.endpoint, method)
            assert r.status_code < 500, (rule.endpoint, method)
    assert unprotected == []


def test_borrower_only_reaches_allowed_routes(client, app, users, rich):
    login(client, users[E])
    leaks = []
    for rule in routes(app):
        url = build_url(app, rule)
        for method in sorted(rule.methods & {"GET", "POST"}):
            if rule.endpoint == "auth.logout":
                continue
            r = client.open(url, method=method)
            assert r.status_code < 500, (rule.endpoint, method)
            if rule.endpoint not in BORROWER and r.status_code not in (403, 404, 405):
                leaks.append((rule.endpoint, method, r.status_code))
    assert leaks == []


def test_admin_only_routes_forbidden_to_prof(client, app, users, rich):
    from app.permissions import DEFAULT_PERMISSIONS
    admin_only = DEFAULT_PERMISSIONS[A] - DEFAULT_PERMISSIONS[P] - DEFAULT_PERMISSIONS[E]
    assert {"loan.validate", "user.manage", "import", "audit.manage", "settings.manage", "item.delete",
            "customfield.manage"} <= admin_only
    login(client, users[P])
    checks = [("POST", "/prets/1/valider"), ("POST", "/prets/1/refuser"), ("GET", "/utilisateurs/1/modifier"),
              ("POST", "/import"), ("GET", "/inventaires/nouveau"), ("POST", "/inventaires/1/cloturer"),
              ("GET", "/parametres"), ("POST", "/objets/1/supprimer"), ("POST", "/objets/1/reformer"),
              ("GET", "/champs-perso"), ("POST", "/lieux/1/supprimer"), ("POST", "/kits/1/supprimer"),
              ("POST", "/categories/1/supprimer"), ("POST", "/parametres/rappels")]
    for method, url in checks:
        assert client.open(url, method=method).status_code == 403, url


def crawl(client, start="/", limit=400):
    seen, queue, problems = set(), deque([start]), []
    while queue and len(seen) < limit:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        r = client.get(url)
        textual = r.content_type.startswith(("text/", "application/json"))
        if r.status_code >= 500 or (textual and "Traceback" in r.get_data(as_text=True)[:5000]):
            problems.append((url, r.status_code))
            continue
        if r.status_code in (301, 302):
            target = urlsplit(r.headers["Location"])
            if not target.netloc or target.netloc == "localhost":
                queue.append(target.path + ("?" + target.query if target.query else ""))
            continue
        if "text/html" not in r.content_type:
            continue
        for href in re.findall(r'href="([^"#]+)', r.get_data(as_text=True)):
            href = href.replace("&amp;", "&")
            parts = urlsplit(urljoin(url, href))
            if parts.netloc and parts.netloc != "localhost":
                continue
            path = parts.path + ("?" + parts.query if parts.query else "")
            if path.startswith(("/static/", "/deconnexion", "/cal/")) or path in seen:
                continue
            queue.append(path)
    return seen, problems


@pytest.mark.parametrize("role", [A, P, E])
def test_crawl_every_link_without_errors(client, users, rich, role):
    login(client, users[role])
    seen, problems = crawl(client)
    assert problems == []
    assert len(seen) > (40 if role != E else 4)
    if role == E:
        assert not any(u.startswith(("/objets", "/lieux", "/kits", "/rapports", "/utilisateurs")) for u in seen)


def test_html_escaping_everywhere(client, users, rich):
    """Le nom « Sax <b> » ne doit jamais apparaître non échappé."""
    login(client, users[A])
    for url in ("/", "/objets", "/objets/1", "/prets", "/prets/1", "/rapports", "/lieux/2", "/etiquettes",
                "/inventaires/1", "/calendrier"):
        assert "Sax <b>" not in client.get(url).get_data(as_text=True), url
