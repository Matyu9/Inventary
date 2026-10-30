import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import (Activity, Category, CategoryType, ConsumptionMode, Item, ItemStatus, Kit, Loan, LoanLine,
                        LoanStatus, LoanType, Location, Notification, Role, StockMovement, Tracking)
from app.services import stock

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def gear(app):
    perc = Category(name="Percussions")
    conso = Category(name="Conso", type=CategoryType.CONSOMMABLE)
    loc = Location(name="Local")
    kit = Kit(name="Batterie", location=loc)
    caisse = Item(code="OHN-0001", name="Caisse claire", category=perc, kit=kit)
    tom = Item(code="OHN-0002", name="Tom basse", category=perc)
    cymbale = Item(code="OHN-0003", name="Cymbale crash", category=perc)
    reeds = Item(code="OHN-0004", name="Anches Vandoren", category=conso, tracking=Tracking.QUANTITE, quantity=10,
                 unit="boîte", low_stock_threshold=3, consumption_mode=ConsumptionMode.CONSOMME)
    old = Item(code="OHN-0005", name="Tom fendu", category=perc, status=ItemStatus.REFORME)
    db.session.add_all([kit, caisse, tom, cymbale, reeds, old])
    db.session.commit()
    return dict(kit=kit, caisse=caisse, tom=tom, cymbale=cymbale, reeds=reeds, old=old, loc=loc)


def loan_on(users, *, kit=None, item=None, status=LoanStatus.VALIDE):
    loan = Loan(type=LoanType.GARDE, status=status, borrower=users[E], guarantor=users[P], requested_by=users[P],
                start=datetime.now() + timedelta(days=1))
    loan.lines = [LoanLine(kit=kit, item=item, quantity=1)]
    db.session.add(loan)
    db.session.commit()
    return loan


# ---------------------------------------------------------------------------
# Kits : accès et CRUD
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url_fn,allowed", [
    (lambda g: "/kits", {A, P}),
    (lambda g: f"/kits/{g['kit'].id}", {A, P}),
    (lambda g: "/kits/nouveau", {A, P}),
    (lambda g: f"/kits/{g['kit'].id}/modifier", {A, P}),
    (lambda g: f"/kits/{g['kit'].id}/candidats?q=tom", {A, P}),
    (lambda g: f"/kits/{g['kit'].id}/qr.svg", {A, P}),
    (lambda g: "/consommables", {A, P}),
])
def test_access(client, as_role, gear, url_fn, allowed):
    for role in Role.ALL:
        as_role(role)
        assert client.get(url_fn(gear)).status_code == (200 if role in allowed else 403), role


def test_create_kit(client, as_role, gear):
    as_role(P)
    r = client.post("/kits/nouveau", data={"name": " Sono ", "location_id": gear["loc"].id, "description": ""})
    kit = db.session.scalar(db.select(Kit).filter_by(name="Sono"))
    assert r.headers["Location"].endswith(f"/kits/{kit.id}") and kit.location == gear["loc"]
    assert db.session.scalar(db.select(Activity).filter_by(action="kit.create", kit_id=kit.id))


def test_create_kit_requires_name(client, as_role, gear):
    as_role(P)
    assert "aria-invalid" in client.post("/kits/nouveau", data={"name": "", "location_id": "0"}).text


def test_edit_kit(client, as_role, gear):
    as_role(P)
    client.post(f"/kits/{gear['kit'].id}/modifier", data={"name": "Batterie jazz", "location_id": "0"})
    db.session.refresh(gear["kit"])
    assert gear["kit"].name == "Batterie jazz" and gear["kit"].location is None


def test_kit_list_and_detail(client, as_role, gear):
    as_role(P)
    r = client.get("/kits")
    assert "Batterie" in r.text and "<td>1</td>" in r.text
    r = client.get(f"/kits/{gear['kit'].id}")
    assert "OHN-0001" in r.text and "KIT-001" in r.text and f"/kits/{gear['kit'].id}/qr.svg" in r.text
    assert 'data-qr-zoom data-qr-caption="Kit Batterie"' in r.text
    assert f"/prets/nouveau?kit_id={gear['kit'].id}" in r.text


def test_kit_list_bulk_labels(client, as_role, gear):
    as_role(P)
    r = client.get("/kits")
    assert 'action="/etiquettes"' in r.text and f'name="kit_ids" value="{gear["kit"].id}"' in r.text
    assert 'name="avec_objets"' in r.text and 'name="kits" value="tous"' in r.text


def test_kit_404(client, as_role):
    as_role(A)
    for url in ("/kits/9", "/kits/9/modifier", "/kits/9/qr.svg"):
        assert client.get(url).status_code == 404


@pytest.mark.parametrize("statuses,expected", [
    ([], "vide"),
    ([ItemStatus.DISPONIBLE, ItemStatus.DISPONIBLE], ItemStatus.DISPONIBLE),
    ([ItemStatus.DISPONIBLE, ItemStatus.EN_GARDE], ItemStatus.EN_GARDE),
    ([ItemStatus.SORTI], ItemStatus.SORTI),
    ([ItemStatus.DISPONIBLE, ItemStatus.EN_ENTRETIEN], "incomplet"),
    ([ItemStatus.REFORME], "incomplet"),
])
def test_kit_status(app, statuses, expected):
    from app.blueprints.kits import kit_status
    kit = Kit(name="K", items=[Item(code=f"X-{n}", name="x", status=s) for n, s in enumerate(statuses)])
    assert kit_status(kit) == expected


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def test_candidates(client, as_role, gear):
    as_role(P)
    assert "OHN-" not in client.get(f"/kits/{gear['kit'].id}/candidats", query_string={"q": "%"}).text
    r = client.get(f"/kits/{gear['kit'].id}/candidats?q=tom")
    assert "OHN-0002" in r.text and "OHN-0005" not in r.text  # réformé exclu
    assert "OHN-0001" not in client.get(f"/kits/{gear['kit'].id}/candidats?q=caisse").text  # déjà dans un kit
    assert "OHN-0004" not in client.get(f"/kits/{gear['kit'].id}/candidats?q=anches").text  # consommable
    assert client.get(f"/kits/{gear['kit'].id}/candidats").text.strip() == ""


def test_add_and_remove_item(client, as_role, gear):
    as_role(P)
    client.post(f"/kits/{gear['kit'].id}/objets", data={"item_id": gear["tom"].id})
    db.session.refresh(gear["tom"])
    assert gear["tom"].kit == gear["kit"]
    client.post(f"/kits/{gear['kit'].id}/objets/{gear['tom'].id}/retirer")
    db.session.refresh(gear["tom"])
    assert gear["tom"].kit is None
    actions = db.session.scalars(db.select(Activity.action).where(Activity.item_id == gear["tom"].id)).all()
    assert actions == ["kit.add", "kit.remove"]


def test_add_item_already_in_other_kit(client, as_role, gear):
    other = Kit(name="Autre")
    db.session.add(other)
    db.session.commit()
    as_role(P)
    r = client.post(f"/kits/{other.id}/objets", data={"item_id": gear["caisse"].id}, follow_redirects=True)
    assert "fait déjà partie du kit « Batterie »" in r.text


def test_add_consumable_refused(client, as_role, gear):
    as_role(P)
    r = client.post(f"/kits/{gear['kit'].id}/objets", data={"item_id": gear["reeds"].id}, follow_redirects=True)
    assert "consommables ne peuvent pas" in r.text


def test_add_unknown_item(client, as_role, gear):
    as_role(P)
    assert client.post(f"/kits/{gear['kit'].id}/objets", data={"item_id": 999}).status_code == 404
    assert client.post(f"/kits/{gear['kit'].id}/objets", data={}).status_code == 404


def test_remove_item_not_in_kit(client, as_role, gear):
    as_role(P)
    assert client.post(f"/kits/{gear['kit'].id}/objets/{gear['tom'].id}/retirer").status_code == 404


def test_item_with_active_loan_cannot_join_kit(client, as_role, users, gear):
    loan = loan_on(users, item=gear["tom"])
    as_role(P)
    r = client.post(f"/kits/{gear['kit'].id}/objets", data={"item_id": gear["tom"].id}, follow_redirects=True)
    assert f"prêt n°{loan.id}" in r.text and gear["tom"].kit is None


def test_returned_item_can_join_kit(client, as_role, users, gear):
    loan = loan_on(users, item=gear["tom"], status=LoanStatus.RENDU)
    as_role(P)
    client.post(f"/kits/{gear['kit'].id}/objets", data={"item_id": gear["tom"].id})
    db.session.refresh(gear["tom"])
    assert gear["tom"].kit == gear["kit"] and loan


@pytest.mark.parametrize("status", [LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS])
def test_kit_composition_locked_during_loan(client, as_role, users, gear, status):
    loan_on(users, kit=gear["kit"], status=status)
    as_role(P)
    r = client.post(f"/kits/{gear['kit'].id}/objets", data={"item_id": gear["tom"].id}, follow_redirects=True)
    assert "composition ne peut pas changer" in r.text
    client.post(f"/kits/{gear['kit'].id}/objets/{gear['caisse'].id}/retirer")
    db.session.refresh(gear["caisse"])
    assert gear["caisse"].kit == gear["kit"]
    assert "composition est verrouillée" in client.get(f"/kits/{gear['kit'].id}").text


def test_delete_kit(client, as_role, gear):
    as_role(P)
    assert client.post(f"/kits/{gear['kit'].id}/supprimer").status_code == 403
    as_role(A)
    client.post(f"/kits/{gear['kit'].id}/supprimer")
    assert db.session.get(Kit, gear["kit"].id) is None
    db.session.refresh(gear["caisse"])
    assert gear["caisse"].kit is None


def test_delete_kit_with_history_refused(client, as_role, users, gear):
    loan_on(users, kit=gear["kit"], status=LoanStatus.RENDU)
    as_role(A)
    r = client.post(f"/kits/{gear['kit'].id}/supprimer", follow_redirects=True)
    assert "historique de prêts" in r.text and db.session.get(Kit, gear["kit"].id)


def test_kit_detail_lists_loans(client, as_role, users, gear):
    loan = loan_on(users, kit=gear["kit"])
    as_role(P)
    assert f"/prets/{loan.id}" in client.get(f"/kits/{gear['kit'].id}").text


def test_item_detail_links_kit(client, as_role, gear):
    as_role(P)
    assert f"/kits/{gear['kit'].id}" in client.get(f"/objets/{gear['caisse'].id}").text


def test_kit_labels(client, as_role, gear):
    as_role(P)
    r = client.get("/etiquettes", query_string={"kit_ids": gear["kit"].id, "ids": [gear["caisse"].id]})
    assert re.findall(r'<div class="code">([^<]+)</div>', r.text) == ["KIT-001", "OHN-0001"]
    r = client.get("/etiquettes", query_string={"kit_ids": gear["kit"].id})
    assert re.findall(r'<div class="code">([^<]+)</div>', r.text) == ["KIT-001"]


def test_kit_qr_scan_resolves(client, as_role, gear):
    as_role(P)
    r = client.get(f"/scan/resoudre?code=/q/{gear['kit'].qr_token}")
    assert r.headers["Location"].endswith(f"/kits/{gear['kit'].id}")


def test_full_kit_loan_flow(client, as_role, users, gear):
    """Un kit prêté met tous ses objets en garde, et les libère au retour."""
    from app.services import loans as svc
    with client.application.test_request_context():
        loan, _ = svc.create_request(loan_type=LoanType.GARDE, borrower=users[E], guarantor=users[P],
                                     requested_by=users[P], start=datetime.now(), end=None,
                                     lines=[LoanLine(kit=gear["kit"], quantity=1)])
        svc.validate(loan, users[A])
        svc.checkout(loan, users[A])
        assert gear["caisse"].status == ItemStatus.EN_GARDE
        svc.checkin(loan, users[A], {loan.lines[0].id: None})
        assert gear["caisse"].status == ItemStatus.DISPONIBLE and loan.status == LoanStatus.RENDU


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason,amount,expected", [
    ("achat", 5, 15), ("retour", 1, 11), ("distribution", 4, 6), ("perte", 10, 0), ("inventaire", 7, 7),
    ("inventaire", 0, 0),
])
def test_stock_moves(app, users, gear, reason, amount, expected):
    delta = stock.move(gear["reeds"], reason, amount, users[P], "  note  ")
    db.session.commit()
    assert gear["reeds"].quantity == expected and delta == expected - 10
    movement = db.session.scalar(db.select(StockMovement))
    assert movement.delta == delta and movement.reason == reason and movement.comment == "note"


@pytest.mark.parametrize("reason,amount,message", [
    ("distribution", 11, "Stock insuffisant : il reste 10 boîte"),
    ("achat", 0, "Quantité invalide"),
    ("achat", -3, "Quantité invalide"),
    ("achat", None, "Quantité invalide"),
    ("vol", 1, "Motif inconnu"),
    ("inventaire", 10, "ne change pas"),
])
def test_stock_errors(app, users, gear, reason, amount, message):
    with pytest.raises(stock.StockError, match=message):
        stock.move(gear["reeds"], reason, amount, users[P])
    assert gear["reeds"].quantity == 10


def test_stock_individual_item_refused(app, users, gear):
    with pytest.raises(stock.StockError, match="pas suivi en quantité"):
        stock.move(gear["tom"], "achat", 1, users[P])


def test_low_stock_notifies_admins_once(app, users, gear):
    stock.move(gear["reeds"], "distribution", 7, users[P])  # 3 = seuil
    stock.move(gear["reeds"], "distribution", 1, users[P])  # déjà bas : pas de nouvelle alerte
    db.session.commit()
    notes = db.session.scalars(db.select(Notification)).all()
    assert len(notes) == 1 and notes[0].user_id == users[A].id and "Stock bas" in notes[0].message


def test_stock_route(client, as_role, gear):
    as_role(P)
    r = client.post(f"/objets/{gear['reeds'].id}/stock", data={"reason": "distribution", "amount": "2"})
    assert r.headers["Location"].endswith(f"/objets/{gear['reeds'].id}#stock")
    db.session.refresh(gear["reeds"])
    assert gear["reeds"].quantity == 8
    r = client.post(f"/objets/{gear['reeds'].id}/stock", data={"reason": "achat", "amount": "4", "next": "list"})
    assert r.headers["Location"].endswith("/consommables")
    r = client.post(f"/objets/{gear['reeds'].id}/stock", data={"reason": "distribution", "amount": "99"},
                    follow_redirects=True)
    assert "Stock insuffisant" in r.text
    r = client.post(f"/objets/{gear['reeds'].id}/stock", data={"reason": "achat", "amount": "abc"},
                    follow_redirects=True)
    assert "Quantité invalide" in r.text


def test_stock_route_rights(client, as_role, gear):
    as_role(E)
    assert client.post(f"/objets/{gear['reeds'].id}/stock", data={"reason": "achat", "amount": 1}).status_code == 403
    as_role(P)
    assert client.post("/objets/999/stock", data={"reason": "achat", "amount": 1}).status_code == 404


def test_consumables_page(client, as_role, users, gear):
    stock.move(gear["reeds"], "distribution", 8, users[P], "Classe de clarinette")
    db.session.commit()
    as_role(P)
    r = client.get("/consommables")
    assert "Anches Vandoren" in r.text and "stock bas" in r.text and "Classe de clarinette" in r.text
    assert "Caisse claire" not in r.text


def test_consumables_tables_become_cards_on_mobile(client, as_role, users, gear):
    """Sur mobile, le formulaire de mouvement était caché dans un tableau qui défilait de côté."""
    stock.move(gear["reeds"], "achat", 1, users[P])
    db.session.commit()
    as_role(P)
    html = client.get("/consommables").text
    assert html.count('<table class="striped responsive">') == 2
    cells = re.findall(r"<td( data-label=\"[^\"]*\")?>", html)
    assert cells and all(cells), "chaque cellule doit avoir un data-label pour la vue en cartes"


def test_item_detail_stock_section(client, as_role, users, gear):
    stock.move(gear["reeds"], "achat", 5, users[P], "Commande")
    db.session.commit()
    as_role(P)
    r = client.get(f"/objets/{gear['reeds'].id}")
    assert 'id="stock"' in r.text and "+5" in r.text and "Commande" in r.text
    assert 'id="stock"' not in client.get(f"/objets/{gear['tom'].id}").text


def test_stock_history_in_item_activity(client, as_role, users, gear):
    stock.move(gear["reeds"], "perte", 2, users[P])
    db.session.commit()
    entry = db.session.scalar(db.select(Activity).filter_by(action="stock.move"))
    assert entry.changes == {"quantity": [10, 8]} and entry.item_id == gear["reeds"].id


def test_nav_has_kits_and_consumables(client, as_role, gear):
    as_role(P)
    r = client.get("/")
    assert 'href="/kits"' in r.text and 'href="/consommables"' in r.text
