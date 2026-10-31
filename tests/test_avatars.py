"""Photos de profil : envoi, recadrage, affichage, droits, fusion et suppression."""
import io
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Activity, Role, User
from app.services import people

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


def image(width=800, height=400, fmt="PNG", color=(200, 30, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, fmt)
    buf.seek(0)
    return buf


def file(data=None):
    return FileStorage(data or image(), "photo.png")


def upload(client, url, data=None, name="moi.png"):
    return client.post(url, data={"photo": (data or image(), name)}, content_type="multipart/form-data",
                       follow_redirects=True)


def avatar_path(app, user):
    return Path(app.config["UPLOAD_DIR"]) / user.avatar


def test_upload_own_avatar_cropped_square(client, app, users):
    login(client, users[E])
    r = upload(client, "/mon-compte/photo")
    assert "Photo enregistrée" in r.text
    db.session.refresh(users[E])
    assert users[E].avatar.startswith("avatars/") and users[E].avatar.endswith(".jpg")
    with Image.open(avatar_path(app, users[E])) as img:
        assert img.size == (256, 256) and img.format == "JPEG"
    url = f"/photos-de-profil/{users[E].avatar.split('/')[1]}"
    assert f'src="{url}"' in client.get("/").text  # menu
    served = client.get(url)
    assert served.status_code == 200 and served.mimetype == "image/jpeg"
    assert "private" in served.headers["Cache-Control"]
    assert db.session.scalar(db.select(Activity).filter_by(action="account.avatar"))


def test_replacing_avatar_deletes_old_file(client, app, users):
    login(client, users[E])
    upload(client, "/mon-compte/photo")
    db.session.refresh(users[E])
    old = avatar_path(app, users[E])
    upload(client, "/mon-compte/photo", image(color=(0, 0, 255)))
    db.session.refresh(users[E])
    assert not old.exists() and avatar_path(app, users[E]).exists()


def test_remove_own_avatar(client, app, users):
    login(client, users[E])
    upload(client, "/mon-compte/photo")
    db.session.refresh(users[E])
    path = avatar_path(app, users[E])
    client.post("/mon-compte/photo/supprimer")
    db.session.refresh(users[E])
    assert users[E].avatar is None and not path.exists()
    assert ">ET<" in client.get("/mon-compte").text.replace("\n", "")  # initiales à la place


@pytest.mark.parametrize("data, name, message", [
    (io.BytesIO(b"pas une image"), "faux.png", "pas une image valide"),
    (None, "", "Choisissez une photo"),
])
def test_invalid_upload_refused(client, users, data, name, message):
    login(client, users[E])
    if name:
        r = upload(client, "/mon-compte/photo", data, name)
    else:
        r = client.post("/mon-compte/photo", data={}, follow_redirects=True)
    assert message in r.text
    db.session.refresh(users[E])
    assert users[E].avatar is None


def test_exif_rotation_applied(client, app, users):
    buf = io.BytesIO()
    img = Image.new("RGB", (300, 100), (10, 200, 10))
    exif = img.getexif()
    exif[0x0112] = 6  # orientation : rotation de 90°
    img.save(buf, "JPEG", exif=exif)
    buf.seek(0)
    login(client, users[E])
    upload(client, "/mon-compte/photo", buf, "photo.jpg")
    db.session.refresh(users[E])
    with Image.open(avatar_path(app, users[E])) as saved:
        assert saved.size == (256, 256) and not saved.getexif().get(0x0112)


def test_admin_sets_and_removes_someones_avatar(client, as_role, app, users):
    as_role(A)
    upload(client, f"/utilisateurs/{users[P].id}/photo")
    db.session.refresh(users[P])
    assert users[P].avatar
    page = client.get(f"/utilisateurs/{users[P].id}").text
    assert "Retirer la photo" in page and users[P].avatar.split("/")[1] in page
    assert users[P].avatar.split("/")[1] in client.get("/utilisateurs").text
    client.post(f"/utilisateurs/{users[P].id}/photo/supprimer")
    db.session.refresh(users[P])
    assert users[P].avatar is None


def test_avatar_management_rights(client, as_role, users):
    as_role(P)
    assert client.post(f"/utilisateurs/{users[E].id}/photo").status_code == 403
    assert "Photo de profil" not in client.get(f"/utilisateurs/{users[E].id}").text
    db.session.add(Role(key="secretariat", name="Secrétariat", permissions=["user.manage", "borrower.create"]))
    manager = make_user(E, email="secr@ohn.test")
    manager.role = "secretariat"
    db.session.commit()
    client.post("/deconnexion")
    login(client, manager)
    assert client.post(f"/utilisateurs/{users[A].id}/photo").status_code == 403


def test_avatar_file_requires_login_and_valid_name(client, users):
    assert "/connexion" in client.get(f"/photos-de-profil/{'a' * 32}.jpg").headers["Location"]
    login(client, users[E])
    assert client.get(f"/photos-de-profil/{'a' * 32}.jpg").status_code == 404
    assert client.get("/photos-de-profil/..%2Finventaire.db").status_code == 404
    assert client.get("/photos-de-profil/x.png").status_code == 404


def test_merge_keeps_target_avatar_or_takes_source(app, users):
    with app.test_request_context():
        dup, keep = make_user(E, email="dup@ohn.test"), make_user(E, email="keep@ohn.test")
        people.set_avatar(dup, file())
        dup_file = Path(app.config["UPLOAD_DIR"]) / dup.avatar
        people.merge(dup, keep)  # la fiche conservée n'a pas de photo : elle prend celle du doublon
        db.session.commit()
        keep = db.session.get(User, keep.id)
        assert keep.avatar and (Path(app.config["UPLOAD_DIR"]) / keep.avatar) == dup_file and dup_file.exists()

        other = make_user(P, email="other@ohn.test")
        people.set_avatar(other, file())
        other_file = Path(app.config["UPLOAD_DIR"]) / other.avatar
        people.merge(other, keep)  # elle a déjà une photo : celle du doublon est effacée
        db.session.commit()
        assert not other_file.exists() and db.session.get(User, keep.id).avatar.endswith(dup_file.name)


def test_delete_record_removes_avatar_file(client, as_role, app):
    as_role(A)
    tom = User(first_name="Tom", last_name="Petit", role=E)
    db.session.add(tom)
    db.session.commit()
    with app.test_request_context():
        people.set_avatar(tom, file())
    db.session.commit()
    path = avatar_path(app, tom)
    client.post(f"/utilisateurs/{tom.id}/supprimer")
    assert not path.exists()
