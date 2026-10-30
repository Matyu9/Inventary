from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Category, Item, Loan, LoanLine, LoanStatus, LoanType, Location, Role, User

from .conftest import make_user


@pytest.fixture
def category(app):
    c = Category(name="Bois")
    db.session.add(c)
    db.session.commit()
    return c


def add_item(category, code, **kw):
    item = Item(code=code, name=kw.pop("name", "Objet"), category=category, **kw)
    db.session.add(item)
    db.session.commit()
    return item


def test_next_code_starts_at_one(app):
    assert Item.next_code("OHN") == "OHN-0001"


def test_next_code_increments_numerically(category):
    add_item(category, "OHN-0009")
    add_item(category, "OHN-9999")
    add_item(category, "OHN-10000")
    assert Item.next_code("OHN") == "OHN-10001"


def test_next_code_ignores_other_prefixes_and_garbage(category):
    add_item(category, "ABC-0050")
    add_item(category, "OHN-0002")
    add_item(category, "OHN-ancien")
    assert Item.next_code("OHN") == "OHN-0003"


def test_item_code_unique(category):
    add_item(category, "OHN-0001")
    with pytest.raises(IntegrityError):
        add_item(category, "OHN-0001")


def test_item_status_constraint(category):
    with pytest.raises(IntegrityError):
        add_item(category, "OHN-0001", status="perdu")


def test_item_quantity_cannot_be_negative(category):
    with pytest.raises(IntegrityError):
        add_item(category, "OHN-0001", quantity=-1)


def test_qr_tokens_are_unique_and_generated(category):
    a, b = add_item(category, "OHN-0001"), add_item(category, "OHN-0002")
    assert a.qr_token and b.qr_token and a.qr_token != b.qr_token


def test_low_stock(category):
    item = add_item(category, "OHN-0001", tracking="quantite", quantity=3, low_stock_threshold=5)
    assert item.is_low_stock
    item.quantity = 6
    assert not item.is_low_stock
    individual = add_item(category, "OHN-0002", low_stock_threshold=5)
    assert not individual.is_low_stock


def test_location_path():
    root = Location(name="Local")
    shelf = Location(name="Étagère", parent=Location(name="Armoire", parent=root))
    assert shelf.path == "Local > Armoire > Étagère"
    assert root.path == "Local"


def test_location_path_survives_cycle(app):
    a, b = Location(name="A"), Location(name="B")
    db.session.add_all([a, b])
    db.session.flush()
    a.parent, b.parent = b, a
    assert a.path in ("B > A", "A > B")


def test_user_role_constraint(app):
    db.session.add(User(first_name="X", last_name="Y", role="superadmin"))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_user_password_and_flags(app):
    user = make_user(Role.PROF)
    assert user.check_password("motdepasse") and not user.check_password("autre")
    assert user.is_prof and not user.is_admin
    assert user.full_name == "Prof Test" and user.role_label == "Professeur"
    assert user.ical_token


def test_user_without_password_cannot_authenticate(app):
    user = User(first_name="Fiche", last_name="Seule")
    assert not user.check_password("")


def test_email_unique(app):
    make_user(Role.PROF, email="a@b.c")
    with pytest.raises(IntegrityError):
        make_user(Role.ADMIN, email="a@b.c")


@pytest.fixture
def people(app):
    return make_user(Role.EMPRUNTEUR), make_user(Role.PROF)


def _loan(borrower, prof, **kw):
    now = datetime.now()
    loan = Loan(borrower=borrower, guarantor=prof, requested_by=prof,
                start=kw.pop("start", now), **kw)
    db.session.add(loan)
    return loan


def test_reservation_requires_end(people):
    _loan(*people, type=LoanType.RESERVATION)
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_garde_without_end_ok(people):
    loan = _loan(*people, type=LoanType.GARDE)
    db.session.commit()
    assert loan.status == LoanStatus.DEMANDE and loan.type_label == "Garde"


def test_loan_end_after_start(people):
    now = datetime.now()
    _loan(*people, type=LoanType.RESERVATION, start=now, end=now - timedelta(hours=1))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_overdue(people):
    now = datetime.now()
    loan = _loan(*people, type=LoanType.RESERVATION, start=now - timedelta(days=3),
                 end=now - timedelta(days=1), status=LoanStatus.EN_COURS)
    assert loan.is_overdue and loan.status_label == "En retard"
    loan.status = LoanStatus.VALIDE
    assert not loan.is_overdue and loan.status_label == "Validé"


def test_garde_without_end_never_overdue(people):
    loan = _loan(*people, type=LoanType.GARDE, status=LoanStatus.EN_COURS)
    assert not loan.is_overdue


def test_loan_line_item_xor_kit(people, category):
    loan = _loan(*people, type=LoanType.GARDE)
    db.session.flush()
    db.session.add(LoanLine(loan=loan))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_timestamps_use_local_time(app):
    """Régression : CURRENT_TIMESTAMP de SQLite est en UTC, le reste de l'appli en heure locale."""
    user = make_user(Role.PROF, email="tz@ohn.test")
    assert abs((user.created_at - datetime.now()).total_seconds()) < 5
    from app.models import Activity, Notification
    db.session.add_all([Activity(action="x", message="x"), Notification(user_id=user.id, message="x")])
    db.session.commit()
    for model in (Activity, Notification):
        created = db.session.scalar(db.select(model.created_at))
        assert abs((created - datetime.now()).total_seconds()) < 5, model


def test_next_code_prefix_is_literal(category):
    """Les jokers SQL du préfixe (« _ », « % ») ne doivent pas attraper d'autres codes."""
    add_item(category, "AXB-0005")
    assert Item.next_code("A_B") == "A_B-0001"
    add_item(category, "A_B-0003")
    assert Item.next_code("A_B") == "A_B-0004"


@pytest.mark.parametrize("raw, expected", [("", "INV"), ("ohn", "OHN"), (" abc- ", "ABC"), ("-", "INV")])
def test_code_prefix_normalized(monkeypatch, raw, expected):
    import importlib

    import app.config as config
    monkeypatch.setenv("ITEM_CODE_PREFIX", raw)
    try:
        assert importlib.reload(config).Config.ITEM_CODE_PREFIX == expected
    finally:
        monkeypatch.delenv("ITEM_CODE_PREFIX")
        importlib.reload(config)
