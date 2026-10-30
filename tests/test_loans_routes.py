import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import (Category, ConsumptionMode, Item, ItemStatus, Kit, Loan, LoanLine, LoanStatus, LoanType,
                        Notification, Role, Tracking, User)

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR
FMT = "%Y-%m-%dT%H:%M"


def fmt(dt):
    return dt.strftime(FMT)


@pytest.fixture
def gear(app):
    bois = Category(name="Bois")
    sax = Item(code="OHN-0001", name="Saxophone", category=bois)
    flute = Item(code="OHN-0002", name="Flûte", category=bois)
    old = Item(code="OHN-0003", name="Vieux cornet", category=bois, status=ItemStatus.REFORME)
    cable = Item(code="OHN-0004", name="Câble", category=bois, tracking=Tracking.QUANTITE, quantity=8,
                 consumption_mode=ConsumptionMode.RETOURNABLE)
    kit = Kit(name="Kit sono")
    enceinte = Item(code="OHN-0005", name="Enceinte", category=bois, kit=kit)
    db.session.add_all([sax, flute, old, cable, kit, enceinte])
    db.session.commit()
    return dict(sax=sax, flute=flute, old=old, cable=cable, kit=kit, enceinte=enceinte)


def soon(days=1, hours=0):
    return (datetime.now() + timedelta(days=days, hours=hours)).replace(second=0, microsecond=0)


def loan_form(users, *, items=(), kits=(), qty=None, **kw):
    data = {"type": LoanType.RESERVATION, "borrower_id": users[E].id, "guarantor_id": users[P].id,
            "title": "Concert", "start": fmt(soon(1)), "end": fmt(soon(2)), "item_ids": [i.id for i in items],
            "kit_ids": [k.id for k in kits]}
    for item_id, q in (qty or {}).items():
        data[f"qty_{item_id}"] = q
    data.update(kw)
    return data


def make_loan(users, *items, status=LoanStatus.DEMANDE, loan_type=LoanType.RESERVATION, borrower=None,
              guarantor=None, start=None, end=None, kits=()):
    loan = Loan(type=loan_type, status=status, borrower=borrower or users[E], guarantor=guarantor or users[P],
                requested_by=guarantor or users[P], start=start or soon(1),
                end=end if end is not None else (soon(2) if loan_type == LoanType.RESERVATION else None))
    loan.lines = [LoanLine(item=i, quantity=1) for i in items] + [LoanLine(kit=k, quantity=1) for k in kits]
    db.session.add(loan)
    db.session.commit()
    return loan


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,allowed", [
    ("/prets", {A, P}),
    ("/prets/nouveau", {A, P}),
    ("/prets/recherche-materiel?q=sax", {A, P}),
    ("/mes-prets", {A, P, E}),
    ("/notifications", {A, P, E}),
    ("/utilisateurs", {A, P}),
    ("/utilisateurs/nouveau", {A, P}),
])
def test_access(client, as_role, gear, url, allowed):
    for role in Role.ALL:
        as_role(role)
        assert client.get(url).status_code == (200 if role in allowed else 403), (url, role)


def test_loan_detail_access(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    other = make_user(E, email="autre@ohn.test")
    url = f"/prets/{loan.id}"
    for role in (A, P, E):
        as_role(role)
        assert client.get(url).status_code == 200
    client.post("/deconnexion")
    login(client, other)
    assert client.get(url).status_code == 403
    assert client.get("/prets/999").status_code == 404


def test_loan_detail_layout(client, as_role, users, gear):
    """Date de demande sous le nom ; formulaires admin regroupés à part, absents pour un prof."""
    loan = make_loan(users, gear["sax"])
    as_role(A)
    html = client.get(f"/prets/{loan.id}").text
    assert re.search(r'Test<small class="muted meta">le ', html)
    assert html.count('class="loan-admin"') == 1 and "Motif du refus" in html
    as_role(P)
    assert "loan-admin" not in client.get(f"/prets/{loan.id}").text


def test_borrower_detail_hides_staff_links(client, as_role, users, gear):
    users[E].phone = "0600000000"
    loan = make_loan(users, gear["sax"])
    as_role(E)
    r = client.get(f"/prets/{loan.id}")
    assert "/objets/" not in r.text and "0600000000" not in r.text and "Valider" not in r.text


# ---------------------------------------------------------------------------
# Demande
# ---------------------------------------------------------------------------

def test_prof_requests_loan(client, as_role, users, gear):
    as_role(P)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], kits=[gear["kit"]],
                                                       auto_validate="y"))
    loan = db.session.scalar(db.select(Loan))
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/prets/{loan.id}")
    assert loan.status == LoanStatus.DEMANDE  # un prof ne peut pas valider directement
    assert loan.guarantor == users[P] and loan.requested_by == users[P] and loan.title == "Concert"
    assert {(l.item_id, l.kit_id) for l in loan.lines} == {(gear["sax"].id, None), (None, gear["kit"].id)}
    assert db.session.scalar(db.select(Notification).filter_by(user_id=users[A].id))


def test_prof_cannot_pick_other_guarantor(client, as_role, users, gear):
    other_prof = make_user(P, email="p2@ohn.test")
    as_role(P)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], guarantor_id=other_prof.id))
    assert r.status_code == 200 and db.session.scalar(db.select(db.func.count(Loan.id))) == 0


def test_prof_form_only_offers_self_as_guarantor(client, as_role, users, gear):
    make_user(P, email="p2@ohn.test")
    as_role(P)
    r = client.get("/prets/nouveau")
    guarantor_select = re.search(r'<select[^>]*id="guarantor_id".*?</select>', r.text, re.S).group(0)
    assert guarantor_select.count("<option") == 1


def test_admin_auto_validates(client, as_role, users, gear):
    as_role(A)
    client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], auto_validate="y"))
    loan = db.session.scalar(db.select(Loan))
    assert loan.status == LoanStatus.VALIDE and loan.validated_by == users[A]


@pytest.mark.parametrize("role,message", [
    (A, "Demande enregistrée : elle reste à valider."),
    (P, "Demande envoyée : un administrateur doit la valider."),
])
def test_request_message_depends_on_role(client, as_role, users, gear, role, message):
    """Un admin ne doit pas lire qu'« un administrateur doit valider » sa propre demande."""
    as_role(role)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]]), follow_redirects=True)
    assert message in r.text
    assert db.session.scalar(db.select(Loan)).status == LoanStatus.DEMANDE


def test_admin_without_guarantor(client, as_role, users, gear):
    as_role(A)
    client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], guarantor_id="0"))
    assert db.session.scalar(db.select(Loan)).guarantor is None


def test_admin_guarantor_must_be_staff(client, as_role, users, gear):
    as_role(A)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], guarantor_id=users[E].id))
    assert r.status_code == 200 and db.session.scalar(db.select(db.func.count(Loan.id))) == 0


def test_request_requires_borrower(client, as_role, users, gear):
    as_role(P)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], borrower_id="0"))
    assert "Choisissez l&#39;emprunteur." in r.text or "Choisissez l'emprunteur." in r.text


def test_request_without_items(client, as_role, users, gear):
    as_role(P)
    r = client.post("/prets/nouveau", data=loan_form(users))
    assert "Ajoutez au moins un objet" in r.text


def test_reservation_without_end(client, as_role, users, gear):
    as_role(P)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], end=""))
    assert "doit avoir une date de fin" in r.text


def test_garde_without_end(client, as_role, users, gear):
    as_role(P)
    client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], type=LoanType.GARDE, end=""))
    loan = db.session.scalar(db.select(Loan))
    assert loan.type == LoanType.GARDE and loan.end is None


def test_conflict_keeps_form_lines(client, as_role, users, gear):
    make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    as_role(P)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"], gear["cable"]],
                                                       qty={gear["cable"].id: 3}))
    assert "déjà pris" in r.text and db.session.scalar(db.select(db.func.count(Loan.id))) == 1
    assert 'name="item_ids" value="%d"' % gear["sax"].id in r.text
    assert 'name="qty_%d" value="3"' % gear["cable"].id in r.text


def test_competing_request_warns(client, as_role, users, gear):
    make_loan(users, gear["sax"])
    as_role(P)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]]), follow_redirects=True)
    assert "aussi demandé" in r.text


def test_invalid_quantity_rejected(client, as_role, users, gear):
    as_role(P)
    r = client.post("/prets/nouveau", data=loan_form(users, items=[gear["cable"]], qty={gear["cable"].id: 0}))
    assert "Quantité invalide" in r.text


def test_prefill_from_item_and_kit(client, as_role, users, gear):
    as_role(P)
    r = client.get(f"/prets/nouveau?item_id={gear['sax'].id}&kit_id={gear['kit'].id}&borrower_id={users[E].id}")
    assert "OHN-0001 — Saxophone" in r.text and "Kit Kit sono" in r.text
    assert f'<option selected value="{users[E].id}">' in r.text


def test_negative_deposit_rejected(client, as_role, users, gear):
    as_role(P)
    client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], deposit_amount="-10"))
    assert db.session.scalar(db.select(db.func.count(Loan.id))) == 0


def test_deposit_french_format(client, as_role, users, gear):
    as_role(P)
    client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], deposit_amount="150,50"))
    assert str(db.session.scalar(db.select(Loan)).deposit_amount) == "150.50"


# ---------------------------------------------------------------------------
# Fragments HTMX
# ---------------------------------------------------------------------------

def test_equipment_search(client, as_role, gear):
    as_role(P)
    r = client.get("/prets/recherche-materiel?q=o")
    assert "OHN-0001" in r.text and "OHN-0003" not in r.text  # réformé exclu
    assert "Kit sono" in r.text and "dans le kit Kit sono" in r.text
    assert client.get("/prets/recherche-materiel?q=").text.strip() == ""
    assert "Aucun résultat" in client.get("/prets/recherche-materiel?q=zzz").text


def test_equipment_search_accent_insensitive(client, as_role, gear):
    as_role(P)
    assert "OHN-0002" in client.get("/prets/recherche-materiel?q=FLUTE").text


@pytest.mark.parametrize("q", ["_", "%", "__", "%0%"])
def test_equipment_search_wildcards_are_literal(client, as_role, gear, q):
    as_role(P)
    assert "Aucun résultat" in client.get("/prets/recherche-materiel", query_string={"q": q}).text


def test_check_endpoint(client, as_role, users, gear):
    as_role(P)
    base = {"start": fmt(soon(1)), "end": fmt(soon(2))}
    assert client.post("/prets/verifier", data=base).text.strip() == ""
    r = client.post("/prets/verifier", data={**base, "item_ids": [gear["sax"].id]})
    assert "Tout le matériel est disponible" in r.text
    make_loan(users, gear["flute"], status=LoanStatus.VALIDE)
    r = client.post("/prets/verifier", data={**base, "item_ids": [gear["flute"].id]})
    assert "Indisponible" in r.text
    r = client.post("/prets/verifier", data={**base, "item_ids": [gear["enceinte"].id]})
    assert "prêtez le kit complet" in r.text
    r = client.post("/prets/verifier", data={"start": fmt(soon(2)), "end": fmt(soon(1)), "item_ids": [gear["sax"].id]})
    assert "après la date de début" in r.text
    r = client.post("/prets/verifier", data={"start": "n'importe quoi", "item_ids": [gear["sax"].id]})
    assert r.status_code == 200


def test_check_endpoint_warns_on_pending(client, as_role, users, gear):
    make_loan(users, gear["sax"])
    as_role(P)
    r = client.post("/prets/verifier", data={"start": fmt(soon(1)), "end": fmt(soon(2)), "item_ids": [gear["sax"].id]})
    assert "aussi demandé" in r.text


def test_borrower_cannot_use_fragments(client, as_role, gear):
    as_role(E)
    assert client.post("/prets/verifier", data={}).status_code == 403


# ---------------------------------------------------------------------------
# Actions sur un prêt
# ---------------------------------------------------------------------------

def test_prof_cannot_validate_or_refuse(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    as_role(P)
    assert client.post(f"/prets/{loan.id}/valider").status_code == 403
    assert client.post(f"/prets/{loan.id}/refuser").status_code == 403
    assert client.post(f"/prets/{loan.id}/fin", data={"end": fmt(soon(5))}).status_code == 403
    assert "Valider" not in client.get(f"/prets/{loan.id}").text
    db.session.refresh(loan)
    assert loan.status == LoanStatus.DEMANDE


def test_admin_validates(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    as_role(A)
    r = client.post(f"/prets/{loan.id}/valider", follow_redirects=True)
    assert "Prêt validé." in r.text
    db.session.refresh(loan)
    assert loan.status == LoanStatus.VALIDE


def test_validation_conflict_shown(client, as_role, users, gear):
    make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    loan = make_loan(users, gear["sax"])
    as_role(A)
    assert "Conflit" in client.get(f"/prets/{loan.id}").text
    r = client.post(f"/prets/{loan.id}/valider", follow_redirects=True)
    assert "déjà pris" in r.text
    db.session.refresh(loan)
    assert loan.status == LoanStatus.DEMANDE


def test_admin_refuses_with_reason(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    as_role(A)
    client.post(f"/prets/{loan.id}/refuser", data={"reason": "Pas dispo"})
    db.session.refresh(loan)
    assert loan.status == LoanStatus.REFUSE and loan.refusal_reason == "Pas dispo"
    assert "Pas dispo" in client.get(f"/prets/{loan.id}").text


def test_cancel_rights(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    other_prof = make_user(P, email="p2@ohn.test")
    as_role(E)
    assert client.post(f"/prets/{loan.id}/annuler").status_code == 403
    client.post("/deconnexion")
    login(client, other_prof)
    assert client.post(f"/prets/{loan.id}/annuler").status_code == 403
    as_role(P)
    client.post(f"/prets/{loan.id}/annuler")
    db.session.refresh(loan)
    assert loan.status == LoanStatus.ANNULE


def test_checkout_rights(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    other_prof = make_user(P, email="p2@ohn.test")
    client.post("/deconnexion")
    login(client, other_prof)
    assert client.post(f"/prets/{loan.id}/remise").status_code == 403
    as_role(E)
    assert client.post(f"/prets/{loan.id}/remise").status_code == 403
    as_role(P)
    assert "Remettre le matériel" in client.get(f"/prets/{loan.id}").text
    client.post(f"/prets/{loan.id}/remise")
    db.session.refresh(loan)
    assert loan.status == LoanStatus.EN_COURS and gear["sax"].status == ItemStatus.SORTI


def test_checkout_wrong_state_flashes(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    as_role(A)
    r = client.post(f"/prets/{loan.id}/remise", follow_redirects=True)
    assert "Action impossible" in r.text


def test_checkin_route(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], gear["flute"], status=LoanStatus.VALIDE)
    as_role(P)
    client.post(f"/prets/{loan.id}/remise")
    sax_line, flute_line = loan.lines
    r = client.get(f"/prets/{loan.id}")
    assert f'name="condition_{sax_line.id}"' in r.text
    client.post(f"/prets/{loan.id}/retour", data={"line_ids": [sax_line.id], f"condition_{sax_line.id}": "usé",
                                                   f"condition_{flute_line.id}": "HS"})
    db.session.refresh(loan)
    assert loan.status == LoanStatus.EN_COURS
    assert gear["sax"].condition == "usé" and gear["flute"].condition == "bon"
    client.post(f"/prets/{loan.id}/retour", data={"line_ids": [flute_line.id], f"condition_{flute_line.id}": "pire"})
    db.session.refresh(loan)
    assert loan.status == LoanStatus.RENDU and gear["flute"].condition == "bon"  # état inconnu ignoré


def test_checkin_nothing_selected(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.EN_COURS)
    as_role(A)
    r = client.post(f"/prets/{loan.id}/retour", data={}, follow_redirects=True)
    assert "au moins un élément" in r.text


def test_change_end_route(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    as_role(A)
    new_end = soon(10)
    client.post(f"/prets/{loan.id}/fin", data={"end": fmt(new_end)})
    db.session.refresh(loan)
    assert loan.end == new_end
    r = client.post(f"/prets/{loan.id}/fin", data={"end": "31/12/2030"}, follow_redirects=True)
    assert "Date invalide" in r.text


def test_loan_history_shown(client, as_role, users, gear):
    as_role(A)
    client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], auto_validate="y"))
    loan = db.session.scalar(db.select(Loan))
    r = client.get(f"/prets/{loan.id}")
    assert "Demande de réservation pour Emprunteur Test" in r.text and "Prêt validé" in r.text


def test_full_flow_end_to_end(client, as_role, users, gear):
    """Prof demande → admin valide → prof remet → prof récupère."""
    as_role(P)
    client.post("/prets/nouveau", data=loan_form(users, items=[gear["sax"]], kits=[gear["kit"]],
                                                   type=LoanType.GARDE, end=""))
    loan = db.session.scalar(db.select(Loan))
    as_role(A)
    client.post(f"/prets/{loan.id}/valider")
    as_role(E)
    assert f"/prets/{loan.id}" in client.get("/mes-prets").text
    assert "validé" in client.get("/notifications").text
    as_role(P)
    client.post(f"/prets/{loan.id}/remise")
    db.session.refresh(loan)
    assert gear["sax"].status == gear["enceinte"].status == ItemStatus.EN_GARDE
    client.post(f"/prets/{loan.id}/retour", data={"line_ids": [l.id for l in loan.lines]})
    db.session.refresh(loan)
    assert loan.status == LoanStatus.RENDU
    assert gear["sax"].status == gear["enceinte"].status == ItemStatus.DISPONIBLE


# ---------------------------------------------------------------------------
# Listes
# ---------------------------------------------------------------------------

def loan_ids(r):
    return {int(x) for x in re.findall(r'/prets/(\d+)"', r.text)}


def test_list_views(client, as_role, users, gear):
    pending = make_loan(users, gear["sax"])
    ongoing = make_loan(users, gear["flute"], status=LoanStatus.EN_COURS)
    late = make_loan(users, gear["cable"], status=LoanStatus.EN_COURS, start=soon(-5), end=soon(-1))
    done = make_loan(users, gear["sax"], status=LoanStatus.RENDU, start=soon(-30), end=soon(-29))
    as_role(P)
    assert loan_ids(client.get("/prets")) == {pending.id, ongoing.id, late.id}
    assert loan_ids(client.get("/prets?vue=demande")) == {pending.id}
    assert loan_ids(client.get("/prets?vue=en_cours")) == {ongoing.id, late.id}
    assert loan_ids(client.get("/prets?vue=retard")) == {late.id}
    assert loan_ids(client.get("/prets?vue=historique")) == {done.id}
    assert loan_ids(client.get("/prets?vue=nimporte")) == {pending.id, ongoing.id, late.id}
    assert "En retard" in client.get("/prets?vue=retard").text


def test_list_filters(client, as_role, users, gear):
    garde = make_loan(users, gear["sax"], loan_type=LoanType.GARDE)
    other = make_user(E, email="zoe@ohn.test")
    other.first_name, other.last_name = "Zoé", "Martin"
    resa = make_loan(users, gear["flute"], borrower=other)
    as_role(P)
    assert loan_ids(client.get("/prets?type=garde")) == {garde.id}
    assert loan_ids(client.get("/prets?type=reservation")) == {resa.id}
    assert loan_ids(client.get("/prets?q=zoe")) == {resa.id}
    assert loan_ids(client.get("/prets?q=concert")) == set()


@pytest.mark.parametrize("q", ["_", "%", "\\"])
def test_list_search_wildcards_are_literal(client, as_role, users, gear, q):
    make_loan(users, gear["sax"])
    as_role(P)
    assert loan_ids(client.get("/prets", query_string={"q": q})) == set()


def test_list_search_finds_literal_underscore(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    loan.title = "Concert_2027"
    db.session.commit()
    as_role(P)
    assert loan_ids(client.get("/prets", query_string={"q": "t_2"})) == {loan.id}


def test_mine(client, as_role, users, gear):
    as_borrower = make_loan(users, gear["sax"])
    other_borrower = make_user(E, email="x@ohn.test")
    as_guarantor = make_loan(users, gear["flute"], borrower=other_borrower)
    as_role(E)
    assert loan_ids(client.get("/mes-prets")) == {as_borrower.id}
    as_role(P)
    r = client.get("/mes-prets")
    assert "Prêts dont je suis garant" in r.text and as_guarantor.id in loan_ids(r)


def test_borrower_dashboard_lists_loans(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"], status=LoanStatus.EN_COURS)
    as_role(E)
    assert f"/prets/{loan.id}" in client.get("/").text


def test_admin_dashboard_pending(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    as_role(A)
    assert f"/prets/{loan.id}" in client.get("/").text


def test_item_detail_loans_section(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])
    kit_loan = make_loan(users, kits=[gear["kit"]], start=soon(5), end=soon(6))
    as_role(P)
    r = client.get(f"/objets/{gear['sax'].id}")
    assert f"/prets/{loan.id}" in r.text and f"/prets/nouveau?item_id={gear['sax'].id}" in r.text
    r = client.get(f"/objets/{gear['enceinte'].id}")
    assert f"/prets/{kit_loan.id}" in r.text and f"/prets/nouveau?kit_id={gear['kit'].id}" in r.text
    assert "Prêter" not in client.get(f"/objets/{gear['old'].id}").text


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def test_notifications_badge_and_read(client, as_role, users):
    db.session.add_all([Notification(user_id=users[E].id, message="Bonjour", link="/mes-prets"),
                        Notification(user_id=users[P].id, message="Pas pour toi")])
    db.session.commit()
    as_role(E)
    assert 'class="bell-count">1<' in client.get("/").text
    r = client.get("/notifications")
    assert "Bonjour" in r.text and "Pas pour toi" not in r.text and 'class="unread"' in r.text
    assert "bell-count" not in client.get("/").text
    assert 'class="unread"' not in client.get("/notifications").text
