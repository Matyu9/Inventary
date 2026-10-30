from types import SimpleNamespace

import pytest

from app.models import Role
from app.permissions import ROLE_PERMISSIONS, can

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


def u(role, id=1):
    return SimpleNamespace(role=role, id=id, is_authenticated=True)


@pytest.mark.parametrize("action,allowed", [
    ("inventory.view", {A, P}),
    ("item.edit", {A, P}),
    ("item.delete", {A}),
    ("note.add", {A, P}),
    ("maintenance.edit", {A, P}),
    ("loan.request", {A, P}),
    ("loan.validate", {A}),
    ("audit.scan", {A, P}),
    ("audit.manage", {A}),
    ("user.manage", {A}),
    ("import", {A}),
    ("export", {A, P}),
    ("myloans.view", {A, P, E}),
])
def test_role_matrix(action, allowed):
    for role in Role.ALL:
        assert can(u(role), action) is (role in allowed), (action, role)


def test_prof_cannot_validate_loans():
    assert not can(u(P), "loan.validate")


def test_checkout_requires_admin_or_guarantor():
    loan = SimpleNamespace(guarantor_id=7, borrower_id=9, requested_by_id=7)
    assert can(u(A), "loan.checkout", loan)
    assert can(u(P, id=7), "loan.checkout", loan)
    assert not can(u(P, id=8), "loan.checkout", loan)
    assert not can(u(E, id=9), "loan.checkout", loan)


def test_borrower_sees_only_own_loans():
    loan = SimpleNamespace(guarantor_id=7, borrower_id=9, requested_by_id=7)
    assert can(u(E, id=9), "loan.view", loan)
    assert not can(u(E, id=10), "loan.view", loan)


def test_anonymous_has_no_rights():
    anon = SimpleNamespace(is_authenticated=False)
    assert not any(can(anon, action) for action in ROLE_PERMISSIONS)


def test_unknown_permission_raises():
    with pytest.raises(KeyError):
        can(u(A), "nope")
