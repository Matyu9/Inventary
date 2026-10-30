import re
from datetime import datetime

import pytest

from app.extensions import db
from app.models import Category, Item, Kit, Loan, LoanLine, LoanStatus, LoanType, Role
from app.services.qr import find_by_token, qr_svg, qr_url, resolve_scan

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def items(app):
    bois = Category(name="Bois")
    sax = Item(code="OHN-0001", name="Saxophone alto", category=bois)
    flute = Item(code="OHN-0002", name="Flûte", category=bois)
    kit = Kit(name="Kit sono")
    db.session.add_all([sax, flute, kit])
    db.session.commit()
    return dict(sax=sax, flute=flute, kit=kit, bois=bois)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def test_qr_url_uses_base_url(app, items):
    app.config["BASE_URL"] = "https://inventaire.ohn.fr"
    assert qr_url(items["sax"].qr_token) == f"https://inventaire.ohn.fr/q/{items['sax'].qr_token}"


def test_qr_svg_is_scalable_svg(app):
    svg = str(qr_svg("https://exemple.fr/q/abc"))
    assert svg.startswith("<svg") and "viewBox" in svg
    assert not re.search(r'<svg[^>]*\swidth=', svg) and not re.search(r'<svg[^>]*\sheight=', svg)


def test_qr_svg_deterministic_and_data_dependent(app):
    assert str(qr_svg("a")) == str(qr_svg("a"))
    assert str(qr_svg("a")) != str(qr_svg("b"))


def test_find_by_token(items):
    assert find_by_token(items["sax"].qr_token) is items["sax"]
    assert find_by_token(items["kit"].qr_token) is items["kit"]
    assert find_by_token("0" * 32) is None


@pytest.mark.parametrize("text_fn,expected", [
    (lambda i: f"https://inventaire.ohn.fr/q/{i['sax'].qr_token}", "sax"),
    (lambda i: f"http://10.0.0.5:8000/q/{i['flute'].qr_token}?x=1", "flute"),
    (lambda i: i["sax"].qr_token, "sax"),
    (lambda i: f"/q/{i['kit'].qr_token}", "kit"),
    (lambda i: "OHN-0002", "flute"),
    (lambda i: "  ohn-0001  ", "sax"),
    (lambda i: "OHN-9999", None),
    (lambda i: "", None),
    (lambda i: "https://autre-site.fr/page", None),
    (lambda i: f"https://x.fr/q/{i['sax'].qr_token}extra", None),
])
def test_resolve_scan(items, text_fn, expected):
    result = resolve_scan(text_fn(items))
    assert result is (items[expected] if expected else None)


# ---------------------------------------------------------------------------
# Lien des QR (/q/<jeton>)
# ---------------------------------------------------------------------------

def test_qr_link_requires_login_and_returns_after(client, users, items):
    url = f"/q/{items['sax'].qr_token}"
    r = client.get(url)
    assert r.status_code == 302 and "/connexion" in r.headers["Location"] and "next=" in r.headers["Location"]
    r = client.post(f"/connexion?next={url}", data={"email": users[P].email, "password": "motdepasse"})
    assert r.headers["Location"] == url
    r = client.get(url)
    assert r.headers["Location"].endswith(f"/objets/{items['sax'].id}")


@pytest.mark.parametrize("role", [A, P])
def test_qr_link_redirects_staff_to_item(client, as_role, items, role):
    as_role(role)
    r = client.get(f"/q/{items['flute'].qr_token}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/objets/{items['flute'].id}")


def test_qr_link_borrower_gets_limited_page(client, as_role, items):
    as_role(E)
    r = client.get(f"/q/{items['sax'].qr_token}")
    assert r.status_code == 200 and "Saxophone alto" in r.text and "rapporter" in r.text
    assert "/objets/" not in r.text


def test_qr_link_borrower_sees_own_loan(client, as_role, items, users):
    loan = Loan(type=LoanType.GARDE, status=LoanStatus.EN_COURS, borrower=users[E], requested_by=users[P],
                start=datetime(2026, 9, 1))
    loan.lines.append(LoanLine(item=items["sax"]))
    db.session.add(loan)
    db.session.commit()
    as_role(E)
    r = client.get(f"/q/{items['sax'].qr_token}")
    assert "vous est actuellement prêté" in r.text and "1 sept. 2026" in r.text


def test_qr_link_borrower_not_shown_others_loans(client, as_role, items, users):
    from .conftest import make_user
    other = make_user(E, email="autre@ohn.test")
    loan = Loan(type=LoanType.GARDE, status=LoanStatus.EN_COURS, borrower=other, requested_by=users[P],
                start=datetime(2026, 9, 1))
    loan.lines.append(LoanLine(item=items["sax"]))
    db.session.add(loan)
    db.session.commit()
    as_role(E)
    assert "vous est actuellement prêté" not in client.get(f"/q/{items['sax'].qr_token}").text


def test_qr_link_unknown_token(client, as_role, items):
    as_role(P)
    assert client.get("/q/" + "f" * 32).status_code == 404
    assert client.get("/q/pas-un-jeton").status_code == 404


def test_qr_link_kit_redirects_to_kit(client, as_role, items):
    as_role(P)
    r = client.get(f"/q/{items['kit'].qr_token}")
    assert r.headers["Location"].endswith(f"/kits/{items['kit'].id}")


def test_qr_link_kit_borrower_limited(client, as_role, items):
    as_role(E)
    r = client.get(f"/q/{items['kit'].qr_token}")
    assert r.status_code == 200 and "Kit sono" in r.text


# ---------------------------------------------------------------------------
# Page de scan et saisie manuelle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role,status", [(A, 200), (P, 200), (E, 403)])
def test_scan_page_access(client, as_role, items, role, status):
    as_role(role)
    r = client.get("/scan")
    assert r.status_code == status
    if status == 200:
        assert "html5-qrcode.min.js" in r.text and "scan.js" in r.text


def test_scan_page_anonymous(client):
    assert client.get("/scan").status_code == 302


def test_lookup_by_code(client, as_role, items):
    as_role(P)
    r = client.get("/scan/resoudre", query_string={"code": "ohn-0002"})
    assert r.headers["Location"].endswith(f"/objets/{items['flute'].id}")


def test_lookup_by_pasted_url(client, as_role, items):
    as_role(P)
    r = client.get("/scan/resoudre", query_string={"code": f"https://ailleurs.fr/q/{items['sax'].qr_token}"})
    assert r.headers["Location"].endswith(f"/objets/{items['sax'].id}")


def test_lookup_unknown_flashes(client, as_role, items):
    as_role(P)
    r = client.get("/scan/resoudre", query_string={"code": "<b>XYZ</b>"}, follow_redirects=True)
    assert "Aucun objet ne correspond" in r.text and "<b>XYZ</b>" not in r.text


def test_lookup_forbidden_for_borrower(client, as_role, items):
    as_role(E)
    assert client.get("/scan/resoudre?code=OHN-0001").status_code == 403


def test_nav_scan_link(client, as_role, items):
    as_role(P)
    assert 'href="/scan"' in client.get("/").text
    as_role(E)
    assert 'href="/scan"' not in client.get("/").text


# ---------------------------------------------------------------------------
# QR SVG d'un objet
# ---------------------------------------------------------------------------

def test_item_qr_svg(client, as_role, app, items):
    as_role(P)
    r = client.get(f"/objets/{items['sax'].id}/qr.svg")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    assert 'filename="OHN-0001.svg"' in r.headers["Content-Disposition"]
    with app.test_request_context():
        assert r.text == str(qr_svg(qr_url(items["sax"].qr_token)))


def test_item_qr_svg_access(client, as_role, items):
    as_role(E)
    assert client.get(f"/objets/{items['sax'].id}/qr.svg").status_code == 403
    as_role(P)
    assert client.get("/objets/999/qr.svg").status_code == 404


def test_item_detail_shows_qr(client, as_role, items):
    as_role(P)
    r = client.get(f"/objets/{items['sax'].id}")
    assert f"/objets/{items['sax'].id}/qr.svg" in r.text and "Imprimer l'étiquette" in r.text


# ---------------------------------------------------------------------------
# Étiquettes
# ---------------------------------------------------------------------------

def label_codes(r):
    return re.findall(r'<div class="code">([^<]+)</div>', r.text)


def test_labels_for_selection(client, as_role, items):
    as_role(P)
    r = client.get("/etiquettes", query_string={"ids": [items["flute"].id]})
    assert r.status_code == 200 and label_codes(r) == ["OHN-0002"] and r.text.count("<svg") == 1


def test_labels_empty_selection_prints_nothing(client, as_role, items):
    as_role(P)
    r = client.get("/etiquettes", query_string={"selection": "1", "csrf_token": "x", "action": "move"})
    assert label_codes(r) == [] and "Aucun objet sélectionné" in r.text


def test_labels_from_filters(client, as_role, items):
    as_role(P)
    assert label_codes(client.get("/etiquettes")) == ["OHN-0001", "OHN-0002"]
    assert label_codes(client.get("/etiquettes?q=flute")) == ["OHN-0002"]


@pytest.mark.parametrize("fmt,cols", [("petit", 5), ("moyen", 3), ("grand", 2), ("inconnu", 3)])
def test_label_formats(client, as_role, items, fmt, cols):
    as_role(P)
    r = client.get("/etiquettes", query_string={"format": fmt})
    assert f"grid-template-columns: repeat({cols}," in r.text


def test_labels_skip_offset(client, as_role, items):
    as_role(P)
    r = client.get("/etiquettes?decalage=4")
    assert r.text.count('class="label blank"') == 4
    r = client.get("/etiquettes?decalage=-3")
    assert r.text.count('class="label blank"') == 0
    r = client.get("/etiquettes?decalage=100000")
    assert r.text.count('class="label blank"') == 200


def test_labels_keep_query_in_toolbar(client, as_role, items):
    as_role(P)
    r = client.get("/etiquettes", query_string={"q": "flute", "format": "grand", "csrf_token": "secret"})
    assert 'name="q" value="flute"' in r.text
    assert 'name="format" value="grand"' not in r.text and "secret" not in r.text


def test_labels_warn_on_local_base_url(client, as_role, app, items):
    as_role(P)
    app.config["BASE_URL"] = "http://localhost:8000"
    assert "ne sera pas accessible" in client.get("/etiquettes").text
    app.config["BASE_URL"] = "https://inventaire.ohn.fr"
    assert "ne sera pas accessible" not in client.get("/etiquettes").text


def test_labels_truncated(client, as_role, items, monkeypatch):
    import app.blueprints.scan as scan_bp
    monkeypatch.setattr(scan_bp, "MAX_LABELS", 1)
    as_role(P)
    r = client.get("/etiquettes")
    assert len(label_codes(r)) == 1 and "Seules les 1 premières" in r.text


def test_labels_escape_names(client, as_role, items):
    items["sax"].name = "<script>x</script>"
    db.session.commit()
    as_role(P)
    assert "<script>x</script>" not in client.get("/etiquettes").text


def test_labels_forbidden_for_borrower(client, as_role, items):
    as_role(E)
    assert client.get("/etiquettes").status_code == 403


def test_list_has_label_buttons(client, as_role, items):
    as_role(P)
    r = client.get("/objets?q=sax")
    assert 'formaction="/etiquettes"' in r.text and "/etiquettes?q=sax" in r.text


# ---------------------------------------------------------------------------
# PWA
# ---------------------------------------------------------------------------

def test_manifest(client, app):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    data = r.get_json(force=True)
    assert data["lang"] == "fr" and data["start_url"] == "/" and len(data["icons"]) == 2
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_service_worker_served_at_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and r.mimetype == "text/javascript"
    assert r.headers["Cache-Control"] == "no-cache" and "addEventListener" in r.text


def test_base_layout_links_manifest(client, as_role, items):
    as_role(P)
    r = client.get("/")
    assert 'rel="manifest"' in r.text and "/static/js/app.js" in r.text
    assert 'serviceWorker.register("/sw.js")' in client.get("/static/js/app.js").text


def test_static_scan_assets_exist(client):
    for path in ("/static/js/scan.js", "/static/vendor/html5-qrcode.min.js", "/static/icons/icon-512.png"):
        assert client.get(path).status_code == 200, path
