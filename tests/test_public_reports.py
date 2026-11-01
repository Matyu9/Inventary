"""QR public : une personne sans compte signale un problème ou un objet trouvé ; l'équipe traite le signalement."""
import re
from datetime import datetime

import pytest

from app.extensions import db
from app.models import (Category, Item, Kit, Loan, LoanLine, LoanStatus, LoanType, Location, Notification,
                        PublicReport, Role)
from app.services import mailer, public_reports, trash

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def gear(app):
    sax = Item(code="OHN-0001", name="Saxophone alto", category=Category(name="Bois"), serial_number="SN-SECRET-42",
               estimated_value=1234, location=Location(name="Armoire du fond"))
    kit = Kit(name="Sono")
    db.session.add_all([sax, kit])
    db.session.commit()
    return {"sax": sax, "kit": kit}


def send(client, obj, kind="probleme", message="Le tampon du sol fuit", ip="10.0.0.1", **extra):
    return client.post(f"/q/{obj.qr_token}/signaler", data={"kind": kind, "message": message, **extra},
                       environ_base={"REMOTE_ADDR": ip})


def reports():
    return db.session.scalars(db.select(PublicReport).order_by(PublicReport.id)).all()


def recipients(kind=None):
    query = db.select(Notification.user_id)
    if kind:
        query = query.where(Notification.kind == kind)
    return set(db.session.scalars(query))


def lend(users, **line):
    loan = Loan(type=LoanType.GARDE, status=LoanStatus.EN_COURS, borrower=users[E], guarantor=users[P],
                requested_by=users[P], start=datetime(2026, 9, 1))
    loan.lines.append(LoanLine(**line))
    db.session.add(loan)
    db.session.commit()
    return loan


# ---------------------------------------------------------------------------
# Page publique
# ---------------------------------------------------------------------------

def test_anonymous_sees_public_page(client, gear):
    r = client.get(f"/q/{gear['sax'].qr_token}")
    assert r.status_code == 200
    page = r.text
    assert "Saxophone alto" in page and "OHN-0001" in page and "Mon association" in page
    assert "Signaler un problème" in page and "trouvé cet objet" in page
    assert f"/connexion?next=/q/{gear['sax'].qr_token}" in page
    for secret in ("SN-SECRET-42", "1234", "Armoire du fond", "Bois", "/objets/"):
        assert secret not in page, secret


def test_public_page_for_kit(client, gear):
    r = client.get(f"/q/{gear['kit'].qr_token}")
    assert r.status_code == 200 and "Kit Sono" in r.text


def test_public_page_hides_current_borrower(client, gear, users):
    lend(users, item=gear["sax"])
    page = client.get(f"/q/{gear['sax'].qr_token}").text
    assert users[E].last_name not in page and "prêté" not in page


def test_unknown_or_trashed_token_returns_404(client, gear, users):
    assert client.get("/q/inconnu").status_code == 404
    assert client.post("/q/inconnu/signaler", data={"kind": "probleme", "message": "x"}).status_code == 404
    token = gear["sax"].qr_token
    trash.trash_item(gear["sax"], users[A])
    db.session.commit()
    assert client.get(f"/q/{token}").status_code == 404
    assert client.post(f"/q/{token}/signaler", data={"kind": "probleme", "message": "x"}).status_code == 404


def test_public_page_works_with_csrf(app, gear):
    """Un visiteur sans compte doit pouvoir envoyer le formulaire, jeton CSRF compris."""
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    page = client.get(f"/q/{gear['sax'].qr_token}").text
    token = page.split('name="csrf_token" value="')[1].split('"')[0]
    assert client.post(f"/q/{gear['sax'].qr_token}/signaler",
                       data={"kind": "probleme", "message": "x"}).status_code == 400
    r = client.post(f"/q/{gear['sax'].qr_token}/signaler",
                    data={"kind": "probleme", "message": "Clé cassée", "csrf_token": token})
    assert r.status_code == 302 and len(reports()) == 1


# ---------------------------------------------------------------------------
# Envoi d'un signalement
# ---------------------------------------------------------------------------

def test_anonymous_problem_report(client, gear, users):
    r = send(client, gear["sax"], name="Camille", contact="06 00 00 00 00")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/q/{gear['sax'].qr_token}")
    assert "bien été transmis" in client.get(r.headers["Location"]).text
    (report,) = reports()
    assert report.item_id == gear["sax"].id and report.kind == PublicReport.PROBLEME
    assert report.name == "Camille" and report.contact == "06 00 00 00 00" and report.reported_by_id is None
    # Admins et profs (droit « Traiter les signalements ») sont prévenus, pas les emprunteurs.
    assert recipients("public.problem") == {users[A].id, users[P].id}
    notification = db.session.scalar(db.select(Notification).where(Notification.user_id == users[A].id))
    assert notification.link == "/signalements" and notification.report_id == report.id
    assert "Camille" in notification.message and "tampon" in notification.message


def test_found_item_notifies_borrower_and_guarantor(client, gear, users):
    other_prof = make_user(P, email="autre.prof@ohn.test")
    lend(users, item=gear["sax"])
    loan = db.session.scalar(db.select(Loan))
    send(client, gear["sax"], kind="trouve", message="Oublié dans le bus 12")
    assert recipients("public.found") == {users[A].id, users[P].id, other_prof.id}
    # L'emprunteur n'a pas accès aux signalements : sa notification mène à son prêt.
    assert recipients("public.found_loan") == {users[E].id}
    notification = db.session.scalar(db.select(Notification).where(Notification.user_id == users[E].id))
    assert notification.link == f"/prets/{loan.id}" and "retrouvé" in notification.message


def test_found_item_inside_lent_kit_notifies_borrower(client, gear, users):
    gear["sax"].kit = gear["kit"]
    db.session.commit()
    lend(users, kit=gear["kit"])
    send(client, gear["sax"], kind="trouve", message="Sur un banc")
    assert users[E].id in recipients("public.found_loan")


def test_problem_report_does_not_notify_borrower(client, gear, users):
    lend(users, item=gear["sax"])
    send(client, gear["sax"])
    assert users[E].id not in recipients()


def test_report_on_kit(client, gear, users):
    send(client, gear["kit"], message="Il manque un câble")
    (report,) = reports()
    assert report.kit_id == gear["kit"].id and report.target_label == "Kit Sono"


@pytest.mark.parametrize("data", [{"kind": "probleme", "message": ""}, {"kind": "piratage", "message": "x"},
                                  {"kind": "probleme", "message": "x" * 2001}])
def test_invalid_report_refused(client, gear, data):
    r = client.post(f"/q/{gear['sax'].qr_token}/signaler", data=data)
    assert r.status_code == 400 and reports() == []


def test_honeypot_silently_ignored(client, gear):
    r = send(client, gear["sax"], website="http://spam.example")
    assert r.status_code == 302 and reports() == []
    assert "bien été transmis" in client.get(r.headers["Location"]).text


def test_throttled_per_ip(client, gear):
    for _ in range(5):
        assert send(client, gear["sax"]).status_code == 302
    r = send(client, gear["sax"])
    assert r.status_code == 429 and "Trop de messages" in r.text
    assert len(reports()) == 5
    assert send(client, gear["sax"], ip="10.0.0.2").status_code == 302  # autre personne


def test_throttled_per_object(client, gear):
    for n in range(10):
        assert send(client, gear["sax"], ip=f"10.0.1.{n}").status_code == 302
    assert send(client, gear["sax"], ip="10.0.2.1").status_code == 429
    assert send(client, gear["kit"], ip="10.0.2.1").status_code == 302


def test_logged_in_borrower_reports(client, as_role, gear, users):
    borrower = as_role(E)
    r = client.get(f"/q/{gear['sax'].qr_token}")
    assert "Signaler un problème" in r.text and "Votre nom" not in r.text
    send(client, gear["sax"])
    (report,) = reports()
    assert report.reported_by_id == borrower.id and report.name == borrower.full_name


def test_staff_still_redirected_to_item(client, as_role, gear):
    as_role(P)
    r = client.get(f"/q/{gear['sax'].qr_token}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/objets/{gear['sax'].id}")


def test_report_email(client, gear, users, app, monkeypatch):
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="inventaire@ohn.test")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject, text)))
    send(client, gear["sax"], kind="trouve", message="Au gymnase", name="Camille", contact="cam@ex.fr")
    to_admin = [s for s in sent if s[0] == users[A].email]
    assert to_admin and "Objet trouvé : OHN-0001 Saxophone alto" in to_admin[0][1]
    assert "Au gymnase" in to_admin[0][2] and "cam@ex.fr" in to_admin[0][2]


# ---------------------------------------------------------------------------
# Traitement
# ---------------------------------------------------------------------------

def test_handle_and_reopen(client, as_role, gear):
    send(client, gear["sax"], name="Camille")
    (report,) = reports()
    admin = as_role(A)
    page = client.get("/signalements").text
    assert "À traiter (1)" in page and "Le tampon du sol fuit" in page and "Camille" in page
    assert "Enregistrer un entretien" in page
    r = client.post(f"/signalements/{report.id}/traiter", data={"note": "Tampon changé"}, follow_redirects=True)
    assert "marqué comme traité" in r.text and "Aucun signalement à traiter" in r.text
    db.session.expire_all()
    assert report.handled_by_id == admin.id and report.handled_note == "Tampon changé"
    page = client.get("/signalements?vue=traites").text
    assert "Tampon changé" in page and "Rouvrir" in page
    client.post(f"/signalements/{report.id}/rouvrir")
    db.session.expire_all()
    assert report.handled_at is None and report.handled_note is None


def test_nav_badge_and_item_banner(client, as_role, gear):
    send(client, gear["sax"])
    as_role(P)
    page = client.get("/").text
    assert "/signalements" in page and 'aria-label="1 à traiter"' in page
    assert "1 signalement à traiter" in client.get(f"/objets/{gear['sax'].id}").text


@pytest.mark.parametrize("role", [E])
def test_borrower_cannot_handle(client, as_role, gear, role):
    send(client, gear["sax"])
    (report,) = reports()
    as_role(role)
    assert client.get("/signalements").status_code == 403
    assert client.post(f"/signalements/{report.id}/traiter").status_code == 403
    assert "/signalements" not in client.get("/").text


def test_role_without_right_sees_no_badge(client, gear):
    db.session.add(Role(key="benevole", name="Bénévole", permissions=["inventory.view"]))
    db.session.commit()
    send(client, gear["sax"])
    login(client, make_user("benevole", email="benevole@ohn.test"))
    assert "/signalements" not in client.get("/").text
    assert "signalement à traiter" not in client.get(f"/objets/{gear['sax'].id}").text


def test_reports_of_trashed_item_hidden(client, as_role, gear, users):
    send(client, gear["sax"])
    send(client, gear["kit"])
    item_report, _ = reports()
    trash.trash_item(gear["sax"], users[A])
    db.session.commit()
    assert public_reports.pending_count() == 1
    as_role(A)
    page = client.get("/signalements").text
    assert "À traiter (1)" in page and "Saxophone" not in page
    assert client.post(f"/signalements/{item_report.id}/traiter").status_code == 404


def test_unknown_report_returns_404(client, as_role):
    as_role(A)
    assert client.post("/signalements/999/traiter").status_code == 404


def test_email_previews_for_report_kinds(client, as_role, gear):
    as_role(A)
    for kind in ("public.problem", "public.found"):
        r = client.get(f"/parametres/emails/{kind}/apercu")
        assert r.status_code == 200 and "Saxophone alto" in r.text and "Camille" in r.text
    send(client, gear["sax"], message="Vrai message")
    assert "Vrai message" in client.get("/parametres/emails/public.problem/apercu").text


def test_honeypot_hidden_even_without_stylesheet(client, gear):
    """Feuille de style ancienne (cache du téléphone) : le champ piège ne doit pas apparaître pour autant."""
    page = client.get(f"/q/{gear['sax'].qr_token}").text
    blocks = re.findall(r'<div class="hp"[^>]*>', page)
    assert blocks and all(re.search(r"\shidden[\s>]", block) for block in blocks)


def test_static_urls_are_versioned(client, gear):
    page = client.get(f"/q/{gear['sax'].qr_token}").text
    assert re.search(r'/static/css/app\.css\?v=\d+', page)
    assert client.get(re.search(r'/static/css/app\.css\?v=\d+', page).group(0)).status_code == 200


def test_borrower_link_opens_and_email_points_to_loan(client, gear, users, app, monkeypatch):
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="inventaire@ohn.test")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject, text)))
    loan = lend(users, item=gear["sax"])
    send(client, gear["sax"], kind="trouve", message="Au gymnase")
    mail = next(s for s in sent if s[0] == users[E].email)
    assert "Votre matériel a été retrouvé : OHN-0001 Saxophone alto" in mail[1]
    assert f"/prets/{loan.id}" in mail[2] and "/signalements" not in mail[2]
    login(client, users[E])
    assert client.get(f"/prets/{loan.id}").status_code == 200


def test_contact_rendered_as_link(client, as_role, gear):
    send(client, gear["sax"], contact="06 12 34 56 78")
    send(client, gear["kit"], contact="cam@ex.fr", ip="10.0.0.9")
    send(client, gear["kit"], contact="<b>gras</b>", ip="10.0.0.8")
    as_role(A)
    page = client.get("/signalements").text
    assert 'href="tel:0612345678"' in page and 'href="mailto:cam@ex.fr"' in page
    assert "<b>gras</b>" not in page and "&lt;b&gt;gras&lt;/b&gt;" in page


def test_email_preview_found_loan(client, as_role, gear):
    as_role(A)
    r = client.get("/parametres/emails/public.found_loan/apercu")
    assert r.status_code == 200 and "Voir le prêt" in r.text
