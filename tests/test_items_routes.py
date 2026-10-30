from io import BytesIO

import pytest
from PIL import Image

from app.extensions import db
from app.models import (Activity, Category, CategoryType, CustomField, Item, ItemStatus, Loan, LoanLine,
                        LoanType, Location, Note, Photo, Role, Tag)

from .conftest import make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def ref(app):
    bois = Category(name="Bois")
    cuivres = Category(name="Cuivres")
    conso = Category(name="Consommables", type=CategoryType.CONSOMMABLE)
    local = Location(name="Local")
    armoire = Location(name="Armoire", parent=local)
    salle = Location(name="Salle des fêtes")
    db.session.add_all([bois, cuivres, conso, local, armoire, salle])
    db.session.commit()
    return dict(bois=bois, cuivres=cuivres, conso=conso, local=local, armoire=armoire, salle=salle)


def new_item(category, code=None, **kw):
    item = Item(code=code or Item.next_code("OHN"), name=kw.pop("name", "Saxophone alto"), category=category, **kw)
    db.session.add(item)
    db.session.commit()
    return item


def form_data(ref, **overrides):
    data = {"name": "Clarinette Sib", "category_id": ref["bois"].id, "condition": "bon", "status": "disponible",
            "location_id": ref["armoire"].id, "tags": "", "tracking": "individuel", "quantity": "1",
            "consumption_mode": ""}
    data.update(overrides)
    return data


def png(name="p.png"):
    buf = BytesIO()
    Image.new("RGB", (50, 50), "blue").save(buf, "PNG")
    buf.seek(0)
    return buf, name


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["/objets", "/objets/nouveau", "/lieux", "/categories", "/champs-perso"])
def test_anonymous_redirected_to_login(client, url):
    r = client.get(url)
    assert r.status_code == 302 and "/connexion" in r.headers["Location"]


@pytest.mark.parametrize("url,allowed", [
    ("/objets", {A, P}),
    ("/objets/nouveau", {A, P}),
    ("/lieux", {A, P}),
    ("/lieux/nouveau", {A, P}),
    ("/categories", {A, P}),
    ("/champs-perso", {A}),
    ("/champs-perso/nouveau", {A}),
])
def test_page_access_by_role(client, as_role, ref, url, allowed):
    for role in Role.ALL:
        as_role(role)
        expected = 200 if role in allowed else 403
        assert client.get(url).status_code == expected, (url, role)


def test_item_pages_access_by_role(client, as_role, ref):
    item = new_item(ref["bois"])
    for role in Role.ALL:
        as_role(role)
        ok = role != E
        assert client.get(f"/objets/{item.id}").status_code == (200 if ok else 403)
        assert client.get(f"/objets/{item.id}/modifier").status_code == (200 if ok else 403)


def test_unknown_item_404(client, as_role, ref):
    as_role(A)
    assert client.get("/objets/999").status_code == 404
    assert client.get("/objets/999/modifier").status_code == 404


def test_borrower_cannot_post_anything(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(E)
    for url in ["/objets/nouveau", f"/objets/{item.id}/modifier", f"/objets/{item.id}/reformer",
                f"/objets/{item.id}/supprimer", f"/objets/{item.id}/notes", f"/objets/{item.id}/photos",
                "/objets/actions-groupees", "/lieux/nouveau", "/categories"]:
        assert client.post(url, data=form_data(ref)).status_code == 403, url


# ---------------------------------------------------------------------------
# Liste et recherche
# ---------------------------------------------------------------------------

@pytest.fixture
def catalog(ref):
    sax = new_item(ref["bois"], name="Saxophone alto", brand="Selmer", serial_number="SN-778",
                   location=ref["armoire"])
    trompette = new_item(ref["cuivres"], name="Trompette", condition="usé", location=ref["salle"])
    flute = new_item(ref["bois"], name="Flûte", status=ItemStatus.EN_ENTRETIEN, location=ref["local"])
    tag = Tag(name="étude")
    sax.tags.append(tag)
    field = CustomField(label="Tonalité", type="texte")
    db.session.add(field)
    db.session.flush()
    from app.models import CustomFieldValue
    trompette.custom_values.append(CustomFieldValue(field_id=field.id, value="Sib"))
    db.session.commit()
    return dict(sax=sax, trompette=trompette, flute=flute, tag=tag)


def codes(response):
    import re
    return set(re.findall(r">(OHN-\d{4})</a>", response.text))


@pytest.mark.parametrize("params,expected", [
    ({}, {"sax", "trompette", "flute"}),
    ({"q": "saxo"}, {"sax"}),
    ({"q": "selmer"}, {"sax"}),
    ({"q": "sn-778"}, {"sax"}),
    ({"q": "sib"}, {"trompette"}),  # champ perso
    ({"q": "ÉTUDE"}, {"sax"}),  # casse des accents
    ({"q": "flute"}, {"flute"}),  # accents ignorés
    ({"q": "OHN-000"}, {"sax", "trompette", "flute"}),
    ({"q": "%"}, set()),  # joker échappé
    ({"q": "_"}, set()),
    ({"q": "étude"}, {"sax"}),  # tag
    ({"status": "en entretien"}, {"flute"}),
    ({"condition": "usé"}, {"trompette"}),
    ({"q": "zzz"}, set()),
])
def test_search_filters(client, as_role, catalog, params, expected):
    as_role(P)
    r = client.get("/objets", query_string=params)
    assert codes(r) == {catalog[k].code for k in expected}


def test_filter_by_category_and_tag(client, as_role, ref, catalog):
    as_role(P)
    assert codes(client.get("/objets", query_string={"category": ref["cuivres"].id})) == {catalog["trompette"].code}
    assert codes(client.get("/objets", query_string={"tag": catalog["tag"].id})) == {catalog["sax"].code}


def test_filter_by_location_includes_sublocations(client, as_role, ref, catalog):
    as_role(P)
    r = client.get("/objets", query_string={"location": ref["local"].id})
    assert codes(r) == {catalog["sax"].code, catalog["flute"].code}


def test_invalid_filter_values_ignored(client, as_role, catalog):
    as_role(P)
    r = client.get("/objets", query_string={"category": "abc", "location": "", "page": "-3"})
    assert r.status_code == 200 and len(codes(r)) == 3


def test_htmx_returns_partial(client, as_role, catalog):
    as_role(P)
    r = client.get("/objets", query_string={"q": "sax"}, headers={"HX-Request": "true", "HX-Target": "item-results"})
    assert "<html" not in r.text and catalog["sax"].code in r.text


def test_pagination(client, as_role, ref):
    for _ in range(55):
        new_item(ref["bois"])
    as_role(P)
    first = client.get("/objets")
    assert "55 objets" in first.text and "Page 1 / 2" in first.text and len(codes(first)) == 50
    assert len(codes(client.get("/objets?page=2"))) == 5


def test_list_hides_bulk_actions_for_nobody_but_editors(client, as_role, catalog):
    as_role(P)
    assert "Appliquer à la sélection" in client.get("/objets").text


# ---------------------------------------------------------------------------
# Création
# ---------------------------------------------------------------------------

def test_prof_creates_item(client, as_role, ref):
    prof = as_role(P)
    r = client.post("/objets/nouveau", data=form_data(ref, tags="étude, Prêt élève, étude",
                                                       brand="Buffet", purchase_price="1200,50"))
    item = db.session.scalar(db.select(Item))
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/objets/{item.id}")
    assert item.code == "OHN-0001" and item.brand == "Buffet" and item.location == ref["armoire"]
    assert {t.name for t in item.tags} == {"étude", "Prêt élève"}
    assert str(item.purchase_price) == "1200.50"
    activity = db.session.scalar(db.select(Activity).where(Activity.action == "item.create"))
    assert activity.user_id == prof.id and activity.item_id == item.id


def test_create_and_create_another(client, as_role, ref):
    as_role(P)
    r = client.post("/objets/nouveau", data=dict(form_data(ref), then="new"))
    item = db.session.scalar(db.select(Item))
    assert r.status_code == 302
    assert r.headers["Location"] == f"/objets/nouveau?category_id={ref['bois'].id}&location_id={ref['armoire'].id}"
    r = client.get(r.headers["Location"])
    assert f"Objet {item.code} créé." in r.text
    assert f'<option selected value="{ref["bois"].id}">' in r.text
    assert f'<option selected value="{ref["armoire"].id}">' in r.text


def test_create_and_create_another_without_location(client, as_role, ref):
    as_role(P)
    r = client.post("/objets/nouveau", data=dict(form_data(ref), location_id="0", then="new"))
    assert r.headers["Location"] == f"/objets/nouveau?category_id={ref['bois'].id}"


def test_create_another_button_only_on_creation(client, as_role, ref):
    as_role(P)
    assert 'name="then" value="new"' in client.get("/objets/nouveau").text
    item = new_item(ref["bois"])
    assert 'name="then" value="new"' not in client.get(f"/objets/{item.id}/modifier").text


def test_create_another_ignored_on_validation_error(client, as_role, ref):
    as_role(P)
    r = client.post("/objets/nouveau", data=dict(form_data(ref), name="", then="new"))
    assert r.status_code == 200 and db.session.scalar(db.select(Item)) is None


def test_create_validation_errors(client, as_role, ref):
    as_role(P)
    r = client.post("/objets/nouveau", data=form_data(ref, name=""))
    assert r.status_code == 200 and "aria-invalid" in r.text
    assert db.session.scalar(db.select(db.func.count(Item.id))) == 0


@pytest.mark.parametrize("raw,expected", [("1 200,5", "1200.50"), ("1\u202f200,50", "1200.50"), ("99.9", "99.90")])
def test_french_amounts(client, as_role, ref, raw, expected):
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref, estimated_value=raw))
    assert str(db.session.scalar(db.select(Item)).estimated_value) == expected


def test_create_rejects_negative_price(client, as_role, ref):
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref, purchase_price="-5"))
    assert db.session.scalar(db.select(db.func.count(Item.id))) == 0


def test_create_rejects_unknown_category(client, as_role, ref):
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref, category_id="999"))
    assert db.session.scalar(db.select(db.func.count(Item.id))) == 0


def test_create_cannot_set_lent_status(client, as_role, ref):
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref, status=ItemStatus.EN_GARDE))
    assert db.session.scalar(db.select(db.func.count(Item.id))) == 0


def test_consumable_category_forces_quantity_tracking(client, as_role, ref):
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref, name="Anches n°3", category_id=ref["conso"].id,
                                                   quantity="20", unit="boîte", low_stock_threshold="5",
                                                   consumption_mode="consomme"))
    item = db.session.scalar(db.select(Item))
    assert item.is_quantity and item.quantity == 20 and item.unit == "boîte" and item.consumption_mode == "consomme"


def test_individual_item_quantity_is_one(client, as_role, ref):
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref, quantity="7"))
    assert db.session.scalar(db.select(Item)).quantity == 1


def test_codes_increment(client, as_role, ref):
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref))
    client.post("/objets/nouveau", data=form_data(ref))
    assert sorted(db.session.scalars(db.select(Item.code))) == ["OHN-0001", "OHN-0002"]


def test_create_prefills_from_query(client, as_role, ref):
    as_role(P)
    r = client.get("/objets/nouveau", query_string={"location_id": ref["salle"].id})
    assert f'<option selected value="{ref["salle"].id}">' in r.text


def test_create_with_custom_fields(client, as_role, ref):
    field = CustomField(label="Tonalité", type="liste", options=["Sib", "Mib"], required=True,
                        categories=[ref["cuivres"]])
    db.session.add(field)
    db.session.commit()
    as_role(P)
    r = client.post("/objets/nouveau", data=form_data(ref, category_id=ref["cuivres"].id))
    assert r.status_code == 200 and "Ce champ est obligatoire." in r.text
    r = client.post("/objets/nouveau", data=form_data(ref, category_id=ref["cuivres"].id, **{f"cf_{field.id}": "Ré"}))
    assert "Valeur invalide." in r.text
    r = client.post("/objets/nouveau", data=form_data(ref, category_id=ref["cuivres"].id, **{f"cf_{field.id}": "Sib"}))
    assert r.status_code == 302
    item = db.session.scalar(db.select(Item))
    assert item.custom_values[0].value == "Sib"
    assert "Tonalité" in client.get(f"/objets/{item.id}").text


def test_custom_field_of_other_category_ignored(client, as_role, ref):
    field = CustomField(label="Tonalité", type="texte", required=True, categories=[ref["cuivres"]])
    db.session.add(field)
    db.session.commit()
    as_role(P)
    r = client.post("/objets/nouveau", data=form_data(ref, **{f"cf_{field.id}": "Sib"}))
    assert r.status_code == 302
    assert db.session.scalar(db.select(Item)).custom_values == []


def test_custom_fields_partial(client, as_role, ref):
    db.session.add(CustomField(label="Tonalité", type="texte", categories=[ref["cuivres"]]))
    db.session.commit()
    as_role(P)
    assert "Tonalité" in client.get(f"/objets/champs?category_id={ref['cuivres'].id}").text
    assert "Tonalité" not in client.get(f"/objets/champs?category_id={ref['bois'].id}").text
    as_role(E)
    assert client.get(f"/objets/champs?category_id={ref['bois'].id}").status_code == 403


# ---------------------------------------------------------------------------
# Modification
# ---------------------------------------------------------------------------

def test_edit_logs_changes(client, as_role, ref):
    item = new_item(ref["bois"], name="Sax", location=ref["armoire"])
    as_role(P)
    r = client.post(f"/objets/{item.id}/modifier", data=form_data(
        ref, name="Saxophone ténor", location_id=ref["salle"].id, condition="usé", tags="soliste"))
    assert r.status_code == 302
    db.session.refresh(item)
    assert item.name == "Saxophone ténor" and item.location == ref["salle"] and item.condition == "usé"
    entry = db.session.scalar(db.select(Activity).where(Activity.action == "item.update"))
    assert entry.changes["name"] == ["Sax", "Saxophone ténor"]
    assert entry.changes["location_id"] == [ref["armoire"].id, ref["salle"].id]
    assert entry.changes["tags"] == ["", "soliste"]


def test_edit_without_changes_logs_nothing(client, as_role, ref):
    item = new_item(ref["bois"], name="Clarinette Sib", location=ref["armoire"])
    as_role(P)
    client.post(f"/objets/{item.id}/modifier", data=form_data(ref))
    assert db.session.scalar(db.select(db.func.count(Activity.id)).where(Activity.action == "item.update")) == 0


def test_edit_form_prefilled(client, as_role, ref):
    item = new_item(ref["bois"], name="Basson")
    item.tags = [Tag(name="rare")]
    db.session.commit()
    as_role(P)
    r = client.get(f"/objets/{item.id}/modifier")
    assert 'value="Basson"' in r.text and 'value="rare"' in r.text


def test_manual_status_change(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    client.post(f"/objets/{item.id}/modifier", data=form_data(ref, status=ItemStatus.EN_ENTRETIEN))
    db.session.refresh(item)
    assert item.status == ItemStatus.EN_ENTRETIEN


def test_lent_item_status_is_locked(client, as_role, ref):
    item = new_item(ref["bois"], status=ItemStatus.EN_GARDE)
    as_role(P)
    r = client.post(f"/objets/{item.id}/modifier", data={k: v for k, v in form_data(ref, name="Renommé").items()
                                                          if k != "status"})
    assert r.status_code == 302
    db.session.refresh(item)
    assert item.status == ItemStatus.EN_GARDE and item.name == "Renommé"
    r = client.post(f"/objets/{item.id}/modifier", data=form_data(ref, status=ItemStatus.DISPONIBLE))
    db.session.refresh(item)
    assert item.status == ItemStatus.EN_GARDE


def test_edit_keeps_quantity(client, as_role, ref):
    item = new_item(ref["conso"], tracking="quantite", quantity=12)
    as_role(P)
    client.post(f"/objets/{item.id}/modifier", data=form_data(ref, category_id=ref["conso"].id, quantity="999"))
    db.session.refresh(item)
    assert item.quantity == 12


def test_edit_removes_custom_value(client, as_role, ref):
    field = CustomField(label="Remarque", type="texte")
    db.session.add(field)
    db.session.commit()
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref, **{f"cf_{field.id}": "Fragile"}))
    item = db.session.scalar(db.select(Item))
    client.post(f"/objets/{item.id}/modifier", data=form_data(ref, **{f"cf_{field.id}": ""}))
    db.session.refresh(item)
    assert item.custom_values == []


# ---------------------------------------------------------------------------
# Réforme et suppression
# ---------------------------------------------------------------------------

def test_prof_cannot_retire_or_delete(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    assert client.post(f"/objets/{item.id}/reformer").status_code == 403
    assert client.post(f"/objets/{item.id}/supprimer").status_code == 403
    assert "Réformer" not in client.get(f"/objets/{item.id}").text


def test_admin_retires_and_restores(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(A)
    client.post(f"/objets/{item.id}/reformer")
    db.session.refresh(item)
    assert item.status == ItemStatus.REFORME
    client.post(f"/objets/{item.id}/reformer")
    db.session.refresh(item)
    assert item.status == ItemStatus.DISPONIBLE
    actions = db.session.scalars(db.select(Activity.action).order_by(Activity.id)).all()
    assert actions == ["item.retire", "item.restore"]


@pytest.mark.parametrize("status", [ItemStatus.EN_GARDE, ItemStatus.SORTI])
def test_cannot_retire_lent_item(client, as_role, ref, status):
    item = new_item(ref["bois"], status=status)
    as_role(A)
    r = client.post(f"/objets/{item.id}/reformer", follow_redirects=True)
    assert "Impossible de réformer" in r.text
    db.session.refresh(item)
    assert item.status == status


def test_admin_deletes_item_and_photos(client, as_role, app, ref):
    item = new_item(ref["bois"])
    as_role(A)
    client.post(f"/objets/{item.id}/photos", data={"photos": png()}, content_type="multipart/form-data")
    photo = db.session.scalar(db.select(Photo))
    files = [app.config["UPLOAD_DIR"] / photo.filename, app.config["UPLOAD_DIR"] / photo.thumbnail]
    assert all(f.exists() for f in files)
    client.post(f"/objets/{item.id}/notes", data={"text": "note"})
    r = client.post(f"/objets/{item.id}/supprimer")
    assert r.status_code == 302
    assert db.session.scalar(db.select(db.func.count(Item.id))) == 0
    assert db.session.scalar(db.select(db.func.count(Note.id))) == 0
    assert not any(f.exists() for f in files)


def test_delete_blocked_when_loan_history(client, as_role, ref, users):
    item = new_item(ref["bois"])
    from datetime import datetime
    loan = Loan(type=LoanType.GARDE, borrower=users[E], requested_by=users[P], start=datetime.now())
    loan.lines.append(LoanLine(item=item))
    db.session.add(loan)
    db.session.commit()
    as_role(A)
    r = client.post(f"/objets/{item.id}/supprimer", follow_redirects=True)
    assert "réformez-le" in r.text
    assert db.session.get(Item, item.id) is not None


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

def test_upload_multiple_photos(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    r = client.post(f"/objets/{item.id}/photos", data={"photos": [png("a.png"), png("b.png")], "caption": "Pavillon"},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "2 photo(s) ajoutée(s)" in r.text
    assert [p.caption for p in item.photos] == ["Pavillon", "Pavillon"]


def test_upload_invalid_photo(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    r = client.post(f"/objets/{item.id}/photos", data={"photos": (BytesIO(b"pas une image"), "x.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "pas une image valide" in r.text and item.photos == []


def test_upload_without_file(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    r = client.post(f"/objets/{item.id}/photos", data={}, content_type="multipart/form-data", follow_redirects=True)
    assert "Aucun fichier" in r.text


def test_photo_files_served_only_to_staff(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    client.post(f"/objets/{item.id}/photos", data={"photos": png()}, content_type="multipart/form-data")
    photo = db.session.scalar(db.select(Photo))
    url = f"/objets/fichiers/{photo.thumbnail}"
    r = client.get(url)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    as_role(E)
    assert client.get(url).status_code == 403
    client.post("/deconnexion")
    assert client.get(url).status_code == 302


def test_photo_path_traversal_blocked(client, as_role, ref):
    as_role(P)
    assert client.get("/objets/fichiers/../inventaire.db").status_code == 404
    assert client.get("/objets/fichiers/%2e%2e/%2e%2e/etc/passwd").status_code == 404


def test_delete_photo(client, as_role, app, ref):
    item = new_item(ref["bois"])
    as_role(P)
    client.post(f"/objets/{item.id}/photos", data={"photos": png()}, content_type="multipart/form-data")
    photo = db.session.scalar(db.select(Photo))
    path = app.config["UPLOAD_DIR"] / photo.filename
    client.post(f"/objets/photos/{photo.id}/supprimer")
    assert not path.exists() and db.session.scalar(db.select(db.func.count(Photo.id))) == 0


def test_thumbnail_shown_in_list(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    client.post(f"/objets/{item.id}/photos", data={"photos": png()}, content_type="multipart/form-data")
    assert 'class="thumb"' in client.get("/objets").text


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def test_add_note_htmx(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    r = client.post(f"/objets/{item.id}/notes", data={"text": "Tampon à changer"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Tampon à changer" in r.text and "<html" not in r.text


def test_add_note_without_htmx_redirects(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    r = client.post(f"/objets/{item.id}/notes", data={"text": "x"})
    assert r.status_code == 302 and r.headers["Location"].endswith("#notes")


def test_empty_note_ignored(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    client.post(f"/objets/{item.id}/notes", data={"text": "   "})
    assert db.session.scalar(db.select(db.func.count(Note.id))) == 0


def test_note_deletion_rights(client, as_role, ref, users):
    item = new_item(ref["bois"])
    other_prof = make_user(P, email="autre@ohn.test")
    note = Note(item=item, author=users[P], text="à moi")
    other = Note(item=item, author=other_prof, text="à l'autre")
    db.session.add_all([note, other])
    db.session.commit()
    note_id, other_id = note.id, other.id

    as_role(P)
    assert client.post(f"/objets/notes/{other_id}/supprimer").status_code == 403
    assert client.post(f"/objets/notes/{note_id}/supprimer").status_code == 302
    as_role(A)
    assert client.post(f"/objets/notes/{other_id}/supprimer").status_code == 302
    assert db.session.scalar(db.select(db.func.count(Note.id))) == 0


def test_note_html_escaped(client, as_role, ref):
    item = new_item(ref["bois"])
    as_role(P)
    client.post(f"/objets/{item.id}/notes", data={"text": "<script>alert(1)</script>"})
    r = client.get(f"/objets/{item.id}")
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


# ---------------------------------------------------------------------------
# Actions groupées
# ---------------------------------------------------------------------------

def test_bulk_move(client, as_role, ref):
    a, b = new_item(ref["bois"]), new_item(ref["bois"])
    as_role(P)
    client.post("/objets/actions-groupees", data={"ids": [a.id, b.id], "action": "move",
                                                   "location_id": ref["salle"].id})
    db.session.refresh(a), db.session.refresh(b)
    assert a.location == b.location == ref["salle"]
    assert db.session.scalar(db.select(db.func.count(Activity.id)).where(Activity.action == "item.move")) == 2


def test_bulk_move_to_no_location(client, as_role, ref):
    a = new_item(ref["bois"], location=ref["salle"])
    as_role(P)
    client.post("/objets/actions-groupees", data={"ids": [a.id], "action": "move", "location_id": "0"})
    db.session.refresh(a)
    assert a.location is None


def test_bulk_tag(client, as_role, ref):
    a, b = new_item(ref["bois"]), new_item(ref["bois"])
    as_role(P)
    client.post("/objets/actions-groupees", data={"ids": [a.id, b.id], "action": "tag", "tag": "concert"})
    client.post("/objets/actions-groupees", data={"ids": [a.id], "action": "tag", "tag": "concert"})
    assert [t.name for t in a.tags] == ["concert"] and [t.name for t in b.tags] == ["concert"]


def test_bulk_without_selection(client, as_role, ref):
    as_role(P)
    r = client.post("/objets/actions-groupees", data={"action": "move"}, follow_redirects=True)
    assert "Aucun objet sélectionné" in r.text


def test_bulk_unknown_action(client, as_role, ref):
    a = new_item(ref["bois"])
    as_role(P)
    r = client.post("/objets/actions-groupees", data={"ids": [a.id], "action": "boom"}, follow_redirects=True)
    assert "Action inconnue" in r.text


# ---------------------------------------------------------------------------
# Fiche
# ---------------------------------------------------------------------------

def test_detail_shows_info_and_history(client, as_role, ref):
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref, brand="Buffet", serial_number="X1", estimated_value="1500"))
    item = db.session.scalar(db.select(Item))
    r = client.get(f"/objets/{item.id}")
    assert "Buffet" in r.text and "X1" in r.text and "1 500,00 €" in r.text
    assert "Local &gt; Armoire" in r.text and "Création de OHN-0001" in r.text


def test_codes_follow_configured_prefix(app, client, as_role, ref):
    """Le préfixe est interchangeable : un changement ne touche pas les codes existants."""
    new_item(ref["bois"], "OHN-0007")
    app.config["ITEM_CODE_PREFIX"] = "ABC"
    as_role(P)
    client.post("/objets/nouveau", data=form_data(ref))
    client.post("/objets/nouveau", data=form_data(ref))
    assert sorted(db.session.scalars(db.select(Item.code))) == ["ABC-0001", "ABC-0002", "OHN-0007"]


@pytest.mark.parametrize("url", ["/scan"])
def test_code_placeholder_uses_prefix(app, client, as_role, url):
    app.config["ITEM_CODE_PREFIX"] = "ABC"
    as_role(A)
    r = client.get(url)
    assert "ABC-0042" in r.text and "OHN-0042" not in r.text
