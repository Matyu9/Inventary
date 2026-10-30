import re

import pytest

from app.extensions import db
from app.models import Activity, Loan, LoanStatus, LoanType, Role, User

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


def person(**kw):
    data = {"first_name": "Léa", "last_name": "Durand", "email": "", "phone": "", "section": "Clarinettes",
            "member_status": "élève", "role": E}
    data.update(kw)
    return data


def created_password(r):
    return re.search(r'<code class="password">([^<]+)</code>', r.text).group(1)


def test_prof_creates_inactive_borrower_record(client, as_role):
    as_role(P)
    r = client.post("/utilisateurs/nouveau", data=person(role=A, active="y", email="lea@ohn.test"))
    user = db.session.scalar(db.select(User).filter_by(last_name="Durand"))
    assert r.status_code == 302
    assert user.role == E and not user.active and user.password_hash is None
    assert user.section == "Clarinettes" and user.member_status == "élève" and user.email == "lea@ohn.test"


def test_prof_form_hides_role_and_access(client, as_role):
    as_role(P)
    r = client.get("/utilisateurs/nouveau")
    assert 'name="role"' not in r.text and 'name="active"' not in r.text and "fiche emprunteur" in r.text


def test_prof_cannot_edit_users(client, as_role, users):
    as_role(P)
    assert client.get(f"/utilisateurs/{users[E].id}/modifier").status_code == 403
    assert client.post(f"/utilisateurs/{users[E].id}/mot-de-passe").status_code == 403


def test_borrower_cannot_manage_people(client, as_role, users):
    as_role(E)
    assert client.post("/utilisateurs/nouveau", data=person()).status_code == 403


def test_create_from_loan_form_redirects_back(client, as_role):
    as_role(P)
    r = client.post("/utilisateurs/nouveau?retour=pret", data=person())
    user = db.session.scalar(db.select(User).filter_by(last_name="Durand"))
    assert f"/prets/nouveau?borrower_id={user.id}" in r.headers["Location"]


def test_admin_creates_account_with_temporary_password(client, as_role):
    as_role(A)
    r = client.post("/utilisateurs/nouveau", data=person(role=P, active="y", email="Prof2@OHN.test"))
    assert r.status_code == 200 and "ne sera <strong>plus affiché</strong>" in r.text
    password = created_password(r)
    user = db.session.scalar(db.select(User).filter_by(email="prof2@ohn.test"))
    assert user.role == P and user.active and user.must_change_password and user.check_password(password)
    assert db.session.scalar(db.select(Activity).filter_by(action="user.create"))


def test_temporary_password_forces_change(client, as_role):
    as_role(A)
    password = created_password(client.post("/utilisateurs/nouveau",
                                            data=person(role=E, active="y", email="lea@ohn.test")))
    client.post("/deconnexion")
    r = client.post("/connexion", data={"email": "lea@ohn.test", "password": password}, follow_redirects=True)
    assert "Choisissez un nouveau mot de passe" in r.text
    assert client.get("/mes-prets").headers["Location"].endswith("/mot-de-passe")
    r = client.post("/mot-de-passe", data={"current": password, "new": password, "confirm": password})
    assert "doit être différent" in r.text
    client.post("/mot-de-passe", data={"current": password, "new": "nouveau-mdp", "confirm": "nouveau-mdp"})
    assert client.get("/mes-prets").status_code == 200
    assert not db.session.scalar(db.select(User).filter_by(email="lea@ohn.test")).must_change_password


def test_logout_allowed_during_forced_change(client, users):
    users[E].must_change_password = True
    db.session.commit()
    login(client, users[E])
    assert client.post("/deconnexion").status_code == 302


def test_active_requires_email(client, as_role):
    as_role(A)
    r = client.post("/utilisateurs/nouveau", data=person(active="y"))
    assert "Un email est nécessaire" in r.text and db.session.scalar(db.select(db.func.count(User.id))) == 3


@pytest.mark.parametrize("email", ["prof@ohn.test", "PROF@ohn.test"])
def test_duplicate_email(client, as_role, users, email):
    as_role(A)
    r = client.post("/utilisateurs/nouveau", data=person(email=email))
    assert "déjà utilisé" in r.text


def test_invalid_email(client, as_role):
    as_role(A)
    assert "Adresse email invalide" in client.post("/utilisateurs/nouveau", data=person(email="pas-un-email")).text


def test_required_names(client, as_role):
    as_role(A)
    r = client.post("/utilisateurs/nouveau", data=person(first_name="", last_name=""))
    assert r.text.count('aria-invalid="true"') == 2


def test_edit_user(client, as_role, users):
    as_role(A)
    target = users[E]
    r = client.post(f"/utilisateurs/{target.id}/modifier", data=person(email=target.email, role=P, active="y"))
    assert r.status_code == 302
    db.session.refresh(target)
    assert target.role == P and target.first_name == "Léa" and target.email == "emprunteur@ohn.test"


def test_edit_keeps_own_email(client, as_role, users):
    as_role(A)
    r = client.post(f"/utilisateurs/{users[P].id}/modifier", data=person(email="PROF@ohn.test", role=P, active="y"))
    assert r.status_code == 302


def test_activating_record_without_password_generates_one(client, as_role):
    record = User(first_name="Tom", last_name="Petit", role=E, active=False)
    db.session.add(record)
    db.session.commit()
    as_role(A)
    r = client.post(f"/utilisateurs/{record.id}/modifier", data=person(email="tom@ohn.test", active="y"))
    assert created_password(r)
    db.session.refresh(record)
    assert record.active and record.must_change_password


def test_deactivate_user_blocks_login(client, as_role, users):
    as_role(A)
    client.post(f"/utilisateurs/{users[P].id}/modifier", data=person(email=users[P].email, role=P))
    client.post("/deconnexion")
    r = login(client, users[P])
    assert "incorrect" in r.text


@pytest.mark.parametrize("changes", [{"role": P, "active": "y"}, {"role": A}])
def test_admin_cannot_lock_self_out(client, as_role, users, changes):
    admin = as_role(A)
    r = client.post(f"/utilisateurs/{admin.id}/modifier", data=person(email=admin.email, **changes),
                    follow_redirects=True)
    assert "vos propres droits" in r.text
    db.session.refresh(admin)
    assert admin.is_admin and admin.active


def test_reset_password(client, as_role, users):
    as_role(A)
    r = client.post(f"/utilisateurs/{users[P].id}/mot-de-passe")
    password = created_password(r)
    db.session.refresh(users[P])
    assert users[P].check_password(password) and not users[P].check_password("motdepasse")
    assert users[P].must_change_password


def test_reset_password_requires_email(client, as_role):
    record = User(first_name="Sans", last_name="Mail", role=E)
    db.session.add(record)
    db.session.commit()
    as_role(A)
    r = client.post(f"/utilisateurs/{record.id}/mot-de-passe", follow_redirects=True)
    assert "Ajoutez d&#39;abord un email" in r.text or "Ajoutez d'abord un email" in r.text


def test_temporary_passwords_are_random(app):
    from app.blueprints.users import temporary_password
    passwords = {temporary_password() for _ in range(50)}
    assert len(passwords) == 50 and all(re.fullmatch(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}", p) for p in passwords)


def test_people_list(client, as_role, users):
    from datetime import datetime
    db.session.add(Loan(type=LoanType.GARDE, status=LoanStatus.EN_COURS, borrower=users[E],
                        requested_by=users[P], start=datetime.now()))
    db.session.commit()
    as_role(P)
    r = client.get("/utilisateurs")
    assert "EMPRUNTEUR Emprunteur" in r.text or "TEST Emprunteur" in r.text
    assert f"/prets/nouveau?borrower_id={users[E].id}" in r.text and "/modifier" not in r.text
    assert "TEST Prof" in client.get("/utilisateurs?role=prof").text
    assert "TEST Prof" not in client.get("/utilisateurs?role=emprunteur").text
    assert "TEST Prof" in client.get("/utilisateurs?q=PROF").text
    assert "TEST Prof" not in client.get("/utilisateurs", query_string={"q": "_"}).text  # joker SQL pris littéralement


def test_user_404(client, as_role):
    as_role(A)
    assert client.get("/utilisateurs/999/modifier").status_code == 404
