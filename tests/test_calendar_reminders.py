import smtplib
from datetime import date, datetime, timedelta

import pytest
from icalendar import Calendar

from app.extensions import db
from app.models import (Category, ConsumptionMode, Item, ItemStatus, Loan, LoanLine, LoanStatus, LoanType,
                        Maintenance, Notification, Reminder, Role, Tracking)
from app.services import mailer, reminders

from .conftest import make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR
NOW = datetime(2030, 3, 15, 12, 0)


@pytest.fixture
def gear(app):
    bois = Category(name="Bois")
    sax = Item(code="OHN-0001", name="Saxophone", category=bois)
    flute = Item(code="OHN-0002", name="Flûte", category=bois)
    reeds = Item(code="OHN-0003", name="Anches", category=bois, tracking=Tracking.QUANTITE, quantity=2,
                 consumption_mode=ConsumptionMode.CONSOMME)
    db.session.add_all([sax, flute, reeds])
    db.session.commit()
    return dict(sax=sax, flute=flute, reeds=reeds)


def loan(users, item, status, start, end=None, loan_type=LoanType.RESERVATION, borrower=None, **kw):
    l = Loan(type=loan_type, status=status, borrower=borrower or users[E], guarantor=users[P],
             requested_by=users[P], start=start, end=end, **kw)
    l.lines = [LoanLine(item=item, quantity=1)]
    db.session.add(l)
    db.session.commit()
    return l


def notes_for(user):
    return db.session.scalars(db.select(Notification.message).filter_by(user_id=user.id)).all()


# ---------------------------------------------------------------------------
# Rappels automatiques
# ---------------------------------------------------------------------------

def test_run_with_nothing(app, users):
    assert reminders.run(NOW) == "0 notification(s) créée(s), 0 email(s) envoyé(s)."


def test_manual_reminder(app, users, gear):
    r = Reminder(item=gear["sax"], due_date=NOW.date(), message="Fin de garantie", recipients=[users[P]])
    future = Reminder(item=gear["sax"], due_date=NOW.date() + timedelta(days=1), message="Plus tard")
    db.session.add_all([r, future])
    db.session.commit()
    result = reminders.run(NOW)
    assert "rappels : 1" in result and r.sent and not future.sent
    assert notes_for(users[P]) == ["Rappel — OHN-0001 Saxophone : Fin de garantie"]
    reminders.run(NOW)
    assert len(notes_for(users[P])) == 1


def test_manual_reminder_defaults_to_admins(app, users, gear):
    db.session.add(Reminder(item=gear["sax"], due_date=NOW.date() - timedelta(days=3), message="Assurance"))
    db.session.commit()
    reminders.run(NOW)
    assert len(notes_for(users[A])) == 1 and notes_for(users[P]) == []


def test_maintenance_due(app, users, gear):
    db.session.add_all([
        Maintenance(item=gear["sax"], date=date(2029, 3, 1), kind="révision", next_due=date(2030, 3, 20)),
        Maintenance(item=gear["flute"], date=date(2029, 3, 1), kind="révision", next_due=date(2030, 6, 1)),
    ])
    db.session.commit()
    reminders.run(NOW)
    reminders.run(NOW)
    assert notes_for(users[A]) == ["Entretien prévu le 20/03/2030 : OHN-0001 Saxophone (révision)."]


def test_maintenance_overdue_and_superseded(app, users, gear):
    db.session.add_all([
        Maintenance(item=gear["sax"], date=date(2029, 1, 1), kind="révision", next_due=date(2030, 1, 1)),
        Maintenance(item=gear["flute"], date=date(2029, 1, 1), kind="révision", next_due=date(2030, 1, 1)),
        # Une révision plus récente remplace l'échéance de la flûte :
        Maintenance(item=gear["flute"], date=date(2030, 2, 1), kind="révision", next_due=date(2031, 2, 1)),
    ])
    db.session.commit()
    reminders.run(NOW)
    assert notes_for(users[A]) == ["Entretien dépassé : OHN-0001 Saxophone (révision)."]


def test_due_maintenances_uses_latest_by_date(app, gear):
    recent = Maintenance(item=gear["sax"], date=date(2030, 2, 1), kind="révision", next_due=date(2031, 1, 1))
    db.session.add(recent)
    db.session.commit()
    # Saisie a posteriori d'un entretien plus ancien : ne doit pas réactiver l'ancienne échéance.
    db.session.add(Maintenance(item=gear["sax"], date=date(2028, 1, 1), kind="révision", next_due=date(2029, 1, 1)))
    db.session.commit()
    assert reminders.due_maintenances(date(2030, 6, 1)) == []


def test_reservation_start_soon(app, users, gear):
    l = loan(users, gear["sax"], LoanStatus.VALIDE, NOW + timedelta(hours=20), NOW + timedelta(days=2))
    loan(users, gear["flute"], LoanStatus.VALIDE, NOW + timedelta(days=3), NOW + timedelta(days=4))
    reminders.run(NOW)
    reminders.run(NOW + timedelta(hours=1))
    msgs = notes_for(users[E])
    assert msgs == [f"Réservation n°{l.id} : le matériel est à récupérer le 16/03 à 08:00."]
    assert len(notes_for(users[P])) == 1


def test_return_due_soon(app, users, gear):
    l = loan(users, gear["sax"], LoanStatus.EN_COURS, NOW - timedelta(days=2), NOW + timedelta(hours=5))
    reminders.run(NOW)
    assert notes_for(users[E]) == [f"Réservation n°{l.id} : retour du matériel attendu le 15/03 à 17:00."]


def test_overdue_daily(app, users, gear):
    l = loan(users, gear["sax"], LoanStatus.EN_COURS, NOW - timedelta(days=5), NOW - timedelta(days=2))
    reminders.run(NOW)
    reminders.run(NOW + timedelta(hours=2))
    assert notes_for(users[E]) == [f"Réservation n°{l.id} en retard de 2 jour(s) (Emprunteur Test)."]
    assert len(notes_for(users[A])) == 1 and len(notes_for(users[P])) == 1
    reminders.run(NOW + timedelta(days=1))
    assert len(notes_for(users[E])) == 2 and "3 jour(s)" in notes_for(users[E])[1]


def test_overdue_same_day_says_since_when(app, users, gear):
    """Fin prévue aujourd'hui, déjà passée : pas de « en retard de 0 jour(s) »."""
    now = NOW.replace(hour=15, minute=0, second=0)
    l = loan(users, gear["sax"], LoanStatus.EN_COURS, now - timedelta(days=2), now.replace(hour=9))
    reminders.run(now)
    assert notes_for(users[E]) == [f"Réservation n°{l.id} en retard depuis 09:00 (Emprunteur Test)."]


def test_not_picked_up(app, users, gear):
    l = loan(users, gear["sax"], LoanStatus.VALIDE, NOW - timedelta(days=3), NOW + timedelta(days=1))
    reminders.run(NOW)
    reminders.run(NOW + timedelta(days=1))
    assert notes_for(users[A]) == [f"Réservation n°{l.id} validée mais jamais remise."]


def test_pending_request_reminder(app, users, gear):
    l = loan(users, gear["sax"], LoanStatus.DEMANDE, NOW + timedelta(days=10), NOW + timedelta(days=11))
    l.created_at = NOW - timedelta(days=3)
    db.session.commit()
    reminders.run(NOW)
    assert notes_for(users[A]) == [f"La demande n°{l.id} attend toujours une validation."]


def low_stock(gear):
    gear["reeds"].low_stock_threshold = 5
    db.session.commit()


def test_low_stock_weekly(app, users, gear):
    low_stock(gear)
    reminders.run(NOW)
    reminders.run(NOW + timedelta(days=1))
    assert len(notes_for(users[A])) == 1
    reminders.run(NOW + timedelta(days=7))
    assert len(notes_for(users[A])) == 2
    assert notes_for(users[A])[0] == "Stock bas : Anches (2 restant(s))."


def test_inactive_admin_not_notified(app, users, gear):
    low_stock(gear)
    users[A].active = False
    db.session.commit()
    reminders.run(NOW)
    assert notes_for(users[A]) == []


def test_cli_run_reminders(app, users, gear):
    result = app.test_cli_runner().invoke(args=["run-reminders"])
    assert result.exit_code == 0 and "notification(s)" in result.output


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

class FakeSMTP:
    sent, fail = [], False

    def __init__(self, host, port, timeout):
        self.host = host

    def __enter__(self):
        if FakeSMTP.fail:
            raise OSError("connexion refusée")
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(app, monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="inventaire@ohn.fr", BASE_URL="https://inv.ohn.fr")
    return FakeSMTP


def test_smtp_disabled_by_default(app):
    assert not mailer.smtp_enabled()


def test_deliver_pending(app, users, smtp):
    db.session.add_all([Notification(user_id=users[P].id, message="Bonjour", link="/prets/1"),
                        Notification(user_id=users[E].id, message="Pour l'élève")])
    db.session.commit()
    assert mailer.deliver_pending() == 2
    first = smtp.sent[0]
    assert first["To"] == users[P].email and first["From"] == "inventaire@ohn.fr"
    assert "https://inv.ohn.fr/prets/1" in first.get_content()
    assert mailer.deliver_pending() == 0


def test_no_email_for_users_without_address_or_inactive(app, users, smtp):
    record = make_user(E, email="fiche@ohn.test", active=False)
    record.email = None
    users[P].active = False
    db.session.add_all([Notification(user_id=record.id, message="x"), Notification(user_id=users[P].id, message="y")])
    db.session.commit()
    assert mailer.deliver_pending() == 0


def test_old_notifications_not_emailed(app, users, smtp):
    n = Notification(user_id=users[P].id, message="Vieux")
    db.session.add(n)
    db.session.commit()
    n.created_at = datetime.now() - timedelta(days=5)
    db.session.commit()
    assert mailer.deliver_pending() == 0


def test_smtp_failure_retries_later(app, users, smtp):
    db.session.add(Notification(user_id=users[P].id, message="x"))
    db.session.commit()
    smtp.fail = True
    assert mailer.deliver_pending() == 0
    smtp.fail = False
    assert mailer.deliver_pending() == 1


def test_deliver_quietly_never_raises(app, users, smtp, monkeypatch):
    monkeypatch.setattr(mailer, "deliver_pending", lambda limit: 1 / 0)
    assert mailer.deliver_quietly() == 0


def test_loan_validation_emails_immediately(client, as_role, users, gear, smtp):
    l = loan(users, gear["sax"], LoanStatus.DEMANDE, datetime.now() + timedelta(days=1),
             datetime.now() + timedelta(days=2))
    as_role(A)
    client.post(f"/prets/{l.id}/valider")
    assert {m["To"] for m in smtp.sent} == {users[E].email, users[P].email}


def test_reminders_send_emails(app, users, gear, smtp):
    low_stock(gear)
    reminders.run(datetime.now())  # stock bas → admins
    assert [m["To"] for m in smtp.sent] == [users[A].email]


# ---------------------------------------------------------------------------
# Calendrier
# ---------------------------------------------------------------------------

def test_calendar_page(client, as_role, users):
    for role in Role.ALL:
        as_role(role)
        r = client.get("/calendrier")
        assert r.status_code == 200 and "fullcalendar.min.js" in r.text
        assert users[role].ical_token in r.text


def test_events_feed(client, as_role, users, gear):
    now = datetime.now().replace(microsecond=0)
    resa = loan(users, gear["sax"], LoanStatus.VALIDE, now + timedelta(days=1), now + timedelta(days=2), title="Concert")
    garde = loan(users, gear["flute"], LoanStatus.EN_COURS, now - timedelta(days=10), loan_type=LoanType.GARDE)
    loan(users, gear["sax"], LoanStatus.REFUSE, now + timedelta(days=1), now + timedelta(days=2))
    db.session.add(Maintenance(item=gear["sax"], date=now.date(), kind="révision", next_due=(now + timedelta(days=5)).date()))
    db.session.commit()
    as_role(P)
    r = client.get("/calendrier/evenements", query_string={"start": (now - timedelta(days=15)).isoformat(),
                                                            "end": (now + timedelta(days=15)).isoformat()})
    events = {e["id"]: e for e in r.get_json()}
    assert set(events) == {f"loan-{resa.id}", f"loan-{garde.id}", f"maint-{db.session.scalar(db.select(Maintenance.id))}"}
    assert events[f"loan-{resa.id}"]["title"] == "Concert — Emprunteur Test : Saxophone"
    assert events[f"loan-{resa.id}"]["url"] == f"/prets/{resa.id}"
    assert events[f"loan-{garde.id}"]["extendedProps"]["openEnded"] is True
    assert events[f"loan-{garde.id}"]["title"].startswith("Garde — ")


def test_events_borrower_sees_only_own(client, as_role, users, gear):
    now = datetime.now()
    other = make_user(E, email="autre@ohn.test")
    mine = loan(users, gear["sax"], LoanStatus.VALIDE, now + timedelta(days=1), now + timedelta(days=2))
    loan(users, gear["flute"], LoanStatus.VALIDE, now + timedelta(days=1), now + timedelta(days=2), borrower=other)
    db.session.add(Maintenance(item=gear["sax"], date=now.date(), kind="r", next_due=(now + timedelta(days=3)).date()))
    db.session.commit()
    as_role(E)
    ids = [e["id"] for e in client.get("/calendrier/evenements").get_json()]
    assert ids == [f"loan-{mine.id}"]


def test_events_overdue_color(client, as_role, users, gear):
    now = datetime.now()
    loan(users, gear["sax"], LoanStatus.EN_COURS, now - timedelta(days=5), now - timedelta(days=1))
    as_role(P)
    assert client.get("/calendrier/evenements").get_json()[0]["color"] == "#c0392b"


def test_events_overdue_still_shown_after_planned_end(client, as_role, users, gear):
    """Pas rendu : le prêt reste visible dans les mois suivants, jusqu'à aujourd'hui."""
    now = datetime.now()
    late = loan(users, gear["sax"], LoanStatus.EN_COURS, now - timedelta(days=90), now - timedelta(days=60))
    loan(users, gear["flute"], LoanStatus.RENDU, now - timedelta(days=90), now - timedelta(days=60))
    as_role(P)
    window = {"start": (now - timedelta(days=7)).isoformat(), "end": (now + timedelta(days=7)).isoformat()}
    events = client.get("/calendrier/evenements", query_string=window).get_json()
    assert [e["id"] for e in events] == [f"loan-{late.id}"]
    assert events[0]["end"] >= (now - timedelta(minutes=1)).isoformat()


@pytest.mark.parametrize("params", [{"start": "2030-01-10", "end": "2030-01-01"},
                                    {"start": "2030-01-01", "end": "2032-01-01"}])
def test_events_bad_range(client, as_role, params):
    as_role(P)
    assert client.get("/calendrier/evenements", query_string=params).status_code == 400


def test_events_garbage_dates_fall_back(client, as_role):
    as_role(P)
    assert client.get("/calendrier/evenements?start=abc&end=def").status_code == 200


def test_ical_feed(client, users, gear):
    now = datetime.now().replace(microsecond=0)
    resa = loan(users, gear["sax"], LoanStatus.VALIDE, now + timedelta(days=1), now + timedelta(days=2))
    garde = loan(users, gear["flute"], LoanStatus.EN_COURS, now - timedelta(days=3), loan_type=LoanType.GARDE)
    loan(users, gear["sax"], LoanStatus.DEMANDE, now + timedelta(days=5), now + timedelta(days=6))
    r = client.get(f"/cal/{users[P].ical_token}.ics")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    cal = Calendar.from_ical(r.data)
    events = {str(e["uid"]): e for e in cal.walk("VEVENT")}
    assert set(events) == {f"loan-{resa.id}@inventaire", f"loan-{garde.id}@inventaire"}
    assert events[f"loan-{resa.id}@inventaire"]["dtstart"].dt == resa.start
    assert events[f"loan-{garde.id}@inventaire"]["dtstart"].dt == garde.start.date()


def test_ical_borrower_only_own(client, users, gear):
    other = make_user(E, email="autre@ohn.test")
    now = datetime.now()
    loan(users, gear["sax"], LoanStatus.VALIDE, now + timedelta(days=1), now + timedelta(days=2), borrower=other)
    cal = Calendar.from_ical(client.get(f"/cal/{users[E].ical_token}.ics").data)
    assert cal.walk("VEVENT") == []


def test_ical_bad_token_or_inactive(client, users):
    assert client.get("/cal/nimporte.ics").status_code == 404
    users[P].active = False
    db.session.commit()
    assert client.get(f"/cal/{users[P].ical_token}.ics").status_code == 404


def test_reset_ical_token(client, as_role, users):
    user = as_role(P)
    old = user.ical_token
    client.post("/calendrier/nouveau-lien")
    db.session.refresh(user)
    assert user.ical_token != old
    assert client.get(f"/cal/{old}.ics").status_code == 404
    assert client.get(f"/cal/{user.ical_token}.ics").status_code == 200


# ---------------------------------------------------------------------------
# Entretiens et rappels sur la fiche
# ---------------------------------------------------------------------------

def test_add_maintenance(client, as_role, gear):
    as_role(P)
    r = client.post(f"/objets/{gear['sax'].id}/entretiens", data={
        "date": "2030-03-01", "kind": "réparation", "provider": "Luthier Dupont", "cost": "85,50",
        "next_due": "2031-03-01", "status": ItemStatus.EN_ENTRETIEN, "description": "Tampons"})
    assert r.headers["Location"].endswith("#entretiens")
    m = db.session.scalar(db.select(Maintenance))
    assert m.provider == "Luthier Dupont" and str(m.cost) == "85.50" and m.next_due == date(2031, 3, 1)
    db.session.refresh(gear["sax"])
    assert gear["sax"].status == ItemStatus.EN_ENTRETIEN
    page = client.get(f"/objets/{gear['sax'].id}").text
    assert "Luthier Dupont" in page and "85,50 €" in page


def test_maintenance_back_to_available(client, as_role, gear):
    gear["sax"].status = ItemStatus.EN_ENTRETIEN
    db.session.commit()
    as_role(P)
    client.post(f"/objets/{gear['sax'].id}/entretiens", data={"date": "2030-03-01", "kind": "révision",
                                                               "status": ItemStatus.DISPONIBLE})
    db.session.refresh(gear["sax"])
    assert gear["sax"].status == ItemStatus.DISPONIBLE


def test_maintenance_does_not_touch_lent_item(client, as_role, gear):
    gear["sax"].status = ItemStatus.EN_GARDE
    db.session.commit()
    as_role(P)
    r = client.post(f"/objets/{gear['sax'].id}/entretiens", data={"date": "2030-03-01", "kind": "révision",
                                                                   "status": ItemStatus.EN_ENTRETIEN},
                    follow_redirects=True)
    assert "Statut inchangé" in r.text and gear["sax"].status == ItemStatus.EN_GARDE


@pytest.mark.parametrize("data,message", [
    ({"date": "", "kind": "révision"}, "Date"),
    ({"date": "2030-03-01", "kind": "révision", "next_due": "2030-02-01"}, "doit être après"),
    ({"date": "2030-03-01", "kind": "magie"}, "Type"),
    ({"date": "2030-03-01", "kind": "révision", "cost": "-1"}, "Coût"),
])
def test_maintenance_validation(client, as_role, gear, data, message):
    as_role(P)
    r = client.post(f"/objets/{gear['sax'].id}/entretiens", data=data, follow_redirects=True)
    assert message in r.text and db.session.scalar(db.select(db.func.count(Maintenance.id))) == 0


def test_maintenance_rights(client, as_role, users, gear):
    m = Maintenance(item=gear["sax"], date=date(2030, 1, 1), kind="révision", recorded_by_id=users[A].id)
    db.session.add(m)
    db.session.commit()
    as_role(E)
    assert client.post(f"/objets/{gear['sax'].id}/entretiens", data={}).status_code == 403
    as_role(P)
    assert client.post(f"/entretiens/{m.id}/supprimer").status_code == 403
    as_role(A)
    assert client.post(f"/entretiens/{m.id}/supprimer").status_code == 302
    assert db.session.scalar(db.select(db.func.count(Maintenance.id))) == 0


def test_prof_deletes_own_maintenance(client, as_role, gear):
    as_role(P)
    client.post(f"/objets/{gear['sax'].id}/entretiens", data={"date": "2030-03-01", "kind": "révision"})
    m = db.session.scalar(db.select(Maintenance))
    assert client.post(f"/entretiens/{m.id}/supprimer").status_code == 302


def test_add_and_delete_reminder(client, as_role, users, gear):
    as_role(P)
    client.post(f"/objets/{gear['sax'].id}/rappels", data={"due_date": "2030-05-01", "message": "Fin de garantie",
                                                            "recipients": [users[P].id]})
    reminder = db.session.scalar(db.select(Reminder))
    assert reminder.recipients == [users[P]] and reminder.message == "Fin de garantie"
    assert "Fin de garantie" in client.get(f"/objets/{gear['sax'].id}").text
    client.post(f"/rappels/{reminder.id}/supprimer")
    assert db.session.scalar(db.select(db.func.count(Reminder.id))) == 0


def test_reminder_validation(client, as_role, users, gear):
    as_role(P)
    r = client.post(f"/objets/{gear['sax'].id}/rappels", data={"due_date": "", "message": ""}, follow_redirects=True)
    assert "obligatoire" in r.text.lower() or "required" in r.text.lower() or "Date" in r.text
    assert db.session.scalar(db.select(db.func.count(Reminder.id))) == 0
    client.post(f"/objets/{gear['sax'].id}/rappels", data={"due_date": "2030-01-01", "message": "x",
                                                            "recipients": [users[E].id]})
    assert db.session.scalar(db.select(db.func.count(Reminder.id))) == 0  # emprunteur non proposé


def test_borrower_cannot_add_reminder(client, as_role, gear):
    as_role(E)
    assert client.post(f"/objets/{gear['sax'].id}/rappels", data={}).status_code == 403


def test_dashboard_shows_due_maintenance(client, as_role, gear):
    db.session.add(Maintenance(item=gear["sax"], date=date.today(), kind="révision",
                               next_due=date.today() + timedelta(days=10)))
    db.session.commit()
    as_role(P)
    r = client.get("/").text
    assert "OHN-0001 Saxophone</a> (révision)" in r


# ---------------------------------------------------------------------------
# Paramètres
# ---------------------------------------------------------------------------

def test_settings_admin_only(client, as_role):
    as_role(P)
    assert client.get("/parametres").status_code == 403
    as_role(A)
    r = client.get("/parametres")
    assert r.status_code == 200 and "Désactivés" in r.text


def test_settings_test_email(client, as_role, smtp):
    as_role(A)
    r = client.post("/parametres/email-test", follow_redirects=True)
    assert "Email de test envoyé" in r.text and len(smtp.sent) == 1


def test_settings_test_email_without_smtp(client, as_role):
    as_role(A)
    assert "pas configuré" in client.post("/parametres/email-test", follow_redirects=True).text


def test_settings_test_email_failure(client, as_role, smtp):
    smtp.fail = True
    as_role(A)
    assert "Échec de l&#39;envoi" in client.post("/parametres/email-test", follow_redirects=True).text or \
        "Échec de l'envoi" in client.post("/parametres/email-test", follow_redirects=True).text


def test_settings_run_reminders(client, as_role, gear):
    as_role(A)
    assert "notification(s) créée(s)" in client.post("/parametres/rappels", follow_redirects=True).text
