from datetime import date
from io import BytesIO

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage, MultiDict

from app.extensions import db
from app.models import Category, CustomField, Item, Location, Tag
from app.services import items as svc
from app.services.photos import InvalidImage, save_photo


def test_parse_tags_dedup_and_normalise(app):
    tags = svc.parse_tags(" étude ,  Prêt   élève, ÉTUDE,, ")
    assert [t.name for t in tags] == ["étude", "Prêt élève"]


def test_parse_tags_reuses_existing_case_insensitive(app):
    db.session.add(Tag(name="Étude"))
    db.session.commit()
    tags = svc.parse_tags("étude")
    db.session.commit()
    assert tags[0].name == "Étude"
    assert db.session.scalar(db.select(db.func.count(Tag.id))) == 1


def test_parse_tags_empty(app):
    assert svc.parse_tags(None) == [] and svc.parse_tags(" , ") == []


def test_parse_tags_truncates(app):
    assert len(svc.parse_tags("x" * 80)[0].name) == 50


def test_location_descendants(app):
    root = Location(name="Local")
    a = Location(name="A", parent=root)
    b = Location(name="B", parent=a)
    other = Location(name="Autre")
    db.session.add_all([root, a, b, other])
    db.session.commit()
    assert svc.location_descendants(root.id) == {root.id, a.id, b.id}
    assert svc.location_descendants(b.id) == {b.id}


def test_location_descendants_with_cycle(app):
    a, b = Location(name="A"), Location(name="B")
    db.session.add_all([a, b])
    db.session.flush()
    a.parent_id, b.parent_id = b.id, a.id
    db.session.commit()
    assert svc.location_descendants(a.id) == {a.id, b.id}


@pytest.fixture
def fields(app):
    bois, cuivres = Category(name="Bois"), Category(name="Cuivres")
    db.session.add_all([bois, cuivres])
    f = {
        "texte": CustomField(label="Remarque", type="texte"),
        "nombre": CustomField(label="Diapason", type="nombre", required=True),
        "date": CustomField(label="Garantie", type="date"),
        "booleen": CustomField(label="Housse", type="booleen"),
        "liste": CustomField(label="Tonalité", type="liste", options=["Sib", "Mib"], categories=[cuivres]),
    }
    db.session.add_all(f.values())
    db.session.commit()
    return bois, cuivres, f


def test_fields_for_category_scoping(fields):
    bois, cuivres, f = fields
    assert f["liste"] not in svc.fields_for_category(bois.id)
    assert f["liste"] in svc.fields_for_category(cuivres.id)
    assert f["texte"] in svc.fields_for_category(bois.id)


def test_validate_custom_values_ok(fields):
    *_, f = fields
    form = MultiDict({f"cf_{f['nombre'].id}": "442,5", f"cf_{f['date'].id}": "2027-01-31",
                      f"cf_{f['booleen'].id}": "on", f"cf_{f['liste'].id}": "Sib", f"cf_{f['texte'].id}": " x "})
    values, errors = svc.validate_custom_values(list(f.values()), form)
    assert errors == {}
    assert values[f["nombre"].id] == "442.5"
    assert values[f["date"].id] == "2027-01-31"
    assert values[f["booleen"].id] == "1"
    assert values[f["liste"].id] == "Sib"
    assert values[f["texte"].id] == "x"


def test_validate_custom_values_errors(fields):
    *_, f = fields
    form = MultiDict({f"cf_{f['date'].id}": "31/01/2027", f"cf_{f['liste'].id}": "Ré"})
    values, errors = svc.validate_custom_values(list(f.values()), form)
    assert errors[f["nombre"].id] == "Ce champ est obligatoire."
    assert errors[f["date"].id] == "Valeur invalide."
    assert errors[f["liste"].id] == "Valeur invalide."
    assert values[f["booleen"].id] == "0"


def test_validate_number_invalid(fields):
    *_, f = fields
    _, errors = svc.validate_custom_values([f["nombre"]], MultiDict({f"cf_{f['nombre'].id}": "abc"}))
    assert errors == {f["nombre"].id: "Valeur invalide."}


def test_apply_and_display_custom_values(fields):
    bois, cuivres, f = fields
    item = Item(code="OHN-0001", name="Trompette", category=cuivres)
    db.session.add(item)
    db.session.flush()
    changes = svc.apply_custom_values(item, {f["liste"].id: "Sib", f["booleen"].id: "1", f["date"].id: "2027-01-31"})
    db.session.commit()
    assert changes["Tonalité"] == [None, "Sib"]
    shown = dict((fld.label, v) for fld, v in svc.custom_display(item))
    assert shown == {"Tonalité": "Sib", "Housse": "Oui", "Garantie": "31/01/2027"}

    changes = svc.apply_custom_values(item, {f["liste"].id: None, f["booleen"].id: "1"})
    db.session.commit()
    assert changes == {"Tonalité": ["Sib", None]}
    assert len(item.custom_values) == 2


def _image_file(fmt="PNG", size=(3000, 1500), name="photo.png"):
    buf = BytesIO()
    Image.new("RGB", size, "red").save(buf, fmt)
    buf.seek(0)
    return FileStorage(buf, filename=name)


def test_save_photo_resizes_and_thumbnails(app):
    full, thumb = save_photo(_image_file())
    root = app.config["UPLOAD_DIR"]
    with Image.open(root / full) as img:
        assert max(img.size) == 2000 and img.format == "JPEG"
    with Image.open(root / thumb) as img:
        assert max(img.size) == 400


def test_save_photo_rejects_non_image(app):
    with pytest.raises(InvalidImage):
        save_photo(FileStorage(BytesIO(b"<script>alert(1)</script>"), filename="x.png"))


def test_save_photo_converts_rgba(app):
    buf = BytesIO()
    Image.new("RGBA", (10, 10)).save(buf, "PNG")
    buf.seek(0)
    full, _ = save_photo(FileStorage(buf, filename="t.png"))
    assert full.endswith(".jpg")
