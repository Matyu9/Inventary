from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import (Category, CategoryType, ConsumptionMode, Item, ItemStatus, Kit, LoanLine, LoanStatus,
                        LoanType, Notification, Role, StockMovement, Tracking)
from app.services import loans as svc

from .conftest import make_user

G, R = LoanType.GARDE, LoanType.RESERVATION


def d(day, hour=10):
    return datetime(2030, 1, day, hour)


@pytest.fixture
def ctx(app):
    with app.test_request_context():
        bois = Category(name="Bois")
        conso = Category(name="Conso", type=CategoryType.CONSOMMABLE)
        sax = Item(code="OHN-0001", name="Sax", category=bois)
        flute = Item(code="OHN-0002", name="Flûte", category=bois)
        cable = Item(code="OHN-0003", name="Câble XLR", category=conso, tracking=Tracking.QUANTITE, quantity=10,
                     consumption_mode=ConsumptionMode.RETOURNABLE)
        reeds = Item(code="OHN-0004", name="Anches", category=conso, tracking=Tracking.QUANTITE, quantity=5,
                     consumption_mode=ConsumptionMode.CONSOMME, unit="boîte")
        kit = Kit(name="Batterie")
        caisse = Item(code="OHN-0005", name="Caisse claire", category=bois, kit=kit)
        cymbale = Item(code="OHN-0006", name="Cymbale", category=bois, kit=kit)
        db.session.add_all([sax, flute, cable, reeds, kit, caisse, cymbale])
        db.session.commit()
        users = {r: make_user(r) for r in Role.ALL}
        yield dict(sax=sax, flute=flute, cable=cable, reeds=reeds, kit=kit, caisse=caisse, cymbale=cymbale, **users)


def line(item=None, kit=None, qty=1):
    return LoanLine(item=item, kit=kit, quantity=qty)


def request(ctx, *lines, loan_type=R, start=None, end=None, by=Role.PROF):
    loan, warnings = svc.create_request(
        loan_type=loan_type, borrower=ctx[Role.EMPRUNTEUR], guarantor=ctx[Role.PROF], requested_by=ctx[by],
        start=start or d(10), end=end if end is not None or loan_type == G else d(12), lines=list(lines))
    db.session.commit()
    return loan, warnings


def approved(ctx, *lines, **kw):
    loan, _ = request(ctx, *lines, **kw)
    svc.validate(loan, ctx[Role.ADMIN])
    db.session.commit()
    return loan


# ---------------------------------------------------------------------------
# Création et validation des données
# ---------------------------------------------------------------------------

def test_create_request(ctx):
    loan, warnings = request(ctx, line(ctx["sax"]))
    assert loan.status == LoanStatus.DEMANDE and warnings == [] and loan.requested_by == ctx[Role.PROF]
    notes = db.session.scalars(db.select(Notification)).all()
    assert [n.user_id for n in notes] == [ctx[Role.ADMIN].id] and f"/prets/{loan.id}" == notes[0].link


def test_admin_request_does_not_notify_self(ctx):
    request(ctx, line(ctx["sax"]), by=Role.ADMIN)
    assert db.session.scalar(db.select(db.func.count(Notification.id))) == 0


@pytest.mark.parametrize("kw,message", [
    ({"loan_type": R, "end": None}, "doit avoir une date de fin"),
    ({"start": d(12), "end": d(10)}, "après la date de début"),
    ({"start": d(10), "end": d(10)}, "après la date de début"),
    ({"loan_type": "vente"}, "Type de prêt inconnu"),
])
def test_invalid_periods(ctx, kw, message):
    loan_type = kw.pop("loan_type", R)
    with pytest.raises(svc.LoanError, match=message):
        svc.create_request(loan_type=loan_type, borrower=ctx[Role.EMPRUNTEUR], guarantor=None,
                           requested_by=ctx[Role.PROF], start=kw.get("start", d(10)), end=kw.get("end"),
                           lines=[line(ctx["sax"])])


def test_missing_start(ctx):
    with pytest.raises(svc.LoanError, match="début est obligatoire"):
        svc.create_request(loan_type=G, borrower=ctx[Role.EMPRUNTEUR], guarantor=None, requested_by=ctx[Role.PROF],
                           start=None, end=None, lines=[line(ctx["sax"])])


def test_garde_without_end(ctx):
    loan, _ = request(ctx, line(ctx["sax"]), loan_type=G)
    assert loan.end is None


def test_requires_lines(ctx):
    with pytest.raises(svc.LoanError, match="au moins un objet"):
        request(ctx)


def test_item_of_kit_cannot_be_lent_alone(ctx):
    with pytest.raises(svc.LoanError, match="fait partie du kit « Batterie »"):
        request(ctx, line(ctx["caisse"]))


def test_individual_item_quantity_one(ctx):
    with pytest.raises(svc.LoanError, match="quantité 1"):
        request(ctx, line(ctx["sax"], qty=2))


def test_duplicate_item(ctx):
    with pytest.raises(svc.LoanError, match="plusieurs fois"):
        request(ctx, line(ctx["sax"]), line(ctx["sax"]))


def test_same_kit_twice(ctx):
    with pytest.raises(svc.LoanError, match="plusieurs fois"):
        request(ctx, line(kit=ctx["kit"]), line(kit=ctx["kit"]))


def test_quantity_lines_merge(ctx):
    problems, _ = svc.check_availability([line(ctx["cable"], qty=6), line(ctx["cable"], qty=5)], d(10), d(12))
    assert problems and "10 disponible(s)" in problems[0] and "11 demandé(s)" in problems[0]


@pytest.mark.parametrize("status", [ItemStatus.REFORME, ItemStatus.EN_ENTRETIEN])
def test_unavailable_item(ctx, status):
    ctx["sax"].status = status
    with pytest.raises(svc.LoanError, match=f"indisponible \\({status}\\)"):
        request(ctx, line(ctx["sax"]))


# ---------------------------------------------------------------------------
# Conflits de période
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("start,end,conflict", [
    (d(11), d(13), True),  # chevauchement
    (d(8), d(11), True),
    (d(9), d(14), True),  # englobe
    (d(10, 12), d(11), True),  # inclus
    (d(12), d(14), False),  # commence à la fin : intervalles semi-ouverts
    (d(5), d(10), False),  # finit au début
    (d(20), d(21), False),
])
def test_reservation_overlap(ctx, start, end, conflict):
    approved(ctx, line(ctx["sax"]))  # du 10 au 12
    problems, _ = svc.check_availability([line(ctx["sax"])], start, end)
    assert bool(problems) is conflict
    if conflict:
        assert "déjà pris" in problems[0] and "Emprunteur Test" in problems[0]


def test_open_garde_blocks_future(ctx):
    approved(ctx, line(ctx["sax"]), loan_type=G, start=d(10))
    assert svc.check_availability([line(ctx["sax"])], d(25), d(26))[0]
    assert not svc.check_availability([line(ctx["sax"])], d(1), d(5))[0]
    assert svc.check_availability([line(ctx["sax"])], d(1), None)[0]  # garde sans fin demandée avant


def test_request_without_end_conflicts_with_later_reservation(ctx):
    approved(ctx, line(ctx["sax"]), start=d(20), end=d(22))
    assert svc.check_availability([line(ctx["sax"])], d(1), None)[0]


def test_pending_requests_only_warn(ctx):
    request(ctx, line(ctx["sax"]))
    problems, warnings = svc.check_availability([line(ctx["sax"])], d(11), d(13))
    assert problems == [] and "aussi demandé" in warnings[0]


def test_creating_conflicting_request_is_refused(ctx):
    approved(ctx, line(ctx["sax"]))
    with pytest.raises(svc.LoanError, match="déjà pris"):
        request(ctx, line(ctx["sax"]), start=d(11), end=d(13))


def test_competing_request_returns_warning(ctx):
    request(ctx, line(ctx["sax"]))
    _, warnings = request(ctx, line(ctx["sax"]), start=d(11), end=d(13))
    assert len(warnings) == 1


@pytest.mark.parametrize("final", ["refuse", "cancel"])
def test_refused_or_cancelled_do_not_block(ctx, final):
    loan, _ = request(ctx, line(ctx["sax"]))
    if final == "refuse":
        svc.refuse(loan, ctx[Role.ADMIN])
    else:
        svc.validate(loan, ctx[Role.ADMIN])
        svc.cancel(loan, ctx[Role.PROF])
    assert svc.check_availability([line(ctx["sax"])], d(10), d(12)) == ([], [])


def test_overdue_loan_blocks_until_returned(ctx):
    now = datetime.now()
    loan = approved(ctx, line(ctx["sax"]), start=now - timedelta(days=5), end=now - timedelta(days=1))
    svc.checkout(loan, ctx[Role.ADMIN])
    assert loan.is_overdue
    assert svc.check_availability([line(ctx["sax"])], now + timedelta(days=30), now + timedelta(days=31))[0]
    svc.checkin(loan, ctx[Role.ADMIN], {loan.lines[0].id: None})
    assert not svc.check_availability([line(ctx["sax"])], now + timedelta(days=30), now + timedelta(days=31))[0]


def test_partial_return_frees_returned_items(ctx):
    loan = approved(ctx, line(ctx["sax"]), line(ctx["flute"]), loan_type=G)
    svc.checkout(loan, ctx[Role.ADMIN])
    sax_line = next(l for l in loan.lines if l.item == ctx["sax"])
    svc.checkin(loan, ctx[Role.ADMIN], {sax_line.id: None})
    assert loan.status == LoanStatus.EN_COURS
    assert not svc.check_availability([line(ctx["sax"])], d(20), d(21))[0]
    assert svc.check_availability([line(ctx["flute"])], d(20), d(21))[0]


def test_kit_conflicts(ctx):
    approved(ctx, line(kit=ctx["kit"]))
    problems, _ = svc.check_availability([line(kit=ctx["kit"])], d(11), d(13))
    assert len(problems) == 2  # les deux objets du kit


def test_exclude_self(ctx):
    loan = approved(ctx, line(ctx["sax"]))
    assert svc.check_availability(loan.lines, loan.start, loan.end, exclude_loan_id=loan.id) == ([], [])


def test_returnable_quantity_capacity(ctx):
    approved(ctx, line(ctx["cable"], qty=7))
    assert not svc.check_availability([line(ctx["cable"], qty=3)], d(11), d(13))[0]
    problems, _ = svc.check_availability([line(ctx["cable"], qty=4)], d(11), d(13))
    assert "3 disponible(s) sur la période, 4 demandé(s)" in problems[0]
    assert not svc.check_availability([line(ctx["cable"], qty=10)], d(15), d(16))[0]


def test_consumable_stock_check(ctx):
    assert not svc.check_availability([line(ctx["reeds"], qty=5)], d(10), d(12))[0]
    problems, _ = svc.check_availability([line(ctx["reeds"], qty=6)], d(10), d(12))
    assert "stock insuffisant (5 boîte disponible(s))" in problems[0]


def test_consumables_never_block_periods(ctx):
    approved(ctx, line(ctx["reeds"], qty=3))
    assert not svc.check_availability([line(ctx["reeds"], qty=5)], d(10), d(12))[0]


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def test_validate(ctx):
    loan, _ = request(ctx, line(ctx["sax"]))
    db.session.execute(db.delete(Notification))
    svc.validate(loan, ctx[Role.ADMIN])
    assert loan.status == LoanStatus.VALIDE and loan.validated_by == ctx[Role.ADMIN] and loan.validated_at
    recipients = set(db.session.scalars(db.select(Notification.user_id)))
    assert recipients == {ctx[Role.EMPRUNTEUR].id, ctx[Role.PROF].id}


def test_validation_rechecks_conflicts(ctx):
    first, _ = request(ctx, line(ctx["sax"]))
    second, _ = request(ctx, line(ctx["sax"]), start=d(11), end=d(13))
    svc.validate(first, ctx[Role.ADMIN])
    with pytest.raises(svc.LoanError, match="déjà pris"):
        svc.validate(second, ctx[Role.ADMIN])
    assert second.status == LoanStatus.DEMANDE


def test_validation_rechecks_item_status(ctx):
    loan, _ = request(ctx, line(ctx["sax"]))
    ctx["sax"].status = ItemStatus.EN_ENTRETIEN
    with pytest.raises(svc.LoanError, match="indisponible"):
        svc.validate(loan, ctx[Role.ADMIN])


@pytest.mark.parametrize("action", ["validate", "refuse"])
def test_cannot_validate_twice(ctx, action):
    loan = approved(ctx, line(ctx["sax"]))
    with pytest.raises(svc.LoanError, match="Action impossible"):
        getattr(svc, action)(loan, ctx[Role.ADMIN])


def test_refuse_with_reason(ctx):
    loan, _ = request(ctx, line(ctx["sax"]))
    svc.refuse(loan, ctx[Role.ADMIN], "  Instrument réservé pour le concert  ")
    assert loan.status == LoanStatus.REFUSE and loan.refusal_reason == "Instrument réservé pour le concert"
    assert loan.status_label == "Refusé"


def test_refuse_reason_truncated(ctx):
    loan, _ = request(ctx, line(ctx["sax"]))
    svc.refuse(loan, ctx[Role.ADMIN], "x" * 400)
    assert len(loan.refusal_reason) == 255


def test_cancel_rules(ctx):
    loan = approved(ctx, line(ctx["sax"]))
    svc.checkout(loan, ctx[Role.ADMIN])
    with pytest.raises(svc.LoanError):
        svc.cancel(loan, ctx[Role.ADMIN])


def test_cancel_notifies_others_only(ctx):
    loan, _ = request(ctx, line(ctx["sax"]))
    db.session.execute(db.delete(Notification))
    svc.cancel(loan, ctx[Role.PROF])
    assert set(db.session.scalars(db.select(Notification.user_id))) == {ctx[Role.EMPRUNTEUR].id}


@pytest.mark.parametrize("validated", [False, True])
def test_cancel_without_guarantor(ctx, validated):
    """Un admin peut créer un prêt sans garant : l'annulation ne doit pas planter."""
    loan, _ = svc.create_request(loan_type=R, borrower=ctx[Role.EMPRUNTEUR], guarantor=None,
                                 requested_by=ctx[Role.ADMIN], start=d(10), end=d(12), lines=[line(ctx["sax"])])
    if validated:
        svc.validate(loan, ctx[Role.ADMIN])
    db.session.execute(db.delete(Notification))
    svc.cancel(loan, ctx[Role.ADMIN])
    assert loan.status == LoanStatus.ANNULE
    assert set(db.session.scalars(db.select(Notification.user_id))) == {ctx[Role.EMPRUNTEUR].id}


@pytest.mark.parametrize("loan_type,status", [(G, ItemStatus.EN_GARDE), (R, ItemStatus.SORTI)])
def test_checkout_sets_status(ctx, loan_type, status):
    ctx["sax"].condition = "usé"
    loan = approved(ctx, line(ctx["sax"]), line(kit=ctx["kit"]), loan_type=loan_type)
    svc.checkout(loan, ctx[Role.PROF])
    assert loan.status == LoanStatus.EN_COURS and loan.checked_out_at
    assert ctx["sax"].status == ctx["caisse"].status == ctx["cymbale"].status == status
    assert loan.lines[0].condition_out == "usé"


def test_checkout_requires_validation(ctx):
    loan, _ = request(ctx, line(ctx["sax"]))
    with pytest.raises(svc.LoanError, match="demandé"):
        svc.checkout(loan, ctx[Role.ADMIN])


def test_checkout_blocked_if_item_went_to_repair(ctx):
    loan = approved(ctx, line(ctx["sax"]))
    ctx["sax"].status = ItemStatus.EN_ENTRETIEN
    with pytest.raises(svc.LoanError, match="indisponible"):
        svc.checkout(loan, ctx[Role.ADMIN])
    assert loan.status == LoanStatus.VALIDE


def test_checkout_consumables(ctx):
    loan = approved(ctx, line(ctx["reeds"], qty=2), line(ctx["sax"]))
    svc.checkout(loan, ctx[Role.ADMIN])
    db.session.commit()
    assert ctx["reeds"].quantity == 3
    movement = db.session.scalar(db.select(StockMovement))
    assert movement.delta == -2 and movement.reason == "distribution" and movement.loan_id == loan.id
    reeds_line = next(l for l in loan.lines if l.item == ctx["reeds"])
    assert reeds_line.returned_at is not None and loan.status == LoanStatus.EN_COURS


def test_consumables_only_loan_closes_on_checkout(ctx):
    loan = approved(ctx, line(ctx["reeds"], qty=1))
    svc.checkout(loan, ctx[Role.ADMIN])
    assert loan.status == LoanStatus.RENDU and loan.returned_at


def test_checkout_returnable_quantity_keeps_stock(ctx):
    loan = approved(ctx, line(ctx["cable"], qty=4))
    svc.checkout(loan, ctx[Role.ADMIN])
    assert ctx["cable"].quantity == 10 and ctx["cable"].status == ItemStatus.DISPONIBLE


def test_checkin_full_with_condition(ctx):
    loan = approved(ctx, line(ctx["sax"]), line(kit=ctx["kit"]), loan_type=G)
    loan.deposit_amount = 50
    svc.checkout(loan, ctx[Role.ADMIN])
    sax_line, kit_line = loan.lines
    svc.checkin(loan, ctx[Role.PROF], {sax_line.id: "à réparer", kit_line.id: None}, deposit_returned=True)
    assert loan.status == LoanStatus.RENDU and loan.returned_at and loan.deposit_returned
    assert ctx["sax"].status == ItemStatus.DISPONIBLE and ctx["sax"].condition == "à réparer"
    assert sax_line.condition_in == "à réparer" and kit_line.condition_in is None
    assert ctx["caisse"].status == ItemStatus.DISPONIBLE and ctx["caisse"].condition == "bon"


def test_checkin_partial_then_rest(ctx):
    loan = approved(ctx, line(ctx["sax"]), line(ctx["flute"]))
    svc.checkout(loan, ctx[Role.ADMIN])
    first, second = loan.lines
    svc.checkin(loan, ctx[Role.ADMIN], {first.id: None})
    assert loan.status == LoanStatus.EN_COURS and ctx["flute"].status == ItemStatus.SORTI
    svc.checkin(loan, ctx[Role.ADMIN], {first.id: "HS", second.id: None})  # ligne déjà rendue ignorée
    assert loan.status == LoanStatus.RENDU and first.condition_in is None


def test_checkin_errors(ctx):
    loan = approved(ctx, line(ctx["sax"]))
    with pytest.raises(svc.LoanError):
        svc.checkin(loan, ctx[Role.ADMIN], {loan.lines[0].id: None})  # pas encore remis
    svc.checkout(loan, ctx[Role.ADMIN])
    with pytest.raises(svc.LoanError, match="au moins un"):
        svc.checkin(loan, ctx[Role.ADMIN], {})
    with pytest.raises(svc.LoanError, match="inconnue"):
        svc.checkin(loan, ctx[Role.ADMIN], {9999: None})


def test_checkin_does_not_override_repair_status(ctx):
    loan = approved(ctx, line(ctx["sax"]))
    svc.checkout(loan, ctx[Role.ADMIN])
    ctx["sax"].status = ItemStatus.EN_ENTRETIEN  # passé en réparation pendant le prêt
    svc.checkin(loan, ctx[Role.ADMIN], {loan.lines[0].id: None})
    assert ctx["sax"].status == ItemStatus.EN_ENTRETIEN


def test_change_end(ctx):
    loan = approved(ctx, line(ctx["sax"]))
    svc.change_end(loan, ctx[Role.ADMIN], d(15))
    assert loan.end == d(15)
    approved(ctx, line(ctx["sax"]), start=d(20), end=d(22))
    with pytest.raises(svc.LoanError, match="déjà pris"):
        svc.change_end(loan, ctx[Role.ADMIN], d(21))
    with pytest.raises(svc.LoanError, match="date de fin"):
        svc.change_end(loan, ctx[Role.ADMIN], None)
    with pytest.raises(svc.LoanError, match="après"):
        svc.change_end(loan, ctx[Role.ADMIN], d(9))


def test_change_end_of_pending_request_ignores_conflicts(ctx):
    approved(ctx, line(ctx["sax"]), start=d(20), end=d(22))
    loan, _ = request(ctx, line(ctx["sax"]))
    svc.change_end(loan, ctx[Role.ADMIN], d(21))
    assert loan.end == d(21)


def test_change_end_garde_can_be_open(ctx):
    loan = approved(ctx, line(ctx["sax"]), loan_type=G, end=d(20))
    svc.change_end(loan, ctx[Role.ADMIN], None)
    assert loan.end is None


def test_change_end_on_closed_loan(ctx):
    loan, _ = request(ctx, line(ctx["sax"]))
    svc.refuse(loan, ctx[Role.ADMIN])
    with pytest.raises(svc.LoanError):
        svc.change_end(loan, ctx[Role.ADMIN], d(20))


# ---------------------------------------------------------------------------
# Construction des lignes depuis le formulaire
# ---------------------------------------------------------------------------

def test_build_lines(ctx):
    lines = svc.build_lines([ctx["sax"].id, ctx["sax"].id, 999, ctx["cable"].id],
                            {ctx["cable"].id: 3, ctx["sax"].id: 5}, [ctx["kit"].id, 998])
    assert [(l.item, l.kit, l.quantity) for l in lines] == [
        (ctx["sax"], None, 1), (ctx["cable"], None, 3), (None, ctx["kit"], 1)]


def test_build_lines_invalid_quantity(ctx):
    with pytest.raises(svc.LoanError, match="Quantité invalide"):
        svc.build_lines([ctx["cable"].id], {ctx["cable"].id: 0}, [])


def test_build_lines_empty_kit(ctx):
    empty = Kit(name="Vide")
    db.session.add(empty)
    db.session.commit()
    with pytest.raises(svc.LoanError, match="est vide"):
        svc.build_lines([], {}, [empty.id])
