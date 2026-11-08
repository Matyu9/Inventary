"""Liens entre fiches : personne, objet, kit, lieu, prêt — affichés seulement si la fiche est accessible."""
from datetime import datetime, timedelta

import pytest
from flask_login import login_user, logout_user

from app.extensions import db
from app.links import link_item, link_kit, link_loan, link_location, link_user
from app.models import (Activity, Category, InventoryAudit, Item, Kit, Loan, LoanLine, LoanStatus, LoanType,
                        Location, Role)
from app.services import ip_access

from .conftest import make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def world(app, users):
    local = Location(name="Local")
    armoire = Location(name="Armoire", parent=local)
    bois = Category(name="Bois")
    kit = Kit(name="Kit sono", location=armoire)
    sax = Item(code="OHN-0001", name="Saxophone", category=bois, location=armoire)
    enceinte = Item(code="OHN-0002", name="Enceinte", category=bois, kit=kit, location=armoire)
    start = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)
    loan = Loan(type=LoanType.RESERVATION, status=LoanStatus.VALIDE, borrower=users[E], guarantor=users[P],
                requested_by=users[P], validated_by=users[A], validated_at=datetime.now(), start=start,
                end=start + timedelta(days=1))
    loan.lines = [LoanLine(item=sax, quantity=1), LoanLine(kit=kit, quantity=1)]
    db.session.add_all([local, armoire, bois, kit, sax, enceinte, loan])
    db.session.commit()
    return dict(local=local, armoire=armoire, bois=bois, kit=kit, sax=sax, enceinte=enceinte, loan=loan)


def href(path):
    return f'href="{path}"'


# ---------------------------------------------------------------------------
# Fonctions de lien
# ---------------------------------------------------------------------------

@pytest.fixture
def as_user(app):
    """Contexte de requête avec la personne donnée connectée (pour appeler les fonctions directement)."""
    contexts = []

    def _as(user):
        ctx = app.test_request_context("/")
        ctx.push()
        contexts.append(ctx)
        if user is not None:
            login_user(user)
        else:
            logout_user()
    yield _as
    for ctx in reversed(contexts):
        ctx.pop()


def test_admin_gets_links_everywhere(as_user, users, world):
    as_user(users[A])
    assert str(link_user(users[E])) == f'<a href="/utilisateurs/{users[E].id}">{users[E].full_name}</a>'
    assert str(link_item(world["sax"])) == f'<a href="/objets/{world["sax"].id}">Saxophone</a>'
    assert str(link_kit(world["kit"])) == f'<a href="/kits/{world["kit"].id}">Kit sono</a>'
    assert str(link_location(world["armoire"])) == f'<a href="/lieux/{world["armoire"].id}">Local &gt; Armoire</a>'
    assert str(link_loan(world["loan"])) == f'<a href="/prets/{world["loan"].id}">n°{world["loan"].id}</a>'


def test_custom_text_and_class(as_user, users, world):
    as_user(users[A])
    assert str(link_item(world["sax"], "OHN-0001", css="small")) == (
        f'<a href="/objets/{world["sax"].id}" class="small">OHN-0001</a>')


def test_location_segments(as_user, users, world):
    as_user(users[A])
    local, armoire = world["local"], world["armoire"]
    assert str(link_location(armoire, segments=True)) == (
        f'<a href="/lieux/{local.id}">Local</a> &gt; <a href="/lieux/{armoire.id}">Armoire</a>')


def test_location_segments_survive_a_cycle(as_user, users, world):
    """Un parent circulaire abîmé en base ne fait pas boucler l'affichage."""
    as_user(users[A])
    world["local"].parent = world["armoire"]
    assert str(link_location(world["armoire"], segments=True)).count("<a ") == 2


def test_text_is_escaped(as_user, users, world):
    as_user(users[A])
    world["sax"].name = "<script>x</script>"
    users[E].first_name = "<b>"
    assert "<script>" not in str(link_item(world["sax"])) and "&lt;script&gt;" in str(link_item(world["sax"]))
    assert "<b>" not in str(link_user(users[E]))
    as_user(None)
    assert str(link_item(world["sax"])) == "&lt;script&gt;x&lt;/script&gt;"


def test_none_renders_nothing(as_user, users):
    as_user(users[A])
    for fn in (link_user, link_item, link_kit, link_location, link_loan):
        assert str(fn(None)) == ""


def test_borrower_gets_plain_text(as_user, users, world):
    """Un emprunteur n'a pas accès à l'inventaire ni aux fiches personnes : simple texte."""
    as_user(users[E])
    assert str(link_user(users[P])) == users[P].full_name
    assert str(link_item(world["sax"])) == "Saxophone"
    assert str(link_kit(world["kit"])) == "Kit sono"
    assert str(link_location(world["armoire"])) == "Local &gt; Armoire"
    assert str(link_location(world["armoire"], segments=True)) == "Local &gt; Armoire"


def test_borrower_links_only_own_loans(as_user, users, world):
    other = make_user(E, email="autre@ohn.test")
    as_user(users[E])
    assert "<a " in str(link_loan(world["loan"]))
    as_user(other)
    assert str(link_loan(world["loan"])) == f"n°{world['loan'].id}"


def test_anonymous_gets_plain_text(as_user, users, world):
    as_user(None)
    assert str(link_user(users[E])) == users[E].full_name
    assert str(link_loan(world["loan"])) == f"n°{world['loan'].id}"


def test_trashed_item_and_kit_not_linked(as_user, users, world):
    as_user(users[A])
    world["sax"].deleted_at = world["kit"].deleted_at = datetime.now()
    assert str(link_item(world["sax"])) == "Saxophone"
    assert str(link_kit(world["kit"])) == "Kit sono"


def test_user_link_hidden_outside_admin_ip_zone(app, users, world):
    """La fiche personne est dans la zone « Administration » : pas de lien depuis une adresse non autorisée."""
    ip_access.save("admin", "192.168.1.0/24")
    db.session.commit()
    for ip, linked in (("203.0.113.9", False), ("192.168.1.20", True)):
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
            login_user(users[A])
            assert ("<a " in str(link_user(users[E]))) is linked, ip
            assert "<a " in str(link_item(world["sax"]))  # le reste n'est pas concerné


def test_prof_links(as_user, users, world):
    """Le prof voit l'inventaire et crée des fiches emprunteurs : liens partout."""
    as_user(users[P])
    for fn, obj in ((link_user, users[E]), (link_item, world["sax"]), (link_kit, world["kit"]),
                    (link_location, world["armoire"]), (link_loan, world["loan"])):
        assert "<a " in str(fn(obj)), fn.__name__


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def test_loan_detail_links_people_and_equipment(client, as_role, users, world):
    as_role(A)
    html = client.get(f"/prets/{world['loan'].id}").text
    for path in (f"/utilisateurs/{users[E].id}", f"/utilisateurs/{users[P].id}", f"/utilisateurs/{users[A].id}",
                 f"/objets/{world['sax'].id}", f"/objets/{world['enceinte'].id}", f"/kits/{world['kit'].id}"):
        assert href(path) in html, path


def test_loan_detail_for_borrower_has_no_staff_links(client, as_role, users, world):
    as_role(E)
    html = client.get(f"/prets/{world['loan'].id}").text
    assert "/utilisateurs/" not in html and "/objets/" not in html and "/kits/" not in html
    assert users[P].full_name in html and "Saxophone" in html


def test_loan_detail_awaiting_guarantor_links_people(client, as_role, users, world):
    loan = world["loan"]
    loan.status = LoanStatus.DEMANDE
    loan.awaiting_guarantor = True
    db.session.commit()
    as_role(A)
    html = client.get(f"/prets/{loan.id}").text
    assert f'garant, <a href="/utilisateurs/{users[P].id}">' in html


def test_loan_list_links_borrower_guarantor_and_lines(client, as_role, users, world):
    as_role(A)
    html = client.get("/prets").text
    for path in (f"/prets/{world['loan'].id}", f"/utilisateurs/{users[E].id}", f"/utilisateurs/{users[P].id}",
                 f"/objets/{world['sax'].id}", f"/kits/{world['kit'].id}"):
        assert href(path) in html, path


def test_my_loans_for_borrower_links_loan_only(client, as_role, users, world):
    as_role(E)
    html = client.get("/mes-prets").text
    assert href(f"/prets/{world['loan'].id}") in html
    assert "/utilisateurs/" not in html and "/objets/" not in html


def test_item_detail_links_category_location_kit_and_history(client, as_role, users, world):
    db.session.add(Activity(user=users[P], action="item.edit", message="Modifié", item_id=world["enceinte"].id))
    db.session.commit()
    as_role(A)
    html = client.get(f"/objets/{world['enceinte'].id}").text
    assert href(f"/objets?category={world['bois'].id}") in html
    assert href(f"/lieux/{world['local'].id}") in html and href(f"/lieux/{world['armoire'].id}") in html
    assert href(f"/kits/{world['kit'].id}") in html
    assert href(f"/utilisateurs/{users[P].id}") in html


def test_item_list_links_category_and_location(client, as_role, world):
    as_role(P)
    html = client.get("/objets").text
    assert href(f"/objets?category={world['bois'].id}") in html and href(f"/lieux/{world['armoire'].id}") in html


def test_kit_pages_link_location(client, as_role, world):
    as_role(P)
    assert href(f"/lieux/{world['armoire'].id}") in client.get(f"/kits/{world['kit'].id}").text
    assert href(f"/lieux/{world['armoire'].id}") in client.get("/kits").text


def test_kit_detail_links_locking_loan(client, as_role, world):
    world["loan"].status = LoanStatus.EN_COURS
    db.session.commit()
    as_role(P)
    html = client.get(f"/kits/{world['kit'].id}").text
    assert f'<a href="/prets/{world["loan"].id}">Prêt n°{world["loan"].id}</a> en cours' in html


def test_location_detail_links_path_items_and_kits(client, as_role, world):
    as_role(P)
    html = client.get(f"/lieux/{world['armoire'].id}").text
    assert href(f"/lieux/{world['local'].id}") in html
    assert href(f"/kits/{world['kit'].id}") in html and href(f"/objets/{world['sax'].id}") in html


def test_user_detail_activity_links_resources(client, as_role, users, world):
    db.session.add(Activity(user=users[A], action="loan.validate", message="Prêt validé", loan_id=world["loan"].id,
                            target_user_id=users[E].id))
    db.session.commit()
    as_role(A)
    html = client.get(f"/utilisateurs/{users[E].id}").text
    assert href(f"/prets/{world['loan'].id}") in html and href(f"/utilisateurs/{users[A].id}") in html


def test_reports_link_borrower_and_category(client, as_role, users, world):
    loan = world["loan"]
    loan.type, loan.status, loan.end = LoanType.GARDE, LoanStatus.EN_COURS, None
    db.session.commit()
    as_role(A)
    html = client.get("/rapports").text
    assert href(f"/objets?category={world['bois'].id}") in html
    assert href(f"/utilisateurs/{users[E].id}") in html


def test_audit_pages_link_people_and_locations(client, as_role, users, world):
    audit = InventoryAudit(name="Inventaire 2026", opened_by=users[A])
    audit.locations = [world["armoire"]]
    db.session.add(audit)
    db.session.commit()
    as_role(A)
    html = client.get("/inventaires").text
    assert href(f"/utilisateurs/{users[A].id}") in html and href(f"/lieux/{world['armoire'].id}") in html
    html = client.get(f"/inventaires/{audit.id}").text
    assert href(f"/lieux/{world['armoire'].id}") in html and href(f"/objets/{world['sax'].id}") in html
