"""Tests de propriétés (Hypothesis) : invariants métier et robustesse face aux entrées arbitraires."""
from datetime import datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.extensions import db, fold
from app.models import (Category, ConsumptionMode, Item, Loan, LoanLine, LoanStatus, LoanType, Role, Tracking)
from app.services import loans as svc
from app.services import transfer

from .conftest import login

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR
BASE = datetime(2030, 1, 1)
FIXTURES = settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)

slot = st.integers(min_value=0, max_value=40)


def interval():
    return st.tuples(slot, st.integers(min_value=1, max_value=10), st.booleans())


# ---------------------------------------------------------------------------
# Chevauchement : comparaison avec un oracle naïf sur des créneaux discrets
# ---------------------------------------------------------------------------

@given(a=interval(), b=interval())
def test_overlap_matches_brute_force(a, b):
    def span(start, length, open_ended):
        return start, None if open_ended else start + length

    a_start, a_end = span(*a)
    b_start, b_end = span(*b)
    horizon = 100
    cells_a = set(range(a_start, a_end if a_end is not None else horizon))
    cells_b = set(range(b_start, b_end if b_end is not None else horizon))
    assert svc._overlaps(a_start, a_end, b_start, b_end) == bool(cells_a & cells_b)


# ---------------------------------------------------------------------------
# Invariant : jamais deux prêts bloquants qui se chevauchent sur un même objet
# ---------------------------------------------------------------------------

def _reset_loans():
    db.session.execute(db.delete(LoanLine))
    db.session.execute(db.delete(Loan))
    db.session.commit()


@FIXTURES
@given(requests=st.lists(st.tuples(interval(), st.sampled_from(["validate", "checkout", "cancel", "return"])),
                         min_size=1, max_size=12))
def test_no_double_booking(app, users, requests):
    with app.test_request_context():
        item = db.session.scalar(db.select(Item).filter_by(code="P-1"))
        if item is None:
            item = Item(code="P-1", name="Sax", category=Category(name="Bois"))
            db.session.add(item)
            db.session.commit()
        _reset_loans()
        item.status = "disponible"
        for (start, length, open_ended), action in requests:
            s = BASE + timedelta(days=start)
            e = None if open_ended else s + timedelta(days=length)
            try:
                loan, _ = svc.create_request(loan_type=LoanType.GARDE if open_ended else LoanType.RESERVATION,
                                             borrower=users[E], guarantor=users[P], requested_by=users[P],
                                             start=s, end=e, lines=[LoanLine(item=item, quantity=1)])
                svc.validate(loan, users[A])
                if action in ("checkout", "return"):
                    svc.checkout(loan, users[A])
                if action == "return":
                    svc.checkin(loan, users[A], {loan.lines[0].id: None})
                if action == "cancel":
                    svc.cancel(loan, users[A])
                db.session.commit()
            except svc.LoanError:
                db.session.rollback()
        blocking = db.session.scalars(db.select(Loan).where(Loan.status.in_(LoanStatus.BLOCKING))).all()
        for i, a in enumerate(blocking):
            for b in blocking[i + 1:]:
                assert not svc._overlaps(a.start, a.end, b.start, b.end), (a.id, b.id)


@FIXTURES
@given(requests=st.lists(st.tuples(interval(), st.integers(min_value=1, max_value=6)), min_size=1, max_size=12))
def test_returnable_quantity_never_exceeded(app, users, requests):
    capacity = 8
    with app.test_request_context():
        item = db.session.scalar(db.select(Item).filter_by(code="P-2"))
        if item is None:
            item = Item(code="P-2", name="Câbles", category=Category(name="Sono"), tracking=Tracking.QUANTITE,
                        quantity=capacity, consumption_mode=ConsumptionMode.RETOURNABLE)
            db.session.add(item)
            db.session.commit()
        _reset_loans()
        for (start, length, _), qty in requests:
            s = BASE + timedelta(days=start)
            try:
                loan, _ = svc.create_request(loan_type=LoanType.RESERVATION, borrower=users[E], guarantor=users[P],
                                             requested_by=users[P], start=s, end=s + timedelta(days=length),
                                             lines=[LoanLine(item=item, quantity=qty)])
                svc.validate(loan, users[A])
                db.session.commit()
            except svc.LoanError:
                db.session.rollback()
        loans = db.session.scalars(db.select(Loan).where(Loan.status == LoanStatus.VALIDE)).all()
        for day in range(0, 52):
            t = BASE + timedelta(days=day, hours=12)
            used = sum(l.lines[0].quantity for l in loans if l.start <= t < l.end)
            assert used <= capacity, day


@FIXTURES
@given(stock=st.integers(min_value=0, max_value=20), moves=st.lists(
    st.tuples(st.sampled_from(["achat", "retour", "distribution", "perte", "inventaire"]),
              st.integers(min_value=-3, max_value=30)), max_size=15))
def test_stock_never_negative(app, users, stock, moves):
    from app.services import stock as stock_svc
    with app.test_request_context():
        item = db.session.scalar(db.select(Item).filter_by(code="P-3"))
        if item is None:
            item = Item(code="P-3", name="Anches", category=Category(name="Conso"), tracking=Tracking.QUANTITE)
            db.session.add(item)
        item.quantity = stock
        db.session.commit()
        expected = stock
        for reason, amount in moves:
            try:
                delta = stock_svc.move(item, reason, amount, users[P])
                db.session.commit()
                expected += delta
            except stock_svc.StockError:
                db.session.rollback()
            assert item.quantity == expected >= 0


# ---------------------------------------------------------------------------
# Robustesse : entrées arbitraires
# ---------------------------------------------------------------------------

@given(st.text())
def test_fold_is_idempotent_and_lowercase(text):
    once = fold(text)
    assert fold(once) == once
    assert once == once.lower() or not once.isascii()


@given(st.text(max_size=40))
def test_import_parsers_only_raise_value_errors(text):
    for parser in (transfer._parse_date, transfer._parse_amount, transfer._parse_int, transfer._condition):
        try:
            parser(text)
        except ValueError:
            pass


@given(st.binary(max_size=400), st.sampled_from(["x.csv", "x.xlsx", "x.txt"]))
def test_read_table_only_raises_transfer_errors(data, name):
    try:
        header, rows = transfer.read_table(data, name)
    except transfer.TransferError:
        return
    assert all(len(r) == len(header) for r in rows)


@FIXTURES
@given(q=st.text(max_size=60))
def test_search_never_crashes(client, users, q):
    login(client, users[P])
    for url in ("/objets", "/prets", "/utilisateurs", "/prets/recherche-materiel", "/scan/resoudre"):
        r = client.get(url, query_string={"q": q, "code": q})
        assert r.status_code < 500, (url, q)


@FIXTURES
@given(data=st.dictionaries(
    st.sampled_from(["name", "category_id", "brand", "condition", "status", "location_id", "tags", "purchase_date",
                     "purchase_price", "estimated_value", "tracking", "quantity", "low_stock_threshold",
                     "consumption_mode", "description"]),
    st.text(max_size=30)))
def test_item_form_fuzz(client, users, data):
    if not db.session.scalar(db.select(Category.id).limit(1)):
        db.session.add(Category(name="Bois"))
        db.session.commit()
    login(client, users[P])
    r = client.post("/objets/nouveau", data=data)
    assert r.status_code in (200, 302)


@FIXTURES
@given(data=st.dictionaries(
    st.sampled_from(["type", "borrower_id", "guarantor_id", "title", "start", "end", "deposit_amount", "comment",
                     "item_ids", "kit_ids", "auto_validate"]),
    st.one_of(st.text(max_size=20), st.integers(min_value=-5, max_value=5).map(str))))
def test_loan_form_fuzz(client, users, data):
    login(client, users[A])
    r = client.post("/prets/nouveau", data=data)
    assert r.status_code in (200, 302)
    r = client.post("/prets/verifier", data=data)
    assert r.status_code == 200
