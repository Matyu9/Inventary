import csv
import io

import pytest

from app.extensions import db
from app.models import (Activity, AuditScan, Category, InventoryAudit, Item, ItemStatus, Kit, Location, Role)
from app.services import audits as svc

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def site(app):
    bois, perc = Category(name="Bois"), Category(name="Percussions")
    local = Location(name="Local")
    armoire = Location(name="Armoire", parent=local)
    salle = Location(name="Salle")
    kit = Kit(name="Batterie")
    items = {
        "sax": Item(code="OHN-0001", name="Sax", category=bois, location=armoire),
        "flute": Item(code="OHN-0002", name="Flûte", category=bois, location=local),
        "clar": Item(code="OHN-0003", name="Clarinette", category=bois, location=armoire, status=ItemStatus.EN_GARDE),
        "old": Item(code="OHN-0004", name="Vieux", category=bois, location=local, status=ItemStatus.REFORME),
        "caisse": Item(code="OHN-0005", name="Caisse", category=perc, location=local, kit=kit),
        "tom": Item(code="OHN-0006", name="Tom", category=perc, location=local, kit=kit),
        "ailleurs": Item(code="OHN-0007", name="Trombone", category=bois, location=salle),
    }
    db.session.add_all([*items.values(), kit])
    db.session.commit()
    return dict(items, bois=bois, perc=perc, local=local, armoire=armoire, salle=salle, kit=kit)


def make_audit(users, site, locations=("local",), categories=()):
    audit = InventoryAudit(name="Rentrée", opened_by=users[A])
    audit.locations = [site[k] for k in locations]
    audit.categories = [site[k] for k in categories]
    db.session.add(audit)
    db.session.commit()
    return audit


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def test_expected_scope_by_location_includes_children(app, users, site):
    audit = make_audit(users, site)
    codes = [i.code for i in db.session.scalars(svc.expected_query(audit))]
    assert codes == ["OHN-0001", "OHN-0002", "OHN-0003", "OHN-0005", "OHN-0006"]  # réformé et salle exclus


def test_expected_scope_by_category(app, users, site):
    audit = make_audit(users, site, locations=(), categories=("perc",))
    assert [i.code for i in db.session.scalars(svc.expected_query(audit))] == ["OHN-0005", "OHN-0006"]


def test_expected_scope_everything(app, users, site):
    audit = make_audit(users, site, locations=())
    assert len(db.session.scalars(svc.expected_query(audit)).all()) == 6


def test_scan_found_unexpected_and_duplicate(app, users, site):
    audit = make_audit(users, site)
    assert svc.scan(audit, "OHN-0001", users[P]) == [(site["sax"], svc.FOUND, False)]
    assert svc.scan(audit, "ohn-0007", users[P]) == [(site["ailleurs"], svc.UNEXPECTED, False)]
    assert svc.scan(audit, "OHN-0001", users[P]) == [(site["sax"], svc.FOUND, True)]
    db.session.commit()
    assert len(audit.scans) == 2


def test_scan_retired_item_is_unexpected(app, users, site):
    audit = make_audit(users, site)
    assert svc.scan(audit, "OHN-0004", users[P])[0][1] == svc.UNEXPECTED


def test_scan_by_qr_url(app, users, site):
    audit = make_audit(users, site)
    assert svc.scan(audit, f"https://x.fr/q/{site['flute'].qr_token}", users[P])[0][0] is site["flute"]


def test_scan_kit_marks_all_items(app, users, site):
    audit = make_audit(users, site)
    results = svc.scan(audit, site["kit"].qr_token, users[P])
    assert {(i.code, r) for i, r, _ in results} == {("OHN-0005", svc.FOUND), ("OHN-0006", svc.FOUND)}


def test_scan_empty_kit(app, users, site):
    empty = Kit(name="Vide")
    db.session.add(empty)
    db.session.commit()
    with pytest.raises(svc.AuditError, match="est vide"):
        svc.scan(make_audit(users, site), empty.qr_token, users[P])


@pytest.mark.parametrize("code", ["", "OHN-9999", "n'importe quoi"])
def test_scan_unknown(app, users, site, code):
    with pytest.raises(svc.AuditError, match="Code inconnu"):
        svc.scan(make_audit(users, site), code, users[P])


def test_scan_records_location(app, users, site):
    audit = make_audit(users, site)
    svc.scan(audit, "OHN-0001", users[P], site["local"])
    db.session.commit()
    assert audit.scans[0].location == site["local"] and audit.scans[0].scanned_by == users[P]


def test_progress(app, users, site):
    audit = make_audit(users, site)
    svc.scan(audit, "OHN-0001", users[P])
    svc.scan(audit, "OHN-0007", users[P])
    db.session.commit()
    p = svc.progress(audit)
    assert p["expected"] == 5 and p["found"] == 1 and len(p["unexpected"]) == 1
    assert [i.code for i in p["remaining"]] == ["OHN-0002", "OHN-0005", "OHN-0006"]
    assert [i.code for i in p["lent"]] == ["OHN-0003"]


def test_close_creates_missing_and_lent(app, users, site):
    audit = make_audit(users, site)
    svc.scan(audit, "OHN-0001", users[P])
    svc.close(audit, users[A])
    db.session.commit()
    groups = svc.report(audit)
    assert [s.item.code for s in groups[svc.FOUND]] == ["OHN-0001"]
    assert [s.item.code for s in groups[svc.MISSING]] == ["OHN-0002", "OHN-0005", "OHN-0006"]
    assert [s.item.code for s in groups[svc.LENT]] == ["OHN-0003"]
    assert audit.status == "cloture" and audit.closed_at
    missing_log = db.session.scalars(db.select(Activity.item_id).filter_by(action="audit.missing")).all()
    assert set(missing_log) == {site["flute"].id, site["caisse"].id, site["tom"].id}


def test_close_is_final(app, users, site):
    audit = make_audit(users, site)
    svc.close(audit, users[A])
    with pytest.raises(svc.AuditError, match="déjà clôturé"):
        svc.close(audit, users[A])
    with pytest.raises(svc.AuditError, match="clôturé"):
        svc.scan(audit, "OHN-0001", users[P])


def test_report_frozen_after_close(app, users, site):
    audit = make_audit(users, site)
    svc.close(audit, users[A])
    db.session.commit()
    site["ailleurs"].location = site["local"]  # un nouvel objet arrive dans le périmètre après coup
    db.session.commit()
    assert "OHN-0007" not in [s.item.code for g in svc.report(audit).values() for s in g]


def test_close_applies_locations(app, users, site):
    audit = make_audit(users, site)
    svc.scan(audit, "OHN-0001", users[P], site["local"])  # trouvé ailleurs qu'enregistré (armoire)
    svc.scan(audit, "OHN-0002", users[P], site["local"])  # déjà au bon endroit
    svc.scan(audit, "OHN-0007", users[P], site["local"])  # inattendu : on corrige aussi
    moved = svc.close(audit, users[A], apply_locations=True)
    db.session.commit()
    assert moved == 2 and site["sax"].location == site["local"] and site["ailleurs"].location == site["local"]


def test_close_without_applying_locations(app, users, site):
    audit = make_audit(users, site)
    svc.scan(audit, "OHN-0001", users[P], site["local"])
    assert svc.close(audit, users[A]) == 0 and site["sax"].location == site["armoire"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def test_access(client, as_role, users, site):
    audit = make_audit(users, site)
    for role, list_ok, manage_ok in ((A, True, True), (P, True, False), (E, False, False)):
        as_role(role)
        assert client.get("/inventaires").status_code == (200 if list_ok else 403)
        assert client.get(f"/inventaires/{audit.id}").status_code == (200 if list_ok else 403)
        assert client.get("/inventaires/nouveau").status_code == (200 if manage_ok else 403)
        if not manage_ok:
            assert client.post(f"/inventaires/{audit.id}/cloturer").status_code == 403
            assert client.post(f"/inventaires/{audit.id}/supprimer").status_code == 403


def test_create_audit(client, as_role, site):
    as_role(A)
    r = client.post("/inventaires/nouveau", data={"name": "Rentrée", "locations": [site["local"].id],
                                                   "categories": [site["bois"].id]})
    audit = db.session.scalar(db.select(InventoryAudit))
    assert r.headers["Location"].endswith(f"/inventaires/{audit.id}")
    assert audit.locations == [site["local"]] and audit.categories == [site["bois"]] and audit.status == "ouvert"


def test_create_audit_requires_name(client, as_role, site):
    as_role(A)
    assert "aria-invalid" in client.post("/inventaires/nouveau", data={"name": ""}).text


def test_prof_scans_via_htmx(client, as_role, users, site):
    audit = make_audit(users, site)
    as_role(P)
    r = client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001", "location_id": site["local"].id},
                    headers={"HX-Request": "true"})
    assert "trouvé ✓" in r.text and 'hx-swap-oob="true"' in r.text and "<html" not in r.text
    r = client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001"}, headers={"HX-Request": "true"})
    assert "déjà scanné" in r.text
    r = client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0007"}, headers={"HX-Request": "true"})
    assert "hors du périmètre" in r.text and "Salle" in r.text
    r = client.post(f"/inventaires/{audit.id}/scans", data={"code": "???"}, headers={"HX-Request": "true"})
    assert "Code inconnu" in r.text


def test_scan_without_htmx_redirects_with_flash(client, as_role, users, site):
    audit = make_audit(users, site)
    as_role(P)
    r = client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0002"}, follow_redirects=True)
    assert "OHN-0002 Flûte : trouvé." in r.text


def test_detail_page_lists(client, as_role, users, site):
    audit = make_audit(users, site)
    as_role(P)
    client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001"})
    r = client.get(f"/inventaires/{audit.id}")
    assert "1</strong> trouvé(s) sur 5" in r.text and "Restent à trouver (3)" in r.text
    assert "Prêtés (absents normalement)" in r.text and "audit_scan.js" in r.text
    assert "Clôturer l&#39;inventaire" not in r.text and "Clôturer l'inventaire" not in r.text  # prof


def test_delete_scan(client, as_role, users, site):
    audit = make_audit(users, site)
    as_role(P)
    client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001"})
    scan = db.session.scalar(db.select(AuditScan))
    client.post(f"/inventaires/{audit.id}/scans/{scan.id}/supprimer")
    assert db.session.scalar(db.select(db.func.count(AuditScan.id))) == 0
    assert client.post(f"/inventaires/{audit.id}/scans/999/supprimer").status_code == 404


def test_delete_scan_of_other_audit_404(client, as_role, users, site):
    a1, a2 = make_audit(users, site), make_audit(users, site)
    as_role(P)
    client.post(f"/inventaires/{a1.id}/scans", data={"code": "OHN-0001"})
    scan = db.session.scalar(db.select(AuditScan))
    assert client.post(f"/inventaires/{a2.id}/scans/{scan.id}/supprimer").status_code == 404


def test_close_and_report(client, as_role, users, site):
    audit = make_audit(users, site)
    as_role(A)
    client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001", "location_id": site["local"].id})
    r = client.post(f"/inventaires/{audit.id}/cloturer", data={"apply_locations": "1"}, follow_redirects=True)
    assert "Inventaire clôturé. 1 lieu(x) corrigé(s)." in r.text and "Manquant (3)" in r.text
    assert client.post(f"/inventaires/{audit.id}/cloturer", follow_redirects=True).status_code == 200
    r = client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0002"}, follow_redirects=True)
    assert "clôturé" in r.text


def test_scan_closed_audit_htmx(client, as_role, users, site):
    audit = make_audit(users, site)
    audit.status = "cloture"
    db.session.commit()
    as_role(P)
    r = client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001"}, headers={"HX-Request": "true"})
    assert "Cet inventaire est clôturé." in r.text


def test_delete_scan_on_closed_audit_rejected(client, as_role, users, site):
    audit = make_audit(users, site)
    as_role(A)
    client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001"})
    client.post(f"/inventaires/{audit.id}/cloturer")
    scan = db.session.scalar(db.select(AuditScan).filter_by(result="trouve"))
    assert client.post(f"/inventaires/{audit.id}/scans/{scan.id}/supprimer").status_code == 400


def test_export_csv(client, as_role, users, site):
    audit = make_audit(users, site)
    as_role(A)
    client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001"})
    client.post(f"/inventaires/{audit.id}/cloturer")
    r = client.get(f"/inventaires/{audit.id}/export.csv")
    assert r.mimetype == "text/csv" and r.text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿")), delimiter=";"))
    assert rows[0][0] == "Code" and len(rows) == 1 + 5
    assert {row[5] for row in rows[1:]} == {"Trouvé", "Manquant", "Prêté (absent normal)"}


def test_delete_audit(client, as_role, users, site):
    audit = make_audit(users, site)
    as_role(A)
    client.post(f"/inventaires/{audit.id}/scans", data={"code": "OHN-0001"})
    client.post(f"/inventaires/{audit.id}/supprimer")
    assert db.session.scalar(db.select(db.func.count(InventoryAudit.id))) == 0
    assert db.session.scalar(db.select(db.func.count(AuditScan.id))) == 0


def test_list_page(client, as_role, users, site):
    make_audit(users, site)
    as_role(P)
    r = client.get("/inventaires")
    assert "Rentrée" in r.text and "En cours" in r.text and "Nouvel inventaire" not in r.text


def test_audit_404(client, as_role):
    as_role(A)
    assert client.get("/inventaires/9").status_code == 404
    assert client.get("/inventaires/9/export.csv").status_code == 404


def test_nav_link(client, as_role):
    as_role(P)
    assert 'href="/inventaires"' in client.get("/").text
    as_role(E)
    assert 'href="/inventaires"' not in client.get("/").text
