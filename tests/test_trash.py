"""Corbeille : un objet ou un kit supprimé disparaît partout, se restaure, puis s'efface après 30 jours."""
from datetime import date, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.extensions import db
from app.models import (Activity, AuditScan, Category, CategoryType, InventoryAudit, Item, ItemStatus, Kit, Location,
                        Maintenance, Note, Reminder, Role, StockMovement, Tag, Tracking)
from app.services import reminders, reports, transfer, trash
from app.services.reminders import due_maintenances

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def stock(app):
    bois = Category(name="Bois")
    armoire = Location(name="Armoire")
    sax = Item(code="OHN-0001", name="Saxophone alto", category=bois, location=armoire, estimated_value=900)
    flute = Item(code="OHN-0002", name="Flûte", category=bois, location=armoire, estimated_value=300)
    kit = Kit(name="Valise concert", location=armoire)
    sax.kit = flute.kit = kit
    db.session.add_all([sax, flute, kit])
    db.session.commit()
    return {"bois": bois, "armoire": armoire, "sax": sax, "flute": flute, "kit": kit}


def trash_item(client, item):
    return client.post(f"/objets/{item.id}/supprimer", follow_redirects=True)


def visible_items():
    return db.session.scalars(db.select(Item.code).order_by(Item.code)).all()


# ---------------------------------------------------------------------------
# Mise à la corbeille
# ---------------------------------------------------------------------------

def test_item_goes_to_trash(client, as_role, stock):
    admin = as_role(A)
    r = trash_item(client, stock["sax"])
    assert "mis à la corbeille" in r.text
    db.session.expire_all()
    sax = db.session.scalar(db.select(Item).where(Item.code == "OHN-0001").execution_options(include_deleted=True))
    assert sax.deleted_at is not None and sax.deleted_by_id == admin.id
    assert visible_items() == ["OHN-0002"]
    assert db.session.scalar(db.select(Activity.action).order_by(Activity.id.desc())) == "item.trash"


def test_trashed_item_hidden_everywhere(client, as_role, stock):
    as_role(A)
    sax = stock["sax"]
    token, sax_id = sax.qr_token, sax.id
    trash_item(client, sax)
    assert client.get(f"/objets/{sax_id}").status_code == 404
    assert "OHN-0001" not in client.get("/objets").text
    assert "OHN-0001" not in client.get("/objets?q=saxophone").text
    assert "OHN-0001" not in client.get(f"/lieux/{stock['armoire'].id}").text
    assert "OHN-0001" not in client.get(f"/kits/{stock['kit'].id}").text
    assert "OHN-0001" not in client.get("/export/inventaire.csv").text
    assert client.get(f"/q/{token}").status_code == 404
    r = client.get("/scan/resoudre?code=OHN-0001", follow_redirects=True)
    assert "Aucun objet" in r.text
    assert client.get(f"/objets/{sax_id}/qr.svg").status_code == 404


def test_trashed_item_left_out_of_reports(app, stock, users):
    before = sum(row["value"] for row in reports.value_by_category())
    trash.trash_item(stock["sax"], users[A])
    db.session.commit()
    after = sum(row["value"] for row in reports.value_by_category())
    assert before - after == 900


def test_trashed_item_not_expected_in_audit(client, as_role, stock, users):
    audit = InventoryAudit(name="Rentrée", opened_by=users[A])
    db.session.add(audit)
    db.session.commit()
    as_role(A)
    assert "OHN-0001" in client.get(f"/inventaires/{audit.id}").text
    trash_item(client, stock["sax"])
    assert "OHN-0001" not in client.get(f"/inventaires/{audit.id}").text


def test_audit_scans_removed_with_trashed_item(app, stock, users):
    audit = InventoryAudit(name="Rentrée", opened_by=users[A])
    db.session.add(AuditScan(audit=audit, item=stock["sax"], result="trouve"))
    db.session.commit()
    trash.trash_item(stock["sax"], users[A])
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(AuditScan.id))) == 0


@pytest.mark.parametrize("status", [ItemStatus.EN_GARDE, ItemStatus.SORTI])
def test_lent_item_cannot_be_trashed(client, as_role, stock, status):
    stock["sax"].status = status
    db.session.commit()
    as_role(A)
    r = trash_item(client, stock["sax"])
    assert "actuellement prêté" in r.text
    assert "OHN-0001" in visible_items()


def test_trash_requires_delete_right(client, as_role, stock):
    as_role(P)
    assert client.post(f"/objets/{stock['sax'].id}/supprimer").status_code == 403
    assert "OHN-0001" in visible_items()


def test_dependent_rows_of_trashed_item_do_not_break_pages(client, as_role, stock, users):
    """Entretiens, rappels et mouvements de stock d'un objet en corbeille : ni affichés, ni en erreur."""
    conso = Item(code="OHN-0003", name="Anches", category=Category(name="Conso", type=CategoryType.CONSOMMABLE),
                 tracking=Tracking.QUANTITE, quantity=3, low_stock_threshold=5)
    db.session.add_all([
        conso,
        Maintenance(item=stock["sax"], kind="révision", date=date.today(), next_due=date.today() + timedelta(days=2)),
        Reminder(item=stock["sax"], due_date=date.today(), message="Garantie"),
        StockMovement(item=conso, delta=-1, reason="distribution"),
    ])
    db.session.commit()
    as_role(A)
    trash_item(client, stock["sax"])
    trash_item(client, conso)
    assert due_maintenances(date.today() + timedelta(days=30)) == []
    for url in ("/", "/consommables", "/rapports",
                f"/calendrier/evenements?start={date.today()}&end={date.today() + timedelta(days=30)}"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert "Saxophone alto" not in r.text and "Anches" not in r.text, url
    assert "0 notification" in reminders.run()


def test_code_of_trashed_item_stays_reserved(app, stock, users):
    trash.trash_item(stock["flute"], users[A])
    db.session.commit()
    assert Item.next_code("OHN") == "OHN-0003"


def test_import_refuses_code_of_trashed_item(app, stock, users):
    trash.trash_item(stock["sax"], users[A])
    db.session.commit()
    created, updated, errors = transfer.run_import(
        [["OHN-0001", "Sax ténor", "Bois"]], transfer.guess_mapping(["Code", "Désignation", "Catégorie"]),
        users[A], prefix="OHN")
    assert (created, updated) == (0, 0)
    assert "corbeille" in errors[0][1]


def test_category_of_trashed_item_cannot_be_deleted(client, as_role, stock):
    as_role(A)
    for item in (stock["sax"], stock["flute"]):
        trash_item(client, item)
    r = client.post(f"/categories/{stock['bois'].id}/supprimer", follow_redirects=True)
    assert "corbeille comprise" in r.text
    assert db.session.get(Category, stock["bois"].id) is not None


# ---------------------------------------------------------------------------
# Page Corbeille et restauration
# ---------------------------------------------------------------------------

def test_trash_page_lists_items_and_kits(client, as_role, stock):
    as_role(A)
    assert "La corbeille est vide" in client.get("/corbeille").text
    trash_item(client, stock["sax"])
    client.post(f"/kits/{stock['kit'].id}/supprimer")
    page = client.get("/corbeille").text
    assert "OHN-0001" in page and "Valise concert" in page and "30 jours" in page
    assert "Admin Test" in page


@pytest.mark.parametrize("role", [P, E])
def test_trash_page_forbidden(client, as_role, stock, role):
    as_role(role)
    assert client.get("/corbeille").status_code == 403
    assert client.post(f"/corbeille/objets/{stock['sax'].id}/restaurer").status_code == 403


def test_trash_links_shown_to_deleters_only(client, as_role, stock):
    as_role(A)
    assert "/corbeille" in client.get("/objets").text and "/corbeille" in client.get("/kits").text
    as_role(P)
    assert "/corbeille" not in client.get("/objets").text


def test_restore_item(client, as_role, stock):
    as_role(A)
    trash_item(client, stock["sax"])
    r = client.post(f"/corbeille/objets/{stock['sax'].id}/restaurer", follow_redirects=True)
    assert "restauré" in r.text and "Saxophone alto" in r.text
    assert visible_items() == ["OHN-0001", "OHN-0002"]
    db.session.expire_all()
    assert stock["sax"].kit == stock["kit"]  # toujours dans son kit
    assert "item.untrash" in db.session.scalars(db.select(Activity.action)).all()


def test_restore_or_purge_unknown_returns_404(client, as_role, stock):
    as_role(A)
    assert client.post(f"/corbeille/objets/{stock['sax'].id}/restaurer").status_code == 404  # pas à la corbeille
    assert client.post("/corbeille/objets/999/supprimer").status_code == 404
    assert client.post("/corbeille/photos/1/restaurer").status_code == 404


def test_trash_kit_detaches_items_and_restores_them(client, as_role, stock, users):
    as_role(A)
    kit, sax, flute = stock["kit"], stock["sax"], stock["flute"]
    client.post(f"/kits/{kit.id}/supprimer")
    db.session.expire_all()
    assert sax.kit is None and flute.kit is None
    assert sorted(kit.trashed_item_ids) == sorted([sax.id, flute.id])
    client.get("/kits")  # consomme le message flash, qui cite le nom du kit
    assert "Valise concert" not in client.get("/kits").text
    # Entre-temps, la flûte rejoint un autre kit.
    other = Kit(name="Pupitre bois")
    flute.kit = other
    db.session.add(other)
    db.session.commit()
    r = client.post(f"/corbeille/kits/{kit.id}/restaurer", follow_redirects=True)
    assert "1 objet remis dedans" in r.text and "1 entré entre-temps" in r.text
    db.session.expire_all()
    assert sax.kit == kit and flute.kit == other
    assert kit.trashed_item_ids is None


def test_restored_kit_skips_trashed_items(app, stock, users):
    kit, sax = stock["kit"], stock["sax"]
    trash.trash_kit(kit, users[A])
    trash.trash_item(sax, users[A])
    db.session.commit()
    back, missing = trash.restore_kit(kit, users[A])
    assert (back, missing) == (1, 1)


# ---------------------------------------------------------------------------
# Suppression définitive
# ---------------------------------------------------------------------------

def test_purge_item_removes_everything(client, as_role, stock, users):
    db.session.add(Note(item=stock["sax"], author=users[A], text="Tampon à changer"))
    stock["sax"].tags = [Tag(name="soliste")]
    db.session.commit()
    as_role(A)
    trash_item(client, stock["sax"])
    r = client.post(f"/corbeille/objets/{stock['sax'].id}/supprimer", follow_redirects=True)
    assert "supprimé définitivement" in r.text
    everything = db.select(db.func.count(Item.id)).execution_options(include_deleted=True)
    assert db.session.scalar(everything) == 1
    assert db.session.scalar(db.select(db.func.count(Note.id))) == 0
    assert db.session.scalar(db.select(db.func.count(Tag.id))) == 0
    assert "item.purge" in db.session.scalars(db.select(Activity.action)).all()


def test_purge_kit(client, as_role, stock):
    as_role(A)
    client.post(f"/kits/{stock['kit'].id}/supprimer")
    client.post(f"/corbeille/kits/{stock['kit'].id}/supprimer")
    assert db.session.scalar(db.select(db.func.count(Kit.id)).execution_options(include_deleted=True)) == 0
    assert visible_items() == ["OHN-0001", "OHN-0002"]


def test_empty_trash_is_admin_only(client, app, as_role, stock):
    db.session.add(Role(key="regie", name="Régie", permissions=["inventory.view", "item.delete"]))
    db.session.commit()
    as_role(A)
    trash_item(client, stock["sax"])
    client.post("/deconnexion")
    login(client, make_user("regie", email="regie@ohn.test"))
    assert client.get("/corbeille").status_code == 200
    assert "Vider la corbeille" not in client.get("/corbeille").text
    assert client.post("/corbeille/vider").status_code == 403
    as_role(A)
    assert "Vider la corbeille" in client.get("/corbeille").text
    client.post(f"/kits/{stock['kit'].id}/supprimer")
    r = client.post("/corbeille/vider", follow_redirects=True)
    assert "2 éléments supprimés" in r.text
    assert trash.count() == 0


def test_purge_expired_after_30_days(app, stock, users):
    trash.trash_item(stock["sax"], users[A])
    trash.trash_kit(stock["kit"], users[A])
    db.session.commit()
    now = datetime.now()
    assert trash.purge_expired(now + timedelta(days=29)) == 0
    assert trash.count() == 2
    assert trash.purge_expired(now + timedelta(days=31)) == 2
    db.session.commit()
    assert trash.count() == 0


def test_reminders_run_purges_expired(app, stock, users):
    trash.trash_item(stock["sax"], users[A])
    stock["sax"].deleted_at = datetime.now() - timedelta(days=40)
    db.session.commit()
    assert "1 élément(s) de la corbeille effacé(s)" in reminders.run()
    assert trash.count() == 0


def test_days_left(app, stock, users):
    trash.trash_item(stock["sax"], users[A])
    now = datetime.now()
    assert trash.days_left(stock["sax"], now) == 30
    assert trash.days_left(stock["sax"], now + timedelta(days=12)) == 18
    assert trash.days_left(stock["sax"], now + timedelta(days=45)) == 0


def test_deleted_by_cleared_when_person_merged(app, stock, users):
    from app.services import people
    dup = make_user(P, email="doublon@ohn.test")
    trash.trash_item(stock["sax"], dup)
    db.session.commit()
    people.merge(dup, users[P])
    db.session.commit()
    sax = db.session.scalar(db.select(Item).where(Item.code == "OHN-0001").execution_options(include_deleted=True))
    assert sax.deleted_by_id == users[P].id


# ---------------------------------------------------------------------------
# Propriété : inventaire + corbeille = tout le matériel
# ---------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(st.lists(st.tuples(st.sampled_from(["trash", "restore", "purge"]), st.integers(0, 4)), max_size=25))
def test_visible_plus_trashed_is_constant(app, users, ops):
    db.session.execute(db.delete(Item).execution_options(include_deleted=True))
    cat = db.session.scalar(db.select(Category)) or Category(name="Divers")
    items = [Item(code=f"P-{i}", name=f"Objet {i}", category=cat) for i in range(5)]
    db.session.add_all(items)
    db.session.commit()
    total = 5
    for op, index in ops:
        item = items[index]
        if db.inspect(item).deleted or db.inspect(item).detached:
            continue
        if op == "trash" and item.deleted_at is None:
            trash.trash_item(item, users[A])
        elif op == "restore" and item.deleted_at is not None:
            trash.restore_item(item, users[A])
        elif op == "purge" and item.deleted_at is not None:
            trash.purge_item(item)
            total -= 1
        db.session.commit()
        visible = db.session.scalar(db.select(db.func.count(Item.id)))
        assert visible + len(trash.trashed_items()) == total
