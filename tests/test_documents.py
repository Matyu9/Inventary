"""Documents joints (factures, notices…) : type réel contrôlé, droits dédiés, fichiers servis prudemment."""
from io import BytesIO

import pytest

from app.extensions import db
from app.models import Activity, Category, Document, Item, Kit, Role
from app.services import trash
from app.services.documents import clean_name, human_size

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
DOCX = b"PK\x03\x04" + b"\x00" * 32


@pytest.fixture
def gear(app):
    sax = Item(code="OHN-0001", name="Saxophone alto", category=Category(name="Bois"))
    kit = Kit(name="Sono")
    db.session.add_all([sax, kit])
    db.session.commit()
    return {"sax": sax, "kit": kit}


def upload(client, owner_url, *files, **form):
    data = {"documents": [(BytesIO(content), name) for name, content in files], **form}
    return client.post(f"/documents/{owner_url}", data=data, content_type="multipart/form-data",
                       follow_redirects=True)


def only_document():
    return db.session.scalar(db.select(Document))


# ---------------------------------------------------------------------------
# Envoi
# ---------------------------------------------------------------------------

def test_prof_attaches_pdf_to_item(client, as_role, app, gear):
    prof = as_role(P)
    r = upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF), kind="facture", title="Facture 2024")
    assert "1 document ajouté" in r.text and "Facture 2024" in r.text
    doc = only_document()
    assert doc.item_id == gear["sax"].id and doc.kind == "facture" and doc.mime == "application/pdf"
    assert doc.original_name == "facture.pdf" and doc.size == len(PDF) and doc.uploaded_by_id == prof.id
    assert doc.filename.startswith("documents/") and doc.filename.endswith(".pdf")
    assert (app.config["UPLOAD_DIR"] / doc.filename).read_bytes() == PDF
    assert db.session.scalar(db.select(Activity.action).order_by(Activity.id.desc())) == "document.add"


def test_attach_to_kit(client, as_role, gear):
    as_role(P)
    r = upload(client, f"kits/{gear['kit'].id}", ("notice.pdf", PDF), kind="notice")
    assert "notice" in r.text
    assert only_document().kit_id == gear["kit"].id


def test_several_files_take_their_own_names(client, as_role, gear):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", ("a.pdf", PDF), ("photo.png", PNG), title="ignoré")
    assert sorted(d.title for d in db.session.scalars(db.select(Document))) == ["a", "photo"]


def test_unknown_kind_falls_back_to_other(client, as_role, gear):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", ("x.pdf", PDF), kind="<script>")
    assert only_document().kind == "autre"


@pytest.mark.parametrize("name, content, message", [
    ("virus.exe", b"MZ\x90\x00", "Format non accepté"),
    ("page.html", b"<html><script>alert(1)</script>", "Format non accepté"),
    ("sans-extension", PDF, "Format non accepté"),
    ("faux.pdf", b"MZ\x90\x00 programme", "ne correspond pas"),
    ("faux.pdf", b"<html><script>alert(1)</script>", "ne correspond pas"),
    ("faux.png", PDF, "ne correspond pas"),
    ("faux.docx", PDF, "ne correspond pas"),
    ("faux.webp", b"RIFF\x00\x00\x00\x00WAVE", "ne correspond pas"),
    ("binaire.txt", b"texte\x00binaire", "ne correspond pas"),
    ("vide.pdf", b"", "vide"),
])
def test_refused_files(client, as_role, gear, name, content, message):
    as_role(A)
    r = upload(client, f"objets/{gear['sax'].id}", (name, content))
    assert message in r.text
    assert only_document() is None


@pytest.mark.parametrize("name, content, mime", [
    ("notice.docx", DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("tableau.ods", DOCX, "application/vnd.oasis.opendocument.spreadsheet"),
    ("photo.JPG", b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
    ("image.webp", b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image/webp"),
    ("liste.csv", "nom;prix\nAnche;3,50 €\n".encode(), "text/csv; charset=utf-8"),
    ("ancien.txt", "Révision chez l'artisan".encode("latin-1"), "text/plain; charset=utf-8"),
])
def test_accepted_formats(client, as_role, gear, name, content, mime):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", (name, content))
    assert only_document().mime == mime


def test_no_file_sent(client, as_role, gear):
    as_role(A)
    r = client.post(f"/documents/objets/{gear['sax'].id}", data={}, follow_redirects=True)
    assert "Aucun fichier" in r.text


def test_unknown_owner_returns_404(client, as_role, gear):
    as_role(A)
    assert client.post("/documents/objets/999", data={}).status_code == 404
    assert client.post(f"/documents/lieux/{gear['sax'].id}", data={}).status_code == 404


@pytest.mark.parametrize("raw, expected", [
    ("../../etc/passwd.pdf", "passwd.pdf"),
    ("C:\\Users\\Moi\\facture.pdf", "facture.pdf"),
    ("fac\x00tu\nre.pdf", "facture.pdf"),
    ("", "document"),
    ("a" * 300 + ".pdf", "a" * 196 + ".pdf"),
])
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


def test_human_size():
    assert human_size(512) == "512 o"
    assert human_size(2048) == "2 Ko"
    assert human_size(3 * 1024 ** 2 + 200_000) == "3,2 Mo"


# ---------------------------------------------------------------------------
# Consultation
# ---------------------------------------------------------------------------

def test_pdf_shown_inline_with_safe_headers(client, as_role, gear):
    as_role(P)
    upload(client, f"objets/{gear['sax'].id}", ("Facture été.pdf", PDF))
    doc = only_document()
    r = client.get(f"/documents/{doc.id}/{doc.original_name}")
    assert r.status_code == 200 and r.data == PDF
    assert r.mimetype == "application/pdf"
    assert r.headers["Content-Disposition"].startswith("inline")
    assert "filename*=UTF-8''Facture%20%C3%A9t%C3%A9.pdf" in r.headers["Content-Disposition"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in r.headers["Cache-Control"]


def test_office_file_downloaded(client, as_role, gear):
    as_role(P)
    upload(client, f"objets/{gear['sax'].id}", ("notice.docx", DOCX))
    doc = only_document()
    r = client.get(f"/documents/{doc.id}/notice.docx")
    assert r.headers["Content-Disposition"].startswith("attachment")


def test_image_gets_strict_csp(client, as_role, gear):
    as_role(P)
    upload(client, f"objets/{gear['sax'].id}", ("plan.png", PNG))
    doc = only_document()
    r = client.get(f"/documents/{doc.id}/plan.png")
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]


def test_section_lists_documents(client, as_role, gear):
    as_role(P)
    upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF), kind="facture")
    page = client.get(f"/objets/{gear['sax'].id}").text
    assert 'id="documents"' in page and "Facture" in page and "Joindre un document" in page
    assert "📎 1" in client.get("/objets").text


def test_photo_route_no_longer_serves_documents(client, as_role, gear):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF))
    doc = only_document()
    assert client.get(f"/objets/fichiers/{doc.filename}").status_code == 404
    assert client.get("/objets/fichiers/avatars/x.jpg").status_code == 404


# ---------------------------------------------------------------------------
# Droits
# ---------------------------------------------------------------------------

def test_borrower_cannot_see_or_add(client, as_role, gear):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF))
    doc = only_document()
    as_role(E)
    assert client.get(f"/documents/{doc.id}/facture.pdf").status_code == 403
    assert client.post(f"/documents/objets/{gear['sax'].id}", data={}).status_code == 403
    assert client.post(f"/documents/{doc.id}/supprimer").status_code == 403


def test_inventory_viewer_without_document_right(client, app, as_role, gear):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF))
    doc = only_document()
    db.session.add(Role(key="benevole", name="Bénévole", permissions=["inventory.view"]))
    db.session.commit()
    client.post("/deconnexion")
    login(client, make_user("benevole", email="benevole@ohn.test"))
    page = client.get(f"/objets/{gear['sax'].id}").text
    assert 'id="documents"' not in page and "facture" not in page
    assert "📎" not in client.get("/objets").text
    assert client.get(f"/documents/{doc.id}/facture.pdf").status_code == 403


def test_read_only_document_right(client, app, as_role, gear):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF))
    doc = only_document()
    db.session.add(Role(key="tresorier", name="Trésorier", permissions=["inventory.view", "document.view"]))
    db.session.commit()
    client.post("/deconnexion")
    login(client, make_user("tresorier", email="tresorier@ohn.test"))
    page = client.get(f"/objets/{gear['sax'].id}").text
    assert "facture" in page and "Joindre un document" not in page and "Supprimer ce document" not in page
    assert client.get(f"/documents/{doc.id}/facture.pdf").status_code == 200
    assert client.post(f"/documents/objets/{gear['sax'].id}", data={}).status_code == 403


def test_document_rights_listed_on_roles_page(client, as_role):
    as_role(A)
    page = client.get("/roles/prof/modifier").text
    assert "Voir les documents joints" in page and "Ajouter et supprimer des documents joints" in page


# ---------------------------------------------------------------------------
# Suppression et corbeille
# ---------------------------------------------------------------------------

def test_delete_document(client, as_role, app, gear):
    as_role(P)
    upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF))
    doc = only_document()
    path = app.config["UPLOAD_DIR"] / doc.filename
    r = client.post(f"/documents/{doc.id}/supprimer", follow_redirects=True)
    assert "Document supprimé" in r.text
    assert only_document() is None and not path.exists()
    assert "document.delete" in db.session.scalars(db.select(Activity.action)).all()


def test_document_of_trashed_item_unreachable_then_purged(client, as_role, app, gear):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF))
    upload(client, f"kits/{gear['kit'].id}", ("notice.pdf", PDF))
    item_doc, kit_doc = sorted(db.session.scalars(db.select(Document)), key=lambda d: d.id)
    paths = [app.config["UPLOAD_DIR"] / d.filename for d in (item_doc, kit_doc)]
    client.post(f"/objets/{gear['sax'].id}/supprimer")
    client.post(f"/kits/{gear['kit'].id}/supprimer")
    for doc in (item_doc, kit_doc):
        assert client.get(f"/documents/{doc.id}/x.pdf").status_code == 404
        assert client.post(f"/documents/{doc.id}/supprimer").status_code == 404
    assert all(p.exists() for p in paths)  # restaurable : rien n'est effacé
    client.post("/corbeille/vider")
    assert db.session.scalar(db.select(db.func.count(Document.id))) == 0
    assert not any(p.exists() for p in paths)


def test_documents_kept_on_restore(client, as_role, gear, users):
    as_role(A)
    upload(client, f"objets/{gear['sax'].id}", ("facture.pdf", PDF))
    client.post(f"/objets/{gear['sax'].id}/supprimer")
    client.post(f"/corbeille/objets/{gear['sax'].id}/restaurer")
    doc = only_document()
    assert client.get(f"/documents/{doc.id}/facture.pdf").status_code == 200


def test_purge_expired_removes_document_files(app, gear, users, tmp_path):
    folder = app.config["UPLOAD_DIR"] / "documents"
    folder.mkdir(parents=True)
    (folder / "f.pdf").write_bytes(PDF)
    gear["sax"].documents.append(Document(title="F", filename="documents/f.pdf", original_name="f.pdf",
                                          mime="application/pdf", size=len(PDF)))
    trash.trash_item(gear["sax"], users[A])
    db.session.commit()
    from datetime import datetime, timedelta
    trash.purge_expired(datetime.now() + timedelta(days=31))
    db.session.commit()
    assert not (folder / "f.pdf").exists()
