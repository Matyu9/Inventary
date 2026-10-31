"""Vérification du matériel d'un prêt (scan avant de quitter la salle) et liste imprimable."""
from datetime import datetime

import pytest

from app.extensions import db
from app.models import (Category, ConsumptionMode, Item, Kit, Location, LoanCheck, LoanLine, LoanStatus, LoanType,
                        Role, Tracking)
from app.services import loan_checks as svc
from app.services import loans as loan_svc

from .test_loans_routes import make_loan

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def gear(app):
    cat = Category(name="Matériel")
    local = Location(name="Local")
    armoire = Location(name="Armoire", parent=local)
    kit = Kit(name="Sono", location=local)
    items = {
        "sax": Item(code="OHN-0001", name="Saxophone", category=cat, location=armoire, brand="Selmer",
                    serial_number="S123"),
        "flute": Item(code="OHN-0002", name="Flûte", category=cat),
        "pupitre": Item(code="OHN-0003", name="Pupitre", category=cat, tracking=Tracking.QUANTITE, quantity=20,
                        unit="pièces", consumption_mode=ConsumptionMode.RETOURNABLE),
        "anche": Item(code="OHN-0004", name="Anches", category=cat, tracking=Tracking.QUANTITE, quantity=50,
                      consumption_mode=ConsumptionMode.CONSOMME),
        "enceinte": Item(code="OHN-0005", name="Enceinte", category=cat, kit=kit),
        "table": Item(code="OHN-0006", name="Table de mixage", category=cat, kit=kit),
        "autre": Item(code="OHN-0007", name="Trombone", category=cat),
    }
    db.session.add_all([*items.values(), kit])
    db.session.commit()
    return dict(items, kit=kit, local=local)


@pytest.fixture
def concert(users, gear):
    """Réservation en cours : sax, pupitres × 3, anches × 10 (distribuées) et le kit sono."""
    loan = make_loan(users, gear["sax"], status=LoanStatus.VALIDE, kits=[gear["kit"]])
    loan.title = "Concert de printemps"
    loan.lines += [LoanLine(item=gear["pupitre"], quantity=3), LoanLine(item=gear["anche"], quantity=10)]
    db.session.commit()
    loan_svc.checkout(loan, users[A])
    db.session.commit()
    return loan


def codes(rows):
    return [r["item"].code for r in rows]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def test_expected_skips_consumables_and_returned_lines(app, users, gear, concert):
    assert codes(svc.expected(concert)) == ["OHN-0001", "OHN-0005", "OHN-0006", "OHN-0003"]
    sax_line = next(l for l in concert.lines if l.item is gear["sax"])
    loan_svc.checkin(concert, users[A], {sax_line.id: None})
    db.session.commit()
    assert codes(svc.expected(concert)) == ["OHN-0005", "OHN-0006", "OHN-0003"]


def test_expected_everything_flags_returned_and_consumed(app, users, gear, concert):
    sax_line = next(l for l in concert.lines if l.item is gear["sax"])
    loan_svc.checkin(concert, users[A], {sax_line.id: None})
    rows = {r["item"].code: r for r in svc.expected(concert, everything=True)}
    assert rows["OHN-0001"]["returned"] and not rows["OHN-0001"]["consumed"]
    assert rows["OHN-0004"]["consumed"] and not rows["OHN-0004"]["returned"]
    assert rows["OHN-0005"]["kit"] is gear["kit"]
    assert rows["OHN-0003"]["qty"] == 3


def test_scan_individual_item(app, users, gear, concert):
    assert svc.scan(concert, "OHN-0001", users[P]) == [(gear["sax"], svc.CHECKED, 1, 1)]
    assert svc.scan(concert, "ohn-0001", users[P]) == [(gear["sax"], svc.ALREADY, 1, 1)]
    db.session.commit()
    assert len(concert.checks) == 1 and concert.checks[0].checked_by is users[P]


def test_scan_by_qr_url(app, users, gear, concert):
    result = svc.scan(concert, f"https://x.fr/q/{gear['sax'].qr_token}", users[P])
    assert result[0][:2] == (gear["sax"], svc.CHECKED)


def test_scan_quantity_item_counts_one_per_scan(app, users, gear, concert):
    results = [svc.scan(concert, "OHN-0003", users[P])[0] for _ in range(4)]
    assert [(r[1], r[2]) for r in results] == [(svc.CHECKED, 1), (svc.CHECKED, 2), (svc.CHECKED, 3), (svc.ALREADY, 3)]
    row = next(r for r in svc.progress(concert)["rows"] if r["item"] is gear["pupitre"])
    assert row["done"] and row["count"] == 3


def test_scan_kit_checks_all_its_items(app, users, gear, concert):
    results = svc.scan(concert, gear["kit"].qr_token, users[P])
    assert [(i.code, r) for i, r, *_ in results] == [("OHN-0005", svc.CHECKED), ("OHN-0006", svc.CHECKED)]


def test_scan_kit_item_individually(app, users, gear, concert):
    svc.scan(concert, "OHN-0006", users[P])
    progress = svc.progress(concert)
    assert codes(progress["missing"]) == ["OHN-0001", "OHN-0005", "OHN-0003"]


def test_scan_item_not_in_loan(app, users, gear, concert):
    assert svc.scan(concert, "OHN-0007", users[P]) == [(gear["autre"], svc.NOT_IN_LOAN, 0, 0)]
    assert concert.checks == []


def test_scan_consumable_is_not_in_loan_check(app, users, gear, concert):
    assert svc.scan(concert, "OHN-0004", users[P])[0][1] == svc.NOT_IN_LOAN


def test_scan_unknown_code(app, users, concert):
    with pytest.raises(svc.CheckError, match="Code inconnu"):
        svc.scan(concert, "XYZ", users[P])
    with pytest.raises(svc.CheckError, match="Code inconnu"):
        svc.scan(concert, "   ", users[P])


def test_scan_empty_kit(app, users, gear, concert):
    empty = Kit(name="Vide")
    db.session.add(empty)
    db.session.commit()
    with pytest.raises(svc.CheckError, match="vide"):
        svc.scan(concert, empty.qr_token, users[P])


@pytest.mark.parametrize("status", [LoanStatus.DEMANDE, LoanStatus.RENDU, LoanStatus.REFUSE, LoanStatus.ANNULE])
def test_check_refused_outside_valid_or_ongoing(app, users, gear, status):
    loan = make_loan(users, gear["sax"], status=status)
    for action in (lambda: svc.scan(loan, "OHN-0001", users[P]), lambda: svc.mark(loan, gear["sax"].id, users[P]),
                   lambda: svc.reset(loan)):
        with pytest.raises(svc.CheckError, match="validé ou en cours"):
            action()


def test_check_allowed_on_validated_loan(app, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    assert svc.scan(loan, "OHN-0001", users[P])[0][1] == svc.CHECKED


def test_progress_all_done(app, users, gear, concert):
    for code in ("OHN-0001", gear["kit"].qr_token):
        svc.scan(concert, code, users[P])
    svc.mark(concert, gear["pupitre"].id, users[P])
    progress = svc.progress(concert)
    assert progress["done"] == progress["total"] == 4 and progress["missing"] == []


def test_mark_and_unmark(app, users, gear, concert):
    svc.mark(concert, gear["pupitre"].id, users[A])
    assert concert.checks[0].quantity == 3
    svc.mark(concert, gear["pupitre"].id, users[A])  # deux fois : ne dépasse pas la quantité prêtée
    assert concert.checks[0].quantity == 3
    svc.mark(concert, gear["pupitre"].id, users[A], checked=False)
    db.session.commit()
    assert concert.checks == [] and db.session.scalar(db.select(db.func.count(LoanCheck.id))) == 0
    svc.mark(concert, gear["sax"].id, users[A], checked=False)  # rien à retirer : sans erreur


def test_mark_item_not_in_loan(app, users, gear, concert):
    with pytest.raises(svc.CheckError, match="ne fait pas partie"):
        svc.mark(concert, gear["autre"].id, users[A])
    with pytest.raises(svc.CheckError):
        svc.mark(concert, gear["anche"].id, users[A])


def test_reset(app, users, gear, concert):
    svc.scan(concert, "OHN-0001", users[P])
    svc.reset(concert)
    db.session.commit()
    assert concert.checks == [] and svc.progress(concert)["done"] == 0


def test_checkout_clears_preparation_checks(app, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    svc.scan(loan, "OHN-0001", users[P])
    db.session.commit()
    loan_svc.checkout(loan, users[A])
    db.session.commit()
    assert loan.checks == []


def test_checks_survive_partial_return_but_ignore_returned_lines(app, users, gear, concert):
    svc.scan(concert, "OHN-0001", users[P])
    sax_line = next(l for l in concert.lines if l.item is gear["sax"])
    loan_svc.checkin(concert, users[A], {sax_line.id: None})
    db.session.commit()
    assert svc.progress(concert)["total"] == 3 and svc.progress(concert)["done"] == 0


def test_check_removed_when_loan_deleted(app, users, gear, concert):
    svc.scan(concert, "OHN-0001", users[P])
    db.session.commit()
    db.session.delete(concert)
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(LoanCheck.id))) == 0


def test_lent_quantity_lowered_after_check(app, users, gear, concert):
    """Le compteur affiché ne dépasse jamais la quantité attendue, même si la ligne change ensuite."""
    svc.mark(concert, gear["pupitre"].id, users[A])
    line = next(l for l in concert.lines if l.item is gear["pupitre"])
    line.quantity = 2
    row = next(r for r in svc.progress(concert)["rows"] if r["item"] is gear["pupitre"])
    assert row["count"] == 2 and row["done"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role,allowed", [(A, True), (P, True), (E, False)])
def test_verify_page_rights(client, as_role, users, gear, concert, role, allowed):
    as_role(role)
    r = client.get(f"/prets/{concert.id}/verification")
    assert r.status_code == (200 if allowed else 403)
    if allowed:
        assert "Rien oublié" in r.text and "audit_scan.js" in r.text and "OHN-0001" in r.text


@pytest.mark.parametrize("method,suffix", [
    ("get", "/verification/etat"), ("post", "/verification/scan"), ("post", "/verification/1"),
    ("post", "/verification/recommencer"),
])
def test_verify_actions_forbidden_to_borrower(client, as_role, concert, method, suffix):
    as_role(E)
    r = getattr(client, method)(f"/prets/{concert.id}{suffix}", data={"code": "OHN-0001"})
    assert r.status_code == 403
    assert concert.checks == []


def test_verify_requires_login(client, concert):
    r = client.get(f"/prets/{concert.id}/verification")
    assert r.status_code == 302 and "/connexion" in r.headers["Location"]


def test_verify_unknown_loan(client, as_role):
    as_role(P)
    assert client.get("/prets/999/verification").status_code == 404
    assert client.post("/prets/999/verification/scan", data={"code": "x"}).status_code == 404


def test_verify_page_redirects_when_loan_not_active(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.DEMANDE)
    as_role(A)
    r = client.get(f"/prets/{loan.id}/verification", follow_redirects=True)
    assert "validé ou en cours" in r.text and f"Réservation n°{loan.id}" in r.text


def test_scan_htmx_fragment(client, as_role, users, gear, concert):
    as_role(P)
    r = client.post(f"/prets/{concert.id}/verification/scan", data={"code": "OHN-0001"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "pointé ✓" in r.text and 'hx-swap-oob="true"' in r.text and "Pointés (1)" in r.text
    r = client.post(f"/prets/{concert.id}/verification/scan", data={"code": "OHN-0007"}, headers={"HX-Request": "true"})
    assert "ne fait pas partie de ce prêt" in r.text
    r = client.post(f"/prets/{concert.id}/verification/scan", data={"code": "nope"}, headers={"HX-Request": "true"})
    assert "Code inconnu" in r.text


def test_scan_quantity_shows_counter(client, as_role, users, gear, concert):
    as_role(P)
    r = client.post(f"/prets/{concert.id}/verification/scan", data={"code": "OHN-0003"}, headers={"HX-Request": "true"})
    assert "(1/3)" in r.text and "1/3" in r.text


def test_scan_without_htmx_redirects_with_flash(client, as_role, users, gear, concert):
    as_role(A)
    r = client.post(f"/prets/{concert.id}/verification/scan", data={"code": "OHN-0003"}, follow_redirects=True)
    assert "OHN-0003 Pupitre : pointé ✓ (1/3)." in r.text
    r = client.post(f"/prets/{concert.id}/verification/scan", data={"code": "?"}, follow_redirects=True)
    assert "Code inconnu" in r.text


def test_all_checked_message(client, as_role, users, gear, concert):
    as_role(P)
    for code in ("OHN-0001", gear["kit"].qr_token):
        client.post(f"/prets/{concert.id}/verification/scan", data={"code": code})
    client.post(f"/prets/{concert.id}/verification/{gear['pupitre'].id}")
    r = client.get(f"/prets/{concert.id}/verification/etat")
    assert "Tout est là ✓" in r.text and "Vous pouvez partir" in r.text and "Manquants (0)" in r.text
    assert 'hx-swap-oob' not in r.text


def test_mark_route_and_unmark(client, as_role, users, gear, concert):
    as_role(P)
    r = client.post(f"/prets/{concert.id}/verification/{gear['sax'].id}", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Pointés (1)" in r.text
    assert concert.checks[0].checked_by_id == users[P].id
    r = client.post(f"/prets/{concert.id}/verification/{gear['sax'].id}", data={"action": "retirer"})
    assert r.status_code == 302
    db.session.expire_all()
    assert concert.checks == []


def test_mark_route_item_not_in_loan(client, as_role, gear, concert):
    as_role(P)
    r = client.post(f"/prets/{concert.id}/verification/{gear['autre'].id}", follow_redirects=True)
    assert "ne fait pas partie du prêt" in r.text


def test_reset_route(client, as_role, users, gear, concert):
    as_role(P)
    client.post(f"/prets/{concert.id}/verification/scan", data={"code": "OHN-0001"})
    r = client.post(f"/prets/{concert.id}/verification/recommencer", follow_redirects=True)
    assert "Pointage remis à zéro" in r.text
    db.session.expire_all()
    assert concert.checks == []


def test_reset_route_on_closed_loan(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.RENDU)
    as_role(A)
    r = client.post(f"/prets/{loan.id}/verification/recommencer", follow_redirects=True)
    assert "validé ou en cours" in r.text


def test_scan_route_on_closed_loan(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.ANNULE)
    as_role(A)
    r = client.post(f"/prets/{loan.id}/verification/scan", data={"code": "OHN-0001"}, headers={"HX-Request": "true"})
    assert "validé ou en cours" in r.text
    assert loan.checks == []


def test_shows_who_checked(client, as_role, users, gear, concert):
    as_role(A)
    client.post(f"/prets/{concert.id}/verification/scan", data={"code": "OHN-0001"})
    as_role(P)
    r = client.get(f"/prets/{concert.id}/verification")
    assert users[A].full_name in r.text


# --- Boutons sur la fiche du prêt -------------------------------------------

@pytest.mark.parametrize("status,shown", [(LoanStatus.DEMANDE, False), (LoanStatus.VALIDE, True),
                                          (LoanStatus.EN_COURS, True), (LoanStatus.RENDU, False)])
def test_detail_verify_button(client, as_role, users, gear, status, shown):
    loan = make_loan(users, gear["sax"], status=status)
    as_role(P)
    r = client.get(f"/prets/{loan.id}")
    assert ("Vérifier le matériel" in r.text) is shown
    assert "Liste à imprimer" in r.text


def test_detail_borrower_sees_print_but_not_verify(client, as_role, users, gear, concert):
    as_role(E)
    r = client.get(f"/prets/{concert.id}")
    assert "Liste à imprimer" in r.text and "Vérifier le matériel" not in r.text


# --- Liste imprimable --------------------------------------------------------

def test_checklist_content(client, as_role, users, gear, concert):
    as_role(P)
    r = client.get(f"/prets/{concert.id}/liste")
    assert r.status_code == 200
    text = r.text
    for expected in ("Concert de printemps", users[E].full_name, users[P].full_name, "OHN-0001", "Saxophone",
                     "Selmer · n° S123", "Local &gt; Armoire", "Kit Sono — 2 objets", f"KIT-{gear['kit'].id:03d}",
                     "OHN-0005", "OHN-0006", "3 pièces", "distribué", "Départ", "Retour", "signature", "<svg"):
        assert expected in text, expected
    assert "OHN-0007" not in text
    assert "5 lignes" in text


def test_checklist_marks_returned_lines(client, as_role, users, gear, concert):
    sax_line = next(l for l in concert.lines if l.item is gear["sax"])
    loan_svc.checkin(concert, users[A], {sax_line.id: None})
    db.session.commit()
    as_role(A)
    assert ">rendu<" in client.get(f"/prets/{concert.id}/liste").text


def test_checklist_for_closed_loan_still_lists_everything(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], gear["flute"], status=LoanStatus.RENDU)
    for line in loan.lines:
        line.returned_at = datetime.now()
    db.session.commit()
    as_role(A)
    r = client.get(f"/prets/{loan.id}/liste")
    assert "OHN-0001" in r.text and "OHN-0002" in r.text


@pytest.mark.parametrize("role,status", [(A, 200), (P, 200), (E, 200)])
def test_checklist_rights_own_loan(client, as_role, users, gear, concert, role, status):
    as_role(role)
    assert client.get(f"/prets/{concert.id}/liste").status_code == status


def test_checklist_borrower_has_no_verification_qr(client, as_role, concert):
    as_role(E)
    r = client.get(f"/prets/{concert.id}/liste")
    assert "Scanner pour vérifier" not in r.text


def test_checklist_other_borrower_forbidden(client, users, gear, concert):
    from .conftest import login, make_user
    other = make_user(E, email="autre@ohn.test")
    login(client, other)
    assert client.get(f"/prets/{concert.id}/liste").status_code == 403


def test_checklist_requires_login(client, concert):
    assert client.get(f"/prets/{concert.id}/liste").status_code == 302


def test_checklist_unknown_loan(client, as_role):
    as_role(A)
    assert client.get("/prets/999/liste").status_code == 404


def test_checklist_escapes_names(client, as_role, users, gear):
    gear["sax"].name = "<script>alert(1)</script>"
    loan = make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    as_role(A)
    r = client.get(f"/prets/{loan.id}/liste")
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


def test_checklist_garde_without_end(client, as_role, users, gear):
    loan = make_loan(users, gear["flute"], status=LoanStatus.EN_COURS, loan_type=LoanType.GARDE)
    as_role(A)
    assert "Sans date de fin" in client.get(f"/prets/{loan.id}/liste").text
