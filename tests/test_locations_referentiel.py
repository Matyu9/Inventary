import re

import pytest

from app.extensions import db
from app.models import Activity, Category, CustomField, CustomFieldValue, Item, Location, Role

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


@pytest.fixture
def tree(app):
    local = Location(name="Local")
    armoire = Location(name="Armoire", parent=local)
    etagere = Location(name="Étagère", parent=armoire)
    salle = Location(name="Salle")
    db.session.add_all([local, armoire, etagere, salle])
    db.session.commit()
    return dict(local=local, armoire=armoire, etagere=etagere, salle=salle)


@pytest.fixture
def bois(app):
    c = Category(name="Bois")
    db.session.add(c)
    db.session.commit()
    return c


def item_in(location, category, code):
    item = Item(code=code, name=f"Objet {code}", category=category, location=location)
    db.session.add(item)
    db.session.commit()
    return item


# ---------------------------------------------------------------------------
# Lieux
# ---------------------------------------------------------------------------

def test_tree_page(client, as_role, tree, bois):
    item_in(tree["etagere"], bois, "OHN-0001")
    item_in(None, bois, "OHN-0002")
    as_role(P)
    r = client.get("/lieux")
    for name in ("Local", "Armoire", "Étagère", "Salle"):
        assert name in r.text
    assert "1 objet(s) sans lieu" in r.text


def test_location_detail_includes_descendants(client, as_role, tree, bois):
    deep = item_in(tree["etagere"], bois, "OHN-0001")
    other = item_in(tree["salle"], bois, "OHN-0002")
    as_role(P)
    r = client.get(f"/lieux/{tree['local'].id}")
    assert deep.code in r.text and other.code not in r.text
    assert "Local &gt; Armoire &gt; Étagère" in r.text


def test_location_404(client, as_role, tree):
    as_role(P)
    assert client.get("/lieux/999").status_code == 404


def test_create_location(client, as_role, tree):
    as_role(P)
    r = client.post("/lieux/nouveau", data={"name": " Placard ", "parent_id": tree["salle"].id})
    loc = db.session.scalar(db.select(Location).filter_by(name="Placard"))
    assert r.status_code == 302 and loc.parent == tree["salle"]
    assert db.session.scalar(db.select(Activity).filter_by(action="location.create"))


def test_create_root_location(client, as_role, tree):
    as_role(P)
    client.post("/lieux/nouveau", data={"name": "Camion", "parent_id": "0"})
    assert db.session.scalar(db.select(Location).filter_by(name="Camion")).parent is None


def test_create_location_requires_name(client, as_role, tree):
    as_role(P)
    r = client.post("/lieux/nouveau", data={"name": "", "parent_id": "0"})
    assert r.status_code == 200 and "aria-invalid" in r.text


def test_create_prefills_parent(client, as_role, tree):
    as_role(P)
    r = client.get(f"/lieux/nouveau?parent_id={tree['armoire'].id}")
    assert f'<option selected value="{tree["armoire"].id}">' in r.text


def test_edit_location(client, as_role, tree):
    as_role(P)
    client.post(f"/lieux/{tree['salle'].id}/modifier", data={"name": "Grande salle", "parent_id": tree["local"].id})
    db.session.refresh(tree["salle"])
    assert tree["salle"].name == "Grande salle" and tree["salle"].parent == tree["local"]
    entry = db.session.scalar(db.select(Activity).filter_by(action="location.update"))
    assert entry.changes["name"] == ["Salle", "Grande salle"]


@pytest.mark.parametrize("target", ["local", "armoire", "etagere"])
def test_cannot_nest_location_in_itself_or_descendant(client, as_role, tree, target):
    as_role(P)
    r = client.post(f"/lieux/{tree['armoire'].id}/modifier", data={"name": "Armoire", "parent_id": tree[target].id})
    db.session.refresh(tree["armoire"])
    if target == "local":
        assert r.status_code == 302
    else:
        assert r.status_code == 200 and tree["armoire"].parent == tree["local"]


def test_edit_form_excludes_descendants(client, as_role, tree):
    as_role(P)
    r = client.get(f"/lieux/{tree['armoire'].id}/modifier")
    assert f'value="{tree["etagere"].id}"' not in r.text and f'value="{tree["salle"].id}"' in r.text


def test_delete_location_rules(client, as_role, tree, bois):
    item_in(tree["salle"], bois, "OHN-0001")
    as_role(P)
    assert client.post(f"/lieux/{tree['etagere'].id}/supprimer").status_code == 403
    as_role(A)
    r = client.post(f"/lieux/{tree['armoire'].id}/supprimer", follow_redirects=True)
    assert "Videz ce lieu" in r.text  # contient un sous-lieu
    r = client.post(f"/lieux/{tree['salle'].id}/supprimer", follow_redirects=True)
    assert "Videz ce lieu" in r.text  # contient un objet
    r = client.post(f"/lieux/{tree['etagere'].id}/supprimer")
    assert r.status_code == 302 and db.session.get(Location, tree["etagere"].id) is None


def test_borrower_cannot_see_locations(client, as_role, tree):
    as_role(E)
    assert client.get(f"/lieux/{tree['local'].id}").status_code == 403
    assert client.post(f"/lieux/{tree['local'].id}/modifier", data={"name": "x"}).status_code == 403


# ---------------------------------------------------------------------------
# Catégories
# ---------------------------------------------------------------------------

def test_prof_creates_category(client, as_role, app):
    as_role(P)
    r = client.post("/categories", data={"name": "Claviers", "type": "instrument", "color": "#123abc"})
    assert r.status_code == 302
    cat = db.session.scalar(db.select(Category))
    assert cat.name == "Claviers" and cat.color == "#123abc"


@pytest.mark.parametrize("name", ["Bois", "bois", "BOIS"])
def test_duplicate_category_rejected(client, as_role, bois, name):
    as_role(P)
    r = client.post("/categories", data={"name": name, "type": "instrument"})
    assert "existe déjà" in r.text
    assert db.session.scalar(db.select(db.func.count(Category.id))) == 1


def test_duplicate_category_accent_insensitive(client, as_role, app):
    as_role(P)
    client.post("/categories", data={"name": "Électronique", "type": "materiel"})
    r = client.post("/categories", data={"name": "electronique", "type": "materiel"})
    assert "existe déjà" in r.text


def no_color_checked(html):
    return bool(re.search(r'<input checked id="no_color"', html))


def test_category_without_color(client, as_role, app):
    """Le sélecteur envoie toujours #000000 : la case « Sans couleur » doit l'emporter."""
    as_role(P)
    assert no_color_checked(client.get("/categories").text)
    client.post("/categories", data={"name": "Claviers", "type": "instrument", "color": "#000000", "no_color": "y"})
    cat = db.session.scalar(db.select(Category))
    assert cat.color is None
    assert "swatch" not in client.get("/categories").text


def test_edit_category_color_roundtrip(client, as_role, bois):
    as_role(P)
    assert no_color_checked(client.get(f"/categories/{bois.id}/modifier").text)
    client.post(f"/categories/{bois.id}/modifier", data={"name": "Bois", "type": "instrument", "color": "#123abc"})
    db.session.refresh(bois)
    assert bois.color == "#123abc"
    assert not no_color_checked(client.get(f"/categories/{bois.id}/modifier").text)
    client.post(f"/categories/{bois.id}/modifier",
                data={"name": "Bois", "type": "instrument", "color": "#123abc", "no_color": "y"})
    db.session.refresh(bois)
    assert bois.color is None


def test_invalid_color_rejected(client, as_role, app):
    as_role(P)
    r = client.post("/categories", data={"name": "X", "type": "instrument", "color": "red"})
    assert "#RRGGBB" in r.text


def test_invalid_category_type_rejected(client, as_role, app):
    as_role(P)
    client.post("/categories", data={"name": "X", "type": "nimporte"})
    assert db.session.scalar(db.select(db.func.count(Category.id))) == 0


def test_category_list_counts(client, as_role, bois):
    item_in(None, bois, "OHN-0001")
    item_in(None, bois, "OHN-0002")
    as_role(P)
    r = client.get("/categories")
    assert "<td>2</td>" in r.text and "Nouvelle catégorie" in r.text


def test_edit_category_keeps_own_name(client, as_role, bois):
    as_role(P)
    r = client.post(f"/categories/{bois.id}/modifier", data={"name": "Bois", "type": "materiel"})
    assert r.status_code == 302
    db.session.refresh(bois)
    assert bois.type == "materiel"


def test_edit_category_name_collision(client, as_role, bois):
    other = Category(name="Cuivres")
    db.session.add(other)
    db.session.commit()
    as_role(P)
    r = client.post(f"/categories/{other.id}/modifier", data={"name": "bois", "type": "instrument"})
    assert "existe déjà" in r.text


def test_delete_category_rules(client, as_role, bois):
    empty = Category(name="Vide")
    db.session.add(empty)
    db.session.commit()
    item_in(None, bois, "OHN-0001")
    as_role(P)
    assert client.post(f"/categories/{empty.id}/supprimer").status_code == 403
    as_role(A)
    r = client.post(f"/categories/{bois.id}/supprimer", follow_redirects=True)
    assert "ne peut pas être supprimée" in r.text
    client.post(f"/categories/{empty.id}/supprimer")
    assert db.session.get(Category, empty.id) is None


def test_borrower_cannot_access_categories(client, as_role, bois):
    as_role(E)
    assert client.get("/categories").status_code == 403
    assert client.get(f"/categories/{bois.id}/modifier").status_code == 403


def test_category_404(client, as_role):
    as_role(A)
    assert client.get("/categories/42/modifier").status_code == 404
    assert client.post("/categories/42/supprimer").status_code == 404


# ---------------------------------------------------------------------------
# Champs personnalisés
# ---------------------------------------------------------------------------

def test_prof_cannot_manage_custom_fields(client, as_role, app):
    as_role(P)
    assert client.get("/champs-perso").status_code == 403
    assert client.post("/champs-perso/nouveau", data={"label": "x", "type": "texte"}).status_code == 403


def test_create_list_field(client, as_role, bois):
    as_role(A)
    r = client.post("/champs-perso/nouveau", data={"label": "Tonalité", "type": "liste",
                                                    "options": "Sib\n\n Mib \nUt", "required": "y",
                                                    "categories": [bois.id], "position": "2"})
    assert r.status_code == 302
    field = db.session.scalar(db.select(CustomField))
    assert field.options == ["Sib", "Mib", "Ut"] and field.required and field.categories == [bois]
    assert "Tonalité" in client.get("/champs-perso").text


def test_list_field_requires_options(client, as_role, app):
    as_role(A)
    r = client.post("/champs-perso/nouveau", data={"label": "Tonalité", "type": "liste", "options": "  "})
    assert "au moins un choix" in r.text


def test_non_list_field_drops_options(client, as_role, app):
    as_role(A)
    client.post("/champs-perso/nouveau", data={"label": "Diapason", "type": "nombre", "options": "a\nb"})
    assert db.session.scalar(db.select(CustomField)).options is None


def test_edit_custom_field(client, as_role, bois):
    field = CustomField(label="Ton", type="liste", options=["Sib"], categories=[bois])
    db.session.add(field)
    db.session.commit()
    as_role(A)
    r = client.get(f"/champs-perso/{field.id}/modifier")
    assert "Sib" in r.text
    client.post(f"/champs-perso/{field.id}/modifier", data={"label": "Tonalité", "type": "texte"})
    db.session.refresh(field)
    assert field.label == "Tonalité" and field.type == "texte" and field.categories == []


def test_delete_custom_field_removes_values(client, as_role, bois):
    field = CustomField(label="Ton", type="texte")
    item = Item(code="OHN-0001", name="Sax", category=bois)
    db.session.add_all([field, item])
    db.session.flush()
    db.session.add(CustomFieldValue(item_id=item.id, field_id=field.id, value="Mib"))
    db.session.commit()
    as_role(A)
    client.post(f"/champs-perso/{field.id}/supprimer")
    db.session.expire_all()
    assert db.session.scalar(db.select(db.func.count(CustomFieldValue.id))) == 0
    assert db.session.get(Item, item.id) is not None


def test_custom_field_404(client, as_role):
    as_role(A)
    assert client.get("/champs-perso/9/modifier").status_code == 404


def test_nav_links_by_role(client, as_role, app):
    as_role(A)
    assert "Champs perso" in client.get("/").text
    as_role(P)
    r = client.get("/")
    assert "Inventaire" in r.text and "Champs perso" not in r.text
    as_role(E)
    r = client.get("/")
    assert "/objets" not in r.text and "/lieux" not in r.text
