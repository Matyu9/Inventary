"""Gestion des comptes : invitations, mot de passe oublié, Mon compte, fiche détaillée, cycle de vie."""
import re
from datetime import date, datetime, timedelta

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import (AccountToken, Activity, Category, Item, Loan, LoanStatus, LoanType, LoginSession, Note, Notification,
                        Reminder, Role, User)
from app.services import accounts, mailer, people, sessions

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR
UA_FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:131.0) Gecko/20100101 Firefox/131.0"


@pytest.fixture
def outbox(app, monkeypatch):
    """SMTP configuré, envois capturés : [(destinataire, objet, texte, html)]."""
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="inventaire@ohn.test", BASE_URL="https://inv.ohn.test")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject, text, html)))
    return sent


@pytest.fixture
def iso(tmp_path):
    """Appli sans contexte ouvert entre les requêtes, comme en production : Flask-Login ne garde pas l'utilisateur
    en cache dans `g`. Indispensable pour vérifier qu'un appareil déconnecté l'est vraiment."""
    class Cfg(TestConfig):
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "uploads"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'inventaire.db'}"

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        for role in Role.BUILTIN:
            make_user(role)
    yield app
    with app.app_context():
        db.drop_all()


def connect(app, role=P):
    client = app.test_client()
    assert client.post("/connexion", data={"email": f"{role}@ohn.test", "password": "motdepasse"},
                       headers={"User-Agent": UA_FIREFOX_MAC}).status_code == 302
    return client


def in_app(app, fn):
    with app.app_context():
        return fn()


def user_id(app, role):
    return in_app(app, lambda: db.session.scalar(db.select(User.id).filter_by(email=f"{role}@ohn.test")))


def session_ids(app, role):
    uid = user_id(app, role)
    return in_app(app, lambda: list(db.session.scalars(
        db.select(LoginSession.id).filter_by(user_id=uid).order_by(LoginSession.created_at, LoginSession.id))))


def link_in(mail):
    return re.search(r"https://inv\.ohn\.test(/lien/[\w-]+)", mail[2]).group(1)


def person(**kw):
    data = {"first_name": "Léa", "last_name": "Durand", "email": "", "phone": "", "section": "",
            "member_status": "", "role": E}
    data.update(kw)
    return data


def record(first="Tom", last="Petit", **kw):
    user = User(first_name=first, last_name=last, role=kw.pop("role", E), active=kw.pop("active", False), **kw)
    db.session.add(user)
    db.session.commit()
    return user


def open_loan(borrower, guarantor=None, status=LoanStatus.EN_COURS):
    loan = Loan(type=LoanType.GARDE, status=status, borrower_id=borrower.id, requested_by_id=borrower.id,
                guarantor_id=guarantor.id if guarantor else None, start=datetime.now())
    db.session.add(loan)
    db.session.commit()
    return loan


# --- Sessions : appareil, IP, dernière activité ----------------------------------------

def test_login_records_device_and_last_login(client, users):
    client.post("/connexion", data={"email": users[P].email, "password": "motdepasse"},
                headers={"User-Agent": UA_FIREFOX_MAC})
    db.session.refresh(users[P])
    assert users[P].last_login_at is not None
    login_session = db.session.scalar(db.select(LoginSession).filter_by(user_id=users[P].id))
    assert login_session.user_agent == UA_FIREFOX_MAC and login_session.ip == "127.0.0.1"
    assert login_session.last_seen_at is not None


@pytest.mark.parametrize("ua, label", [
    (UA_FIREFOX_MAC, "Firefox · macOS"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 "
     "Safari/604.1", "Safari · iPhone"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0",
     "Edge · Windows"),
    ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36", "Chrome · Android"),
    ("", "Appareil inconnu"),
    (None, "Appareil inconnu"),
])
def test_device_label(ua, label):
    assert sessions.device_label(ua) == label


def test_last_seen_updated_at_most_every_five_minutes(iso):
    client = connect(iso, E)
    sid, = session_ids(iso, E)

    def set_seen(value):
        def run():
            db.session.get(LoginSession, sid).last_seen_at = value
            db.session.commit()
        in_app(iso, run)

    def seen():
        return in_app(iso, lambda: db.session.get(LoginSession, sid).last_seen_at)

    recent = datetime.now() - timedelta(minutes=2)
    set_seen(recent)
    client.get("/mes-prets")
    assert seen() == recent  # pas d'écriture à chaque requête
    old = datetime.now() - timedelta(minutes=10)
    set_seen(old)
    client.get("/mes-prets")
    assert seen() > recent


# --- Invitations ------------------------------------------------------------------------

def test_invitation_email_then_choose_password(client, as_role, outbox):
    as_role(A)
    r = client.post("/utilisateurs/nouveau", data=person(email="lea@ohn.test", active="y"))
    assert "vient d'être envoyée" in r.text
    (to, subject, text, html), = outbox
    assert to == "lea@ohn.test" and "accès" in subject and "Choisir mon mot de passe" in html
    lea = db.session.scalar(db.select(User).filter_by(email="lea@ohn.test"))
    assert lea.active and lea.password_hash is None

    client.post("/deconnexion")
    path = link_in(outbox[0])
    r = client.get(path)
    assert "Bienvenue, Léa" in r.text
    r = client.post(path, data={"new": "court", "confirm": "court"})
    assert "8" in r.text and lea.password_hash is None
    r = client.post(path, data={"new": "mon-mot-de-passe", "confirm": "mon-mot-de-passe"})
    assert r.headers["Location"] == "/"
    assert client.get("/mes-prets").status_code == 200  # connectée
    db.session.refresh(lea)
    assert lea.check_password("mon-mot-de-passe") and not lea.must_change_password
    assert db.session.scalar(db.select(db.func.count(AccountToken.id))) == 0
    client.post("/deconnexion")
    r = client.get(path, follow_redirects=True)
    assert "plus valable" in r.text  # le lien ne sert qu'une fois


def test_token_stored_as_hash_only(app):
    user = record(email="tom@ohn.test", active=True)
    raw = accounts.issue(user, AccountToken.INVITE)
    db.session.commit()
    token = db.session.scalar(db.select(AccountToken))
    assert raw not in token.id and len(token.id) == 64
    assert accounts.find(raw) is token
    assert accounts.find("x" * 200) is None and accounts.find("") is None


def test_new_link_cancels_previous(app):
    user = record(email="tom@ohn.test", active=True)
    first = accounts.issue(user, AccountToken.INVITE)
    second = accounts.issue(user, AccountToken.INVITE)
    db.session.commit()
    assert accounts.find(first) is None and accounts.find(second) is not None


def test_expired_link_refused(client, app):
    user = record(email="tom@ohn.test", active=True)
    raw = accounts.issue(user, AccountToken.INVITE)
    db.session.scalar(db.select(AccountToken)).expires_at = datetime.now() - timedelta(seconds=1)
    db.session.commit()
    assert "plus valable" in client.get(f"/lien/{raw}", follow_redirects=True).text


def test_deactivation_kills_pending_links(client, as_role):
    tom = record(email="tom@ohn.test", active=True)
    raw = accounts.issue(tom, AccountToken.INVITE)
    db.session.commit()
    as_role(A)
    client.post(f"/utilisateurs/{tom.id}/modifier", data=person(first_name="Tom", last_name="Petit",
                                                               email="tom@ohn.test"))
    assert accounts.find(raw) is None
    assert db.session.scalar(db.select(db.func.count(AccountToken.id))) == 0


def test_link_shown_when_smtp_disabled(client, as_role):
    as_role(A)
    r = client.post("/utilisateurs/nouveau", data=person(email="lea@ohn.test", active="y"))
    path = re.search(r'value="http://[^"]+(/lien/[\w-]+)"', r.text).group(1)
    client.post("/deconnexion")
    assert "Bienvenue" in client.get(path).text


def test_resend_invitation_and_reset_link(client, as_role, users, outbox):
    as_role(A)
    tom = record(email="tom@ohn.test", active=True)
    client.post(f"/utilisateurs/{tom.id}/inviter")
    client.post(f"/utilisateurs/{users[P].id}/inviter")
    assert [m[0] for m in outbox] == ["tom@ohn.test", users[P].email]
    assert "accès" in outbox[0][1] and "Réinitialisation" in outbox[1][1]


@pytest.mark.parametrize("fields, message", [({"email": None}, "abord un email"),
                                             ({"active": False}, "Peut se connecter")])
def test_invite_requires_email_and_access(client, as_role, fields, message):
    as_role(A)
    tom = record(email="tom@ohn.test", active=True)
    for key, value in fields.items():
        setattr(tom, key, value)
    db.session.commit()
    assert message in client.post(f"/utilisateurs/{tom.id}/inviter", follow_redirects=True).text


def test_manager_cannot_take_over_admin_account(client, users):
    """Un gestionnaire de comptes non admin ne génère ni mot de passe ni lien pour un admin."""
    db.session.add(Role(key="secretariat", name="Secrétariat", permissions=["user.manage", "borrower.create"]))
    manager = make_user(E, email="secr@ohn.test")
    manager.role = "secretariat"
    db.session.commit()
    login(client, manager)
    assert client.post(f"/utilisateurs/{users[A].id}/mot-de-passe").status_code == 403
    assert client.post(f"/utilisateurs/{users[A].id}/inviter").status_code == 403
    assert client.post(f"/utilisateurs/{users[A].id}/archiver").status_code == 403
    assert client.post(f"/utilisateurs/{users[A].id}/sessions/fermer").status_code == 403
    assert users[A].check_password("motdepasse")


# --- Mot de passe oublié -----------------------------------------------------------------

def test_login_page_links_to_forgot(client):
    assert 'href="/mot-de-passe-oublie"' in client.get("/connexion").text


def test_forgot_password_flow(client, users, outbox):
    login(client, users[P])
    client.post("/deconnexion")
    r = client.post("/mot-de-passe-oublie", data={"email": " PROF@ohn.test "}, follow_redirects=True)
    assert "Si un compte actif correspond" in r.text
    (to, subject, _text, _html), = outbox
    assert to == "prof@ohn.test" and "Réinitialisation" in subject
    r = client.post(link_in(outbox[0]), data={"new": "tout-nouveau", "confirm": "tout-nouveau"})
    assert r.headers["Location"] == "/"
    db.session.refresh(users[P])
    assert users[P].check_password("tout-nouveau")
    assert db.session.scalar(db.select(Activity).filter_by(action="user.reset_request"))


def test_reset_closes_other_sessions(iso):
    phone, laptop = connect(iso), iso.test_client()

    def issue():
        raw = accounts.issue(db.session.get(User, user_id(iso, P)), AccountToken.RESET)
        db.session.commit()
        return raw
    raw = in_app(iso, issue)
    laptop.post(f"/lien/{raw}", data={"new": "tout-nouveau", "confirm": "tout-nouveau"})
    assert phone.get("/mes-prets").status_code == 302  # l'autre appareil est déconnecté
    assert laptop.get("/mes-prets").status_code == 200


@pytest.mark.parametrize("email", ["inconnu@ohn.test", "inactif@ohn.test", "archive@ohn.test"])
def test_forgot_password_sends_nothing_without_active_account(client, outbox, email):
    record(email="inactif@ohn.test", active=False)
    record(first="Ar", email="archive@ohn.test", active=True, archived_at=datetime.now())
    r = client.post("/mot-de-passe-oublie", data={"email": email}, follow_redirects=True)
    assert "Si un compte actif correspond" in r.text  # même réponse : rien n'est révélé
    assert outbox == []


def test_forgot_password_throttled(client, users, outbox):
    for _ in range(8):
        client.post("/mot-de-passe-oublie", data={"email": "prof@ohn.test"})
    r = client.post("/mot-de-passe-oublie", data={"email": "prof@ohn.test"})
    assert r.status_code == 429 and len(outbox) == 8


def test_forgot_password_without_smtp(client):
    r = client.get("/mot-de-passe-oublie")
    assert "pas configuré" in r.text and 'name="email"' not in r.text


def test_forgot_password_when_logged_in_goes_to_password_page(client, users):
    login(client, users[P])
    assert client.get("/mot-de-passe-oublie").headers["Location"].endswith("/mot-de-passe")


def test_reset_link_dead_for_deactivated_account(client, users):
    raw = accounts.issue(users[P], AccountToken.RESET)
    users[P].active = False
    db.session.commit()
    assert "plus valable" in client.get(f"/lien/{raw}", follow_redirects=True).text


# --- Mon compte ------------------------------------------------------------------------------

def test_account_page_for_everyone(client, users):
    login(client, users[E])
    r = client.get("/mon-compte")
    assert r.status_code == 200 and "Mes appareils connectés" in r.text and "cet appareil" in r.text
    assert 'href="/mon-compte"' in client.get("/").text


def test_account_requires_login(client):
    assert "/connexion" in client.get("/mon-compte").headers["Location"]


def test_update_profile(client, users):
    login(client, users[E])
    client.post("/mon-compte", data={"first_name": " Emma ", "last_name": "Leroy", "phone": "06 11 22 33 44"})
    db.session.refresh(users[E])
    assert (users[E].first_name, users[E].last_name, users[E].phone) == ("Emma", "Leroy", "06 11 22 33 44")
    assert db.session.scalar(db.select(Activity).filter_by(action="account.profile"))


def test_profile_requires_names(client, users):
    login(client, users[E])
    r = client.post("/mon-compte", data={"first_name": "", "last_name": "Leroy"})
    assert "error" in r.text
    db.session.refresh(users[E])
    assert users[E].first_name == "Emprunteur"


def test_change_email_with_confirmation(app, client, users, outbox):
    login(client, users[E])
    r = client.post("/mon-compte/email", data={"email": "nouvelle@ohn.test", "password": "faux"})
    assert "Mot de passe incorrect" in r.text and outbox == []
    client.post("/mon-compte/email", data={"email": "Nouvelle@OHN.test", "password": "motdepasse"})
    (to, subject, text, _html), = outbox
    assert to == "nouvelle@ohn.test" and "Confirmez" in subject
    db.session.refresh(users[E])
    assert users[E].email == "emprunteur@ohn.test"  # pas encore confirmé
    assert "nouvelle@ohn.test" in client.get("/mon-compte").text  # en attente

    path = link_in(outbox[0])
    other = app.test_client()  # le lien s'ouvre souvent sur un autre appareil
    assert "Confirmer l'adresse" in other.get(path).text
    other.post(path)
    db.session.refresh(users[E])
    assert users[E].email == "nouvelle@ohn.test"
    assert outbox[1][0] == "emprunteur@ohn.test" and "modifiée" in outbox[1][1]  # l'ancienne adresse est prévenue
    assert "plus valable" in other.get(path, follow_redirects=True).text


def test_change_email_refuses_taken_or_same(client, users, outbox):
    login(client, users[E])
    r = client.post("/mon-compte/email", data={"email": "prof@ohn.test", "password": "motdepasse"})
    assert "déjà utilisée" in r.text
    r = client.post("/mon-compte/email", data={"email": "emprunteur@ohn.test", "password": "motdepasse"})
    assert "déjà votre adresse" in r.text
    assert outbox == []


def test_email_taken_between_request_and_confirmation(client, users, outbox):
    login(client, users[E])
    client.post("/mon-compte/email", data={"email": "libre@ohn.test", "password": "motdepasse"})
    make_user(P, email="libre@ohn.test")
    path = link_in(outbox[0])
    assert "déjà utilisée" in client.get(path).text
    client.post(path)
    db.session.refresh(users[E])
    assert users[E].email == "emprunteur@ohn.test"


def test_change_email_without_smtp(client, users):
    login(client, users[E])
    r = client.post("/mon-compte/email", data={"email": "x@ohn.test", "password": "motdepasse"},
                    follow_redirects=True)
    assert "pas configuré" in r.text


def test_revoke_my_other_sessions(iso):
    phone = connect(iso)
    laptop = connect(iso)
    phone_session, laptop_session = session_ids(iso, P)
    laptop.post(f"/mon-compte/sessions/{laptop_session}/fermer")  # la sienne : passer par « Déconnexion »
    assert laptop.get("/mes-prets").status_code == 200
    laptop.post(f"/mon-compte/sessions/{phone_session}/fermer")
    assert phone.get("/mes-prets").status_code == 302
    assert laptop.get("/mes-prets").status_code == 200
    phone = connect(iso)
    laptop.post("/mon-compte/sessions/fermer")
    assert phone.get("/mes-prets").status_code == 302 and laptop.get("/mes-prets").status_code == 200


def test_cannot_revoke_someone_elses_session(iso):
    victim, attacker = connect(iso, A), connect(iso, E)
    target, = session_ids(iso, A)
    attacker.post(f"/mon-compte/sessions/{target}/fermer")
    assert victim.get("/mes-prets").status_code == 200


# --- Fiche détaillée ---------------------------------------------------------------------------

def test_detail_for_admin_shows_sessions_and_journal(client, as_role, users):
    login(client.application.test_client(), users[P])
    as_role(A)
    r = client.get(f"/utilisateurs/{users[P].id}")
    assert r.status_code == 200 and "Appareils connectés" in r.text and "Journal" in r.text
    assert "Fusionner" in r.text


def test_detail_for_prof_hides_account_management(client, as_role, users):
    as_role(P)
    r = client.get(f"/utilisateurs/{users[E].id}")
    assert r.status_code == 200 and "Prêts" in r.text
    assert "Appareils connectés" not in r.text and "Journal" not in r.text and "Archiver" not in r.text


def test_detail_forbidden_to_borrower_and_404(client, as_role, users):
    as_role(E)
    assert client.get(f"/utilisateurs/{users[P].id}").status_code == 403
    as_role(A)
    assert client.get("/utilisateurs/999").status_code == 404


def test_admin_disconnects_one_device(iso):
    device, admin = connect(iso), connect(iso, A)
    target, = session_ids(iso, P)
    prof_id = user_id(iso, P)
    assert "Firefox · macOS" in admin.get(f"/utilisateurs/{prof_id}").text
    admin.post(f"/utilisateurs/{prof_id}/sessions/{target}/fermer")
    assert device.get("/mes-prets").status_code == 302
    assert in_app(iso, lambda: db.session.scalar(
        db.select(Activity).filter_by(action="user.session", target_user_id=prof_id)))


def test_admin_disconnects_all_devices(iso):
    device, admin = connect(iso), connect(iso, A)
    admin.post(f"/utilisateurs/{user_id(iso, P)}/sessions/fermer")
    assert device.get("/mes-prets").status_code == 302
    assert admin.get("/mes-prets").status_code == 200


def test_archived_user_device_disconnected(iso):
    device, admin = connect(iso, E), connect(iso, A)
    admin.post(f"/utilisateurs/{user_id(iso, E)}/archiver")
    assert device.get("/mes-prets").status_code == 302


def test_journal_shows_actions_about_the_person(client, as_role, users):
    as_role(A)
    client.post(f"/utilisateurs/{users[P].id}/modifier", data=person(first_name="Paul", last_name="Prof",
                                                                    email=users[P].email, role=P, active="y"))
    assert "Fiche modifiée : Paul Prof" in client.get(f"/utilisateurs/{users[P].id}").text


# --- Cycle de vie -------------------------------------------------------------------------------

def test_archive_and_unarchive(client, as_role, users):
    as_role(A)
    r = client.post(f"/utilisateurs/{users[E].id}/archiver", follow_redirects=True)
    assert "archivé" in r.text
    db.session.refresh(users[E])
    assert users[E].archived_at and not users[E].active
    assert users[E].email not in client.get("/utilisateurs").text
    assert users[E].email in client.get("/utilisateurs?statut=archive").text
    assert f'value="{users[E].id}"' not in client.get("/prets/nouveau").text
    client.post(f"/utilisateurs/{users[E].id}/desarchiver")
    db.session.refresh(users[E])
    assert users[E].archived_at is None and not users[E].active


def test_archived_cannot_log_in(client, users):
    people.archive(users[E])
    db.session.commit()
    r = login(client, users[E])
    assert r.status_code == 200 and "incorrect" in r.text


@pytest.mark.parametrize("role", ["borrower", "guarantor"])
def test_archive_refused_with_open_loans(client, as_role, users, role):
    as_role(A)
    tom = record()
    open_loan(tom if role == "borrower" else users[E], guarantor=tom if role == "guarantor" else None)
    r = client.post(f"/utilisateurs/{tom.id}/archiver", follow_redirects=True)
    assert "prêt(s) en cours" in r.text
    db.session.refresh(tom)
    assert tom.archived_at is None


def test_archive_allowed_after_loans_closed(client, as_role):
    as_role(A)
    tom = record()
    open_loan(tom, status=LoanStatus.RENDU)
    client.post(f"/utilisateurs/{tom.id}/archiver")
    db.session.refresh(tom)
    assert tom.archived_at


def test_cannot_archive_or_delete_self(client, as_role):
    admin = as_role(A)
    assert "propre compte" in client.post(f"/utilisateurs/{admin.id}/archiver", follow_redirects=True).text
    assert "propre compte" in client.post(f"/utilisateurs/{admin.id}/supprimer", follow_redirects=True).text
    db.session.refresh(admin)
    assert admin.active and not admin.archived_at


def test_delete_unused_record(client, as_role):
    as_role(A)
    tom = record()
    tom_id = tom.id
    r = client.post(f"/utilisateurs/{tom_id}/supprimer", follow_redirects=True)
    assert "supprimée" in r.text
    assert db.session.get(User, tom_id) is None
    assert db.session.scalar(db.select(Activity).filter_by(action="user.delete"))


def test_delete_refused_with_history(client, as_role):
    as_role(A)
    tom = record()
    open_loan(tom, status=LoanStatus.RENDU)
    r = client.post(f"/utilisateurs/{tom.id}/supprimer", follow_redirects=True)
    assert "archivez-la plutôt" in r.text and db.session.get(User, tom.id) is not None
    assert "Supprimer</button>" not in client.get(f"/utilisateurs/{tom.id}").text


def test_references_ignore_cascading_rows(app):
    tom = record(email="tom@ohn.test", active=True)
    accounts.issue(tom, AccountToken.INVITE)
    db.session.add(Notification(user_id=tom.id, message="Coucou"))
    db.session.commit()
    assert people.references(tom) == {}


def test_merge_moves_history_and_fills_gaps(client, as_role, users):
    as_role(A)
    dup = record("Léa", "DURAND", phone="0600000000", section="Clarinettes")
    keep = record("Lea", "Durand", email="lea@ohn.test", active=True)
    item = Item(code="OHN-0001", name="Sax", category=Category(name="Bois"))
    db.session.add(item)
    db.session.flush()
    loan = open_loan(dup, status=LoanStatus.RENDU)
    db.session.add_all([Note(item=item, author_id=dup.id, text="note"),
                        Notification(user_id=dup.id, message="hello")])
    reminder = Reminder(item=item, due_date=date.today(), message="r", recipients=[dup, keep])
    db.session.add(reminder)
    db.session.commit()
    dup_id, loan_id = dup.id, loan.id

    page = client.get(f"/utilisateurs/{dup_id}/fusionner")
    assert "Doublons probables" in page.text and f'<option value="{keep.id}">' in page.text
    r = client.post(f"/utilisateurs/{dup_id}/fusionner", data={"target_id": keep.id})
    assert r.headers["Location"].endswith(f"/utilisateurs/{keep.id}")
    db.session.expire_all()
    assert db.session.get(User, dup_id) is None
    assert db.session.get(Loan, loan_id).borrower_id == keep.id
    assert db.session.scalar(db.select(Note)).author_id == keep.id
    assert db.session.scalar(db.select(Notification)).user_id == keep.id
    assert [u.id for u in db.session.get(Reminder, reminder.id).recipients] == [keep.id]
    keep = db.session.get(User, keep.id)
    assert (keep.email, keep.phone, keep.section, keep.first_name) == ("lea@ohn.test", "0600000000",
                                                                       "Clarinettes", "Lea")
    assert db.session.scalar(db.select(Activity).filter_by(action="user.merge", target_user_id=keep.id))


def test_merge_takes_email_of_duplicate_when_missing(app):
    dup = record(email="tom@ohn.test", active=True)
    keep = record("Tom", "Petit")
    people.merge(dup, keep)
    db.session.commit()
    assert db.session.get(User, keep.id).email == "tom@ohn.test"


def test_merge_refusals(client, as_role, users):
    admin = as_role(A)
    a = record(oidc_sub="sub-a")
    b = record("Bob", "B", oidc_sub="sub-b")
    r = client.post(f"/utilisateurs/{a.id}/fusionner", data={"target_id": b.id}, follow_redirects=True)
    assert "identité OIDC" in r.text and db.session.get(User, a.id)
    r = client.post(f"/utilisateurs/{a.id}/fusionner", data={"target_id": a.id}, follow_redirects=True)
    assert "deux fiches différentes" in r.text
    r = client.post(f"/utilisateurs/{admin.id}/fusionner", data={"target_id": a.id}, follow_redirects=True)
    assert "votre compte doit être conservé" in r.text and db.session.get(User, admin.id)
    assert client.post(f"/utilisateurs/{a.id}/fusionner", data={"target_id": 999}).status_code == 404


def test_bulk_open_access_sends_invitations(client, as_role, outbox):
    as_role(A)
    tom = record(email="tom@ohn.test")
    nomail = record("Sans", "Email")
    r = client.post("/utilisateurs/actions", data={"action": "activer", "user_ids": [tom.id, nomail.id]},
                    follow_redirects=True)
    assert "1 personne" in r.text and "pas d&#39;email" in r.text
    assert [m[0] for m in outbox] == ["tom@ohn.test"]
    db.session.refresh(tom)
    db.session.refresh(nomail)
    assert tom.active and not nomail.active


def test_bulk_open_access_without_smtp_warns(client, as_role):
    as_role(A)
    tom = record(email="tom@ohn.test")
    r = client.post("/utilisateurs/actions", data={"action": "activer", "user_ids": [tom.id]},
                    follow_redirects=True)
    assert "Invitation non envoyée" in r.text and accounts.pending_invitation(tom)


def test_bulk_deactivate_and_role(client, as_role, users):
    admin = as_role(A)
    tom = record(email="tom@ohn.test", active=True)
    r = client.post("/utilisateurs/actions", data={"action": "desactiver", "user_ids": [tom.id, admin.id]},
                    follow_redirects=True)
    assert "votre propre compte" in r.text
    db.session.refresh(tom)
    db.session.refresh(admin)
    assert not tom.active and admin.active
    client.post("/utilisateurs/actions", data={"action": "role", "role": P, "user_ids": [tom.id, admin.id]})
    db.session.refresh(tom)
    db.session.refresh(admin)
    assert tom.role == P and admin.role == A
    r = client.post("/utilisateurs/actions", data={"action": "role", "role": "", "user_ids": [tom.id]},
                    follow_redirects=True)
    assert "Choisissez le nouveau rôle" in r.text


def test_bulk_archive_skips_open_loans(client, as_role):
    as_role(A)
    busy, free = record(), record("Free", "Lance")
    open_loan(busy)
    r = client.post("/utilisateurs/actions", data={"action": "archiver", "user_ids": [busy.id, free.id]},
                    follow_redirects=True)
    assert "prêts en cours" in r.text
    db.session.refresh(busy)
    db.session.refresh(free)
    assert busy.archived_at is None and free.archived_at


def test_bulk_manager_cannot_touch_admins_or_grant_admin(client, users):
    db.session.add(Role(key="secretariat", name="Secrétariat", permissions=["user.manage", "borrower.create"]))
    manager = make_user(E, email="secr@ohn.test")
    manager.role = "secretariat"
    db.session.commit()
    login(client, manager)
    r = client.post("/utilisateurs/actions", data={"action": "desactiver", "user_ids": [users[A].id]},
                    follow_redirects=True)
    assert "compte administrateur" in r.text
    r = client.post("/utilisateurs/actions", data={"action": "role", "role": A, "user_ids": [users[E].id]},
                    follow_redirects=True)
    assert "Choisissez le nouveau rôle" in r.text
    db.session.refresh(users[A])
    db.session.refresh(users[E])
    assert users[A].active and users[E].role == E


def test_bulk_requires_selection_and_rights(client, as_role):
    as_role(A)
    assert "Choisissez" in client.post("/utilisateurs/actions", data={"action": "activer"},
                                       follow_redirects=True).text
    as_role(P)
    assert client.post("/utilisateurs/actions", data={"action": "activer"}).status_code == 403


@pytest.mark.parametrize("status, expected", [("jamais", "jamais@ohn.test"), ("invite", "invite@ohn.test"),
                                              ("sans_acces", "fiche@ohn.test"), ("actif", "prof@ohn.test")])
def test_status_filter(client, as_role, users, status, expected):
    as_role(A)
    record("Ja", "Mais", email="jamais@ohn.test", active=True)
    invited = record("In", "Vite", email="invite@ohn.test", active=True)
    accounts.issue(invited, AccountToken.INVITE)
    record("Fi", "Che", email="fiche@ohn.test")
    users[P].last_login_at = datetime.now()
    db.session.commit()
    text = client.get(f"/utilisateurs?statut={status}").text
    assert expected in text
    others = {"jamais@ohn.test", "invite@ohn.test", "fiche@ohn.test", "prof@ohn.test"} - {expected}
    assert not any(email in text for email in others)


def test_invitation_when_smtp_fails_shows_link(client, as_role, app, monkeypatch):
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="inventaire@ohn.test")

    def broken(*args, **kwargs):
        raise OSError("serveur injoignable")
    monkeypatch.setattr(mailer, "send", broken)
    as_role(A)
    r = client.post("/utilisateurs/nouveau", data=person(email="lea@ohn.test", active="y"))
    assert "pas pu partir" in r.text and "/lien/" in r.text
