"""Contenu des emails (HTML + texte) et personnalisation de leurs textes depuis les paramètres."""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import (Activity, EmailTemplate, Kit, Loan, LoanLine, LoanStatus, LoanType, Location, Notification,
                        Role)
from app.services import mailer, reminders
from app.services.notify import notify

from .test_calendar_reminders import gear, loan, smtp  # noqa: F401 (fixtures)

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR
SOON = datetime.now().replace(second=0, microsecond=0) + timedelta(days=3)


def parts(message):
    return message.get_body("plain").get_content(), message.get_body("html").get_content()


def note(user, kind, loan=None, item=None, message="Le message.", link="/prets/1"):
    created = notify([user], message, link, kind=kind, loan=loan, item=item)
    db.session.commit()
    return created[0]


@pytest.fixture
def resa(users, gear):
    l = loan(users, gear["sax"], LoanStatus.VALIDE, SOON, SOON + timedelta(days=2), title="Concert de Noël")
    l.lines.append(LoanLine(item=gear["reeds"], quantity=3))
    db.session.commit()
    return l


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------

def test_email_is_multipart_with_sender_name(app, users, resa, smtp):
    note(users[E], "loan.validated", loan=resa)
    mailer.deliver_pending()
    message = smtp.sent[0]
    assert message.is_multipart()
    assert message["From"].addresses[0].display_name == f"Inventaire {app.config['ASSO_NAME']}"
    text, html = parts(message)
    assert text and html.lstrip().startswith("<!doctype html>")


def test_subject_no_longer_repeats_message(app, users, resa, smtp):
    note(users[E], "loan.validated", loan=resa, message="Votre réservation n°1 a été validée par Admin Test.")
    mailer.deliver_pending()
    assert smtp.sent[0]["Subject"] == f"[{app.config['ASSO_NAME']}] Votre réservation n°{resa.id} est validée"


def test_loan_recap(app, users, resa, smtp):
    note(users[E], "loan.validated", loan=resa)
    mailer.deliver_pending()
    text, html = parts(smtp.sent[0])
    for body in (text, html):
        assert "Bonjour Emprunteur," in body
        assert f"Réservation n°{resa.id}" in body
        assert "Concert de Noël" in body
        assert "Emprunteur Test" in body and "Prof Test" in body  # emprunteur et garant
        assert "OHN-0001 Saxophone" in body
        assert "OHN-0003 Anches × 3" in body
        assert "Le message." in body
        assert "https://inv.ohn.fr/prets/1" in body
        assert "Voir le prêt" in body
    assert "Motif du refus" not in text


def test_refusal_reason_in_recap(app, users, resa, smtp):
    resa.status, resa.refusal_reason = LoanStatus.REFUSE, "Déjà prêté"
    note(users[P], "loan.refused", loan=resa)
    mailer.deliver_pending()
    text, html = parts(smtp.sent[0])
    assert "Motif du refus : Déjà prêté" in text and "Déjà prêté" in html
    assert "#b42318" in html  # ton « danger »


def test_garde_without_end(app, users, gear, smtp):
    garde = loan(users, gear["flute"], LoanStatus.EN_COURS, SOON, loan_type=LoanType.GARDE)
    note(users[E], "loan.end_changed", loan=garde)
    mailer.deliver_pending()
    assert "Fin : Sans date de fin" in parts(smtp.sent[0])[0]


def test_kit_line(app, users, gear, smtp):
    kit = Kit(name="Pupitre bois")
    gear["sax"].kit = kit
    gear["flute"].kit = kit
    l = Loan(type=LoanType.RESERVATION, status=LoanStatus.VALIDE, borrower=users[E], requested_by=users[P],
             start=SOON, end=SOON + timedelta(days=1))
    l.lines = [LoanLine(kit=kit)]
    db.session.add_all([kit, l])
    db.session.commit()
    note(users[E], "loan.validated", loan=l)
    mailer.deliver_pending()
    assert "Kit Pupitre bois (2 objets)" in parts(smtp.sent[0])[0]


def test_item_recap(app, users, gear, smtp):
    gear["reeds"].low_stock_threshold = 5
    gear["reeds"].location = Location(name="Armoire")
    db.session.commit()
    note(users[A], "item.low_stock", item=gear["reeds"], link="/objets/3")
    mailer.deliver_pending()
    message = smtp.sent[0]
    text, html = parts(message)
    assert message["Subject"].endswith("Stock bas : Anches")
    assert "OHN-0003 Anches" in text and "Emplacement : Armoire" in text and "Seuil d'alerte : 5" in text
    assert "Voir le stock" in html


def test_html_is_escaped(app, users, gear, smtp):
    users[E].first_name = "<b>Zoé</b>"
    l = loan(users, gear["sax"], LoanStatus.VALIDE, SOON, SOON + timedelta(days=1), title="<script>x</script>")
    note(users[E], "loan.validated", loan=l, message="<img src=x>")
    mailer.deliver_pending()
    text, html = parts(smtp.sent[0])
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x>" not in html and "<b>Zoé</b>" not in html
    assert "<script>x</script>" in text  # la version texte n'est pas échappée


def test_generic_email_without_kind(app, users, smtp):
    db.session.add(Notification(user_id=users[P].id, message="Ancienne notification", link="/x"))
    db.session.commit()
    mailer.deliver_pending()
    message = smtp.sent[0]
    text, html = parts(message)
    assert message["Subject"].endswith("Ancienne notification")
    assert "Ouvrir l&#39;inventaire" in html and "https://inv.ohn.fr/x" in text


def test_generic_email_when_loan_deleted(app, users, resa, smtp):
    note(users[E], "loan.validated", loan=resa, message="Validé.")
    db.session.delete(resa)
    db.session.commit()
    assert mailer.deliver_pending() == 1
    assert smtp.sent[0]["Subject"].endswith("Validé.")


def test_long_subject_shortened(app, users, smtp):
    db.session.add(Notification(user_id=users[P].id, message="x" * 300))
    db.session.commit()
    mailer.deliver_pending()
    subject = smtp.sent[0]["Subject"]
    assert subject.endswith("…") and len(subject) < 130


def test_every_kind_renders(app, users, resa, gear):
    for kind in mailer.KINDS:
        n = note(users[A], kind, loan=resa if kind.startswith("loan.") else None,
                 item=None if kind.startswith("loan.") else gear["reeds"])
        subject, text, html = mailer.compose(n)
        assert "{" not in subject, kind  # toutes les variables par défaut sont connues
        assert "Bonjour Admin," in text and "</html>" in html


def test_every_notification_has_a_known_kind(app, users, gear, resa):
    """Les rappels automatiques posent tous un type connu du mailer."""
    now = datetime.now()
    gear["reeds"].low_stock_threshold = 5
    loan(users, gear["flute"], LoanStatus.EN_COURS, now - timedelta(days=5), now - timedelta(days=1))
    loan(users, gear["flute"], LoanStatus.DEMANDE, now + timedelta(days=10), now + timedelta(days=11),
         created_at=now - timedelta(days=3))
    resa.start = now + timedelta(hours=2)
    db.session.commit()
    reminders.run(now)
    kinds = set(db.session.scalars(db.select(Notification.kind)))
    assert kinds >= {"loan.overdue", "loan.pending", "loan.start_soon", "item.low_stock"}
    assert kinds <= set(mailer.KINDS)


def test_loan_actions_set_kind(client, as_role, users, gear, smtp):
    l = loan(users, gear["sax"], LoanStatus.DEMANDE, SOON, SOON + timedelta(days=1))
    as_role(A)
    client.post(f"/prets/{l.id}/valider")
    assert set(db.session.scalars(db.select(Notification.kind))) == {"loan.validated"}
    assert all(n.loan_id == l.id for n in db.session.scalars(db.select(Notification)))
    assert all("est validée" in m["Subject"] for m in smtp.sent)


def test_test_email_is_html(client, as_role, users, smtp):
    as_role(A)
    client.post("/parametres/email-test")
    text, html = parts(smtp.sent[0])
    assert "Bonjour Admin," in text and "l&#39;envoi d&#39;emails de l&#39;inventaire fonctionne" in html


# ---------------------------------------------------------------------------
# Textes personnalisés
# ---------------------------------------------------------------------------

def test_fill():
    values = {"numero": 12, "prenom": "Zoé"}
    assert mailer.fill("Prêt n°{numero} pour {prenom}", values) == "Prêt n°12 pour Zoé"
    assert mailer.fill("{inconnu} {numero}", values) == "{inconnu} 12"
    # Pas de format() : ni attributs ni index évalués.
    assert mailer.fill("{numero.__class__} {numero[0]}", values) == "{numero.__class__} {numero[0]}"


def test_unknown_variables():
    assert mailer.unknown_variables("{numero} {objet} {prenom}", "loan.validated") == ["objet"]
    assert mailer.unknown_variables("{code} {numero}", "item.low_stock") == ["numero"]


def test_custom_texts_used(app, users, resa, smtp):
    db.session.add(EmailTemplate(kind="loan.validated", subject="C'est bon pour la {type} « {titre} » !",
                                 intro="Bravo {prenom} !\n\nÀ récupérer le {debut}.\nMerci.", button="Go"))
    db.session.commit()
    note(users[E], "loan.validated", loan=resa)
    mailer.deliver_pending()
    message = smtp.sent[0]
    text, html = parts(message)
    assert message["Subject"].endswith("C'est bon pour la réservation « Concert de Noël » !")
    assert "Bravo Emprunteur !" in text and "Le message." not in text
    assert html.count("<p style=\"margin:0 0 12px") == 1 and html.count("<p style=\"margin:0 0 20px") == 1
    assert "À récupérer le" in html and "<br>Merci." in html
    assert ">Go</a>" in html


def test_custom_subject_newlines_removed(app, users, resa, smtp):
    db.session.add(EmailTemplate(kind="loan.validated", subject="Ligne 1\nBcc: pirate@x.fr", intro="{message}",
                                 button="Voir"))
    db.session.commit()
    note(users[E], "loan.validated", loan=resa)
    mailer.deliver_pending()
    assert smtp.sent[0]["Subject"].endswith("Ligne 1 Bcc: pirate@x.fr") and smtp.sent[0]["Bcc"] is None


def test_emails_page(client, as_role, users):
    as_role(A)
    r = client.get("/parametres/emails")
    assert r.status_code == 200
    assert "Prêt validé" in r.text and "Votre {type} n°{numero} est validée" in r.text
    assert "/parametres/emails" in client.get("/parametres").text


@pytest.mark.parametrize("role", [P, E])
def test_emails_pages_admin_only(client, as_role, users, role):
    as_role(role)
    for url in ("/parametres/emails", "/parametres/emails/loan.validated", "/parametres/emails/loan.validated/apercu"):
        assert client.get(url).status_code == 403
    assert client.post("/parametres/emails/loan.validated", data={"subject": "x", "intro": "x", "button": "x"}
                       ).status_code == 403
    assert db.session.get(EmailTemplate, "loan.validated") is None


def test_unknown_kind_404(client, as_role, users):
    as_role(A)
    assert client.get("/parametres/emails/nope").status_code == 404
    assert client.get("/parametres/emails/nope/apercu").status_code == 404
    assert client.post("/parametres/emails/nope/reinitialiser").status_code == 404


def test_edit_email(client, as_role, users):
    as_role(A)
    r = client.get("/parametres/emails/loan.overdue")
    assert r.status_code == 200 and "{emprunteur}" in r.text and "Matériel en retard" in r.text
    r = client.post("/parametres/emails/loan.overdue", data={
        "subject": "  Retard   de {emprunteur} ", "intro": "Merci de rapporter le matériel.\r\n\r\n{message}",
        "button": "Voir"}, follow_redirects=True)
    assert "Email enregistré." in r.text
    custom = db.session.get(EmailTemplate, "loan.overdue")
    assert custom.subject == "Retard de {emprunteur}"
    assert custom.intro == "Merci de rapporter le matériel.\n\n{message}"
    assert "Personnalisé" in client.get("/parametres/emails").text
    # Seconde modification : même ligne mise à jour.
    client.post("/parametres/emails/loan.overdue", data={"subject": "Retard", "intro": "{message}", "button": "Voir"})
    assert db.session.scalar(db.select(db.func.count(EmailTemplate.kind))) == 1
    assert db.session.get(EmailTemplate, "loan.overdue").subject == "Retard"


def test_edit_email_rejects_unknown_variables(client, as_role, users):
    as_role(A)
    r = client.post("/parametres/emails/item.low_stock", data={
        "subject": "Stock de {objet} pour {emprunteur}", "intro": "{message}", "button": "Voir"})
    assert r.status_code == 200 and "Variable(s) inconnue(s) : {emprunteur}." in r.text
    assert db.session.get(EmailTemplate, "item.low_stock") is None


def test_edit_email_requires_fields(client, as_role, users):
    as_role(A)
    client.post("/parametres/emails/item.low_stock", data={"subject": "", "intro": "{message}", "button": "x"})
    client.post("/parametres/emails/item.low_stock", data={"subject": "x" * 201, "intro": "x", "button": "x"})
    assert db.session.get(EmailTemplate, "item.low_stock") is None


def test_reset_email(client, as_role, users):
    as_role(A)
    db.session.add(EmailTemplate(kind="loan.validated", subject="Perso", intro="x", button="x"))
    db.session.commit()
    r = client.post("/parametres/emails/loan.validated/reinitialiser", follow_redirects=True)
    assert "Textes par défaut rétablis." in r.text
    assert db.session.get(EmailTemplate, "loan.validated") is None
    assert mailer.texts("loan.validated")[0] == "Votre {type} n°{numero} est validée"


def test_preview(client, as_role, users, resa):
    as_role(A)
    db.session.add(EmailTemplate(kind="loan.validated", subject="Aperçu {numero}", intro="{message}", button="x"))
    db.session.commit()
    before = db.session.scalar(db.select(db.func.count(Notification.id)))
    r = client.get("/parametres/emails/loan.validated/apercu")
    assert r.status_code == 200 and r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert f"Aperçu {resa.id}" in r.text and "Concert de Noël" in r.text and "Bonjour Admin," in r.text
    assert db.session.scalar(db.select(db.func.count(Notification.id))) == before  # rien d'enregistré


def test_preview_uses_last_notification(client, as_role, users, resa):
    note(users[E], "loan.validated", loan=resa, message="Message réel de la notification.")
    as_role(A)
    assert "Message réel de la notification." in client.get("/parametres/emails/loan.validated/apercu").text


def test_preview_on_empty_inventory(client, as_role, users):
    as_role(A)
    for kind in ("loan.validated", "item.low_stock"):
        r = client.get(f"/parametres/emails/{kind}/apercu")
        assert r.status_code == 200 and "(texte de la notification)" in r.text


def test_preview_item_kind(client, as_role, users, gear):
    as_role(A)
    assert "OHN-0003 Anches" in client.get("/parametres/emails/item.low_stock/apercu").text


def test_other_pages_still_deny_framing(client, as_role, users):
    as_role(A)
    assert client.get("/parametres/emails").headers["X-Frame-Options"] == "DENY"


def test_edit_logged(client, as_role, users):
    as_role(A)
    client.post("/parametres/emails/loan.validated", data={"subject": "x", "intro": "x", "button": "x"})
    assert db.session.scalar(db.select(Activity.message).where(Activity.action == "settings.email")) \
        == "Email « Prêt validé » personnalisé"

