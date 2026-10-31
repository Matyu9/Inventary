"""Demandes de prêt faites par un emprunteur pour lui-même : le garant choisi accepte, puis l'admin valide."""
import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Activity, Category, Item, Loan, LoanStatus, LoanType, Notification, Role
from app.services import loans as svc
from app.services import reminders

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


def soon(days):
    return (datetime.now() + timedelta(days=days)).replace(second=0, microsecond=0)


@pytest.fixture
def sax(app):
    item = Item(code="OHN-0001", name="Saxophone", category=Category(name="Bois"))
    db.session.add(item)
    db.session.commit()
    return item


@pytest.fixture
def ctx(app):
    """Le service construit les liens avec url_for : il lui faut une requête."""
    with app.test_request_context():
        yield


def request_form(users, item, **kw):
    data = {"type": LoanType.RESERVATION, "borrower_id": users[E].id, "guarantor_id": users[P].id,
            "title": "Stage d'été", "start": soon(1).strftime("%Y-%m-%dT%H:%M"),
            "end": soon(3).strftime("%Y-%m-%dT%H:%M"), "item_ids": [item.id], "comment": "Pour le stage"}
    data.update(kw)
    return data


def borrower_requests(client, as_role, users, sax, **kw):
    as_role(E)
    r = client.post("/prets/nouveau", data=request_form(users, sax, **kw))
    return r, db.session.scalar(db.select(Loan).order_by(Loan.id.desc()))


def notified(kind):
    return set(db.session.scalars(db.select(Notification.user_id).where(Notification.kind == kind)))


# ---------------------------------------------------------------------------
# Formulaire
# ---------------------------------------------------------------------------

def test_borrower_form_is_for_self_with_guarantor_choice(client, as_role, users, sax):
    other_borrower = make_user(E, email="autre@ohn.test")
    inactive_prof = make_user(P, email="parti@ohn.test", active=False)
    as_role(E)
    page = client.get("/prets/nouveau").text
    assert "Demander du matériel" in page and "il devra accepter votre demande" in page
    assert 'id="borrower_id"' not in page and f'name="borrower_id" value="{users[E].id}"' in page
    guarantors = re.search(r'<select[^>]*id="guarantor_id".*?</select>', page, re.S).group(0)
    assert f'value="{users[P].id}"' in guarantors and f'value="{users[A].id}"' in guarantors
    assert f'value="{other_borrower.id}"' not in guarantors and f'value="{inactive_prof.id}"' not in guarantors
    assert "Caution" not in page and "Valider directement" not in page


def test_request_buttons_shown_to_borrower(client, as_role, users):
    as_role(E)
    assert "/prets/nouveau" in client.get("/").text
    assert "/prets/nouveau" in client.get("/mes-prets").text


def test_equipment_search_for_borrower(client, as_role, users, sax):
    as_role(E)
    r = client.get("/prets/recherche-materiel?q=sax")
    assert r.status_code == 200 and "OHN-0001" in r.text


# ---------------------------------------------------------------------------
# Parcours complet
# ---------------------------------------------------------------------------

def test_full_flow_borrower_guarantor_admin(client, as_role, users, sax):
    r, loan = borrower_requests(client, as_role, users, sax)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/prets/{loan.id}")
    assert loan.borrower_id == users[E].id and loan.requested_by_id == users[E].id
    assert loan.guarantor_id == users[P].id and loan.status == LoanStatus.DEMANDE and loan.awaiting_guarantor
    assert loan.deposit_amount is None and loan.status_label == "En attente du garant"
    # Seul le garant est prévenu ; les admins attendent son accord.
    assert notified("loan.guarantee_request") == {users[P].id}
    assert notified("loan.request") == set()
    page = client.get(f"/prets/{loan.id}").text
    assert "En attente du garant" in page and "accord de son garant" in page

    # Le garant voit la demande et l'accepte.
    as_role(P)
    assert f"/prets/{loan.id}" in client.get("/mes-prets").text
    assert "Demandes à confirmer comme garant" in client.get("/").text
    page = client.get(f"/prets/{loan.id}").text
    assert "Accepter d&#39;être garant" in page and "Valider</button>" not in page
    r = client.post(f"/prets/{loan.id}/garant/accepter", follow_redirects=True)
    assert "attend maintenant la validation" in r.text
    db.session.expire_all()
    assert loan.status == LoanStatus.DEMANDE and not loan.awaiting_guarantor
    assert notified("loan.request") == {users[A].id}
    assert notified("loan.guarantee_accepted") == {users[E].id}

    # L'admin valide puis remet le matériel.
    as_role(A)
    assert f"/prets/{loan.id}" in client.get("/").text  # dans « Demandes à valider »
    client.post(f"/prets/{loan.id}/valider")
    client.post(f"/prets/{loan.id}/remise")
    db.session.expire_all()
    assert loan.status == LoanStatus.EN_COURS
    actions = db.session.scalars(db.select(Activity.action).where(Activity.loan_id == loan.id,
                                                                  Activity.item_id.is_(None))).all()
    assert actions[:2] == ["loan.request", "loan.guarantee_accept"]


def test_guarantor_refuses(client, as_role, users, sax):
    _, loan = borrower_requests(client, as_role, users, sax)
    as_role(P)
    r = client.post(f"/prets/{loan.id}/garant/refuser", data={"reason": "Pas inscrit au stage"},
                    follow_redirects=True)
    assert "Demande refusée" in r.text
    db.session.expire_all()
    assert loan.status == LoanStatus.REFUSE and loan.refusal_reason == "Pas inscrit au stage"
    assert loan.validated_by_id == users[P].id and not loan.awaiting_guarantor
    assert notified("loan.guarantee_refused") == {users[E].id}
    assert "Pas inscrit au stage" in client.get(f"/prets/{loan.id}").text


def test_borrower_cancels_own_request(client, as_role, users, sax):
    _, loan = borrower_requests(client, as_role, users, sax)
    assert "Annuler" in client.get(f"/prets/{loan.id}").text
    client.post(f"/prets/{loan.id}/annuler")
    db.session.expire_all()
    assert loan.status == LoanStatus.ANNULE
    assert users[P].id in notified("loan.cancelled")


def test_admin_can_refuse_while_waiting(client, as_role, users, sax):
    _, loan = borrower_requests(client, as_role, users, sax)
    as_role(A)
    client.post(f"/prets/{loan.id}/refuser", data={"reason": "Stage annulé"})
    db.session.expire_all()
    assert loan.status == LoanStatus.REFUSE and not loan.awaiting_guarantor


# ---------------------------------------------------------------------------
# Garde-fous
# ---------------------------------------------------------------------------

def test_admin_cannot_validate_before_guarantor(client, as_role, users, sax):
    _, loan = borrower_requests(client, as_role, users, sax)
    as_role(A)
    page = client.get(f"/prets/{loan.id}").text
    assert ">Valider</button>" not in page
    r = client.post(f"/prets/{loan.id}/valider", follow_redirects=True)
    assert "attend encore l&#39;accord de son garant" in r.text
    db.session.expire_all()
    assert loan.status == LoanStatus.DEMANDE


def test_borrower_cannot_request_for_someone_else(client, as_role, users, sax):
    other = make_user(E, email="autre@ohn.test")
    r, loan = borrower_requests(client, as_role, users, sax, borrower_id=other.id)
    assert r.status_code == 200 and loan is None


def test_guarantor_is_mandatory(client, as_role, users, sax):
    r, loan = borrower_requests(client, as_role, users, sax, guarantor_id="0")
    assert r.status_code == 200 and loan is None and "garant" in r.text


def test_guarantor_must_have_the_right(client, as_role, users, sax):
    other = make_user(E, email="autre@ohn.test")
    r, loan = borrower_requests(client, as_role, users, sax, guarantor_id=other.id)
    assert r.status_code == 200 and loan is None


def test_borrower_cannot_self_validate_or_set_deposit(client, as_role, users, sax):
    _, loan = borrower_requests(client, as_role, users, sax, auto_validate="y", deposit_amount="200")
    assert loan.status == LoanStatus.DEMANDE and loan.awaiting_guarantor and loan.deposit_amount is None
    assert client.post(f"/prets/{loan.id}/valider").status_code == 403
    assert client.post(f"/prets/{loan.id}/garant/accepter").status_code == 403


def test_only_designated_guarantor_answers(client, as_role, users, sax):
    other_prof = make_user(P, email="p2@ohn.test")
    _, loan = borrower_requests(client, as_role, users, sax)
    client.post("/deconnexion")
    login(client, other_prof)
    assert client.post(f"/prets/{loan.id}/garant/accepter").status_code == 403
    as_role(A)  # même un admin : c'est l'accord du garant qui est demandé
    assert client.post(f"/prets/{loan.id}/garant/accepter").status_code == 403
    db.session.expire_all()
    assert loan.awaiting_guarantor


def test_answer_only_once(app, users, sax, ctx):
    loan, _ = svc.create_request(loan_type=LoanType.RESERVATION, borrower=users[E], guarantor=users[P],
                                 requested_by=users[E], start=soon(1), end=soon(2),
                                 lines=svc.build_lines([sax.id], {}, []), awaiting_guarantor=True)
    svc.guarantor_accept(loan, users[P])
    with pytest.raises(svc.LoanError):
        svc.guarantor_accept(loan, users[P])
    with pytest.raises(svc.LoanError):
        svc.guarantor_refuse(loan, users[P])


def test_service_rejects_self_guarantee(app, users, sax, ctx):
    with pytest.raises(svc.LoanError, match="autre personne"):
        svc.create_request(loan_type=LoanType.RESERVATION, borrower=users[P], guarantor=users[P],
                           requested_by=users[P], start=soon(1), end=soon(2),
                           lines=svc.build_lines([sax.id], {}, []), awaiting_guarantor=True)


def test_waiting_request_does_not_block_equipment(client, as_role, users, sax):
    _, waiting = borrower_requests(client, as_role, users, sax)
    problems, warnings = svc.check_availability(svc.build_lines([sax.id], {}, []), soon(1), soon(3))
    assert problems == [] and any(f"n°{waiting.id}" in w for w in warnings)


def test_guarantor_without_list_right_can_open_request(client, app, as_role, users, sax):
    db.session.add(Role(key="garant", name="Garant", permissions=["myloans.view", "loan.guarantee"]))
    db.session.commit()
    garant = make_user("garant", email="garant@ohn.test")
    _, loan = borrower_requests(client, as_role, users, sax, guarantor_id=garant.id)
    client.post("/deconnexion")
    login(client, garant)
    assert client.get(f"/prets/{loan.id}").status_code == 200
    client.post(f"/prets/{loan.id}/garant/accepter")
    db.session.expire_all()
    assert not loan.awaiting_guarantor


# ---------------------------------------------------------------------------
# Listes et rappels
# ---------------------------------------------------------------------------

def test_loan_views_split_waiting_requests(client, as_role, users, sax):
    _, loan = borrower_requests(client, as_role, users, sax)
    as_role(A)
    assert f"/prets/{loan.id}" not in client.get("/prets?vue=demande").text
    page = client.get("/prets?vue=garant").text
    assert f"/prets/{loan.id}" in page and "Attente du garant" in page
    assert f"/prets/{loan.id}" not in client.get("/").text  # pas dans « Demandes à valider »


def test_guarantor_reminded_after_three_days(client, as_role, users, sax):
    _, loan = borrower_requests(client, as_role, users, sax)
    db.session.execute(db.delete(Notification))
    loan.created_at = datetime.now() - timedelta(days=4)
    db.session.commit()
    reminders.run()
    assert notified("loan.guarantee_request") == {users[P].id}
    assert notified("loan.pending") == set()  # les admins ne sont pas relancés pour une demande sans garant
    reminders.run()  # une relance par jour
    assert db.session.scalar(db.select(db.func.count(Notification.id))
                             .where(Notification.kind == "loan.guarantee_request")) == 1


def test_no_reminder_before_three_days(client, as_role, users, sax):
    _, loan = borrower_requests(client, as_role, users, sax)
    db.session.execute(db.delete(Notification))
    loan.created_at = datetime.now() - timedelta(days=2)
    db.session.commit()
    reminders.run()
    assert notified("loan.guarantee_request") == set()


def test_guarantee_email_texts(app, users, sax, monkeypatch, ctx):
    from app.services import mailer
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="inventaire@ohn.test")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject)))
    svc.create_request(loan_type=LoanType.RESERVATION, borrower=users[E], guarantor=users[P], requested_by=users[E],
                       start=soon(1), end=soon(2), lines=svc.build_lines([sax.id], {}, []), awaiting_guarantor=True)
    db.session.commit()
    mailer.deliver_pending()
    assert (users[P].email, f"[Mon association] {users[E].full_name} vous demande d'être garant") in sent

