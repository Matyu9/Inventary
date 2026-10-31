"""Rôles personnalisés : écran « Rôles », droits modifiables, attribution et garde-fous."""
import pytest

from app.extensions import db
from app.models import Activity, Role, User
from app.permissions import DEFAULT_PERMISSIONS, PERMISSIONS, can, holders, permissions_of
from app.services import roles

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


def role_form(name="Régisseur", permissions=(), **kw):
    return {"name": name, "description": "", "oidc_groups": "", "permissions": list(permissions), **kw}


def add_role(key, name, permissions, oidc_groups=None):
    role = Role(key=key, name=name, permissions=list(permissions), oidc_groups=oidc_groups)
    db.session.add(role)
    db.session.commit()
    return role


def person_with(role_key, email):
    user = make_user(E, email=email)
    user.role = role_key
    db.session.commit()
    return user


# --- Droits ------------------------------------------------------------------

def test_builtin_defaults_without_saved_roles(app):
    assert permissions_of(A) == set(PERMISSIONS)
    assert permissions_of(P) == DEFAULT_PERMISSIONS[P]
    assert permissions_of(E) == {"myloans.view", "calendar.view", "loan.request_own"}
    assert permissions_of("inconnu") == set()


def test_saved_role_overrides_defaults(app):
    add_role(P, "Professeur", ["inventory.view"])
    assert permissions_of(P) == {"inventory.view"}


def test_admin_keeps_every_right_even_if_saved_empty(app):
    add_role(A, "Administrateur", [])
    assert permissions_of(A) == set(PERMISSIONS)


def test_object_rights_follow_custom_permissions(app):
    add_role("garant", "Garant", ["loan.guarantee", "note.add"])
    user = person_with("garant", "g@ohn.test")
    loan = type("L", (), {"guarantor_id": user.id, "borrower_id": 0, "requested_by_id": 0})()
    other = type("L", (), {"guarantor_id": 999, "borrower_id": 0, "requested_by_id": 0})()
    note = type("N", (), {"author_id": user.id})()
    assert can(user, "loan.checkout", loan) and not can(user, "loan.checkout", other)
    assert can(user, "note.delete", note) and not can(user, "note.delete", type("N", (), {"author_id": user.id + 1})())
    assert not can(user, "loan.view", other)


def test_holders(app):
    add_role("regie", "Régie", ["loan.guarantee"])
    assert holders("loan.guarantee") == {A, P, "regie"}
    add_role(P, "Professeur", [])
    assert holders("loan.guarantee") == {A, "regie"}


def test_new_key_is_slug_and_unique(app):
    assert roles.new_key("Régisseur son & lumière") == "regisseur-son-lu"
    add_role("regisseur", "Régisseur", [])
    assert roles.new_key("Régisseur") == "regisseur-2"
    assert roles.new_key("Admin") == "admin-2"
    assert roles.new_key("!!!") == "role"
    assert roles.new_key("Prof") == "prof-2"


# --- Écran « Rôles » -----------------------------------------------------------

@pytest.mark.parametrize("role", [P, E])
def test_roles_screen_admin_only(client, as_role, role):
    as_role(role)
    assert client.get("/roles").status_code == 403
    assert client.post("/roles/nouveau", data=role_form()).status_code == 403
    assert db.session.scalar(db.select(db.func.count()).select_from(Role)) == 0


def test_roles_screen_requires_login(client):
    assert client.get("/roles").status_code == 302


def test_list_shows_builtin_roles(client, as_role):
    as_role(A)
    r = client.get("/roles")
    assert r.status_code == 200
    for name in ("Administrateur", "Professeur", "Emprunteur"):
        assert name in r.text


def test_create_role(client, as_role):
    as_role(A)
    r = client.post("/roles/nouveau", data=role_form(
        "Régisseur", ["inventory.view", "scan", "pas.un.droit"], description="Régie du matériel",
        oidc_groups=" regie , , technique "))
    assert r.status_code == 302
    role = db.session.get(Role, "regisseur")
    assert role.name == "Régisseur" and role.description == "Régie du matériel"
    assert role.permissions == ["inventory.view", "scan"]  # droit inconnu ignoré, ordre du catalogue
    assert role.oidc_groups == "regie, technique" and role.oidc_group_list == ["regie", "technique"]
    assert db.session.scalar(db.select(Activity).filter_by(action="role.create"))


def test_duplicate_name_refused(client, as_role):
    as_role(A)
    r = client.post("/roles/nouveau", data=role_form(" professeur "))
    assert "déjà ce nom" in r.text
    assert db.session.scalar(db.select(db.func.count()).select_from(Role)) == 0


def test_duplicate_from_existing_role_prefills_rights(client, as_role):
    as_role(A)
    r = client.get("/roles/nouveau?depuis=prof")
    assert r.status_code == 200
    assert 'value="loan.guarantee" checked' in r.text and 'value="loan.validate" checked' not in r.text
    assert db.session.get(Role, P) is None  # simple lecture : rien d'enregistré


def test_edit_builtin_role_changes_access(client, as_role, users):
    as_role(A)
    perms = DEFAULT_PERMISSIONS[P] - {"report.view"}
    r = client.post("/roles/prof/modifier", data=role_form("Professeur", perms))
    assert r.status_code == 302
    assert set(db.session.get(Role, P).permissions) == perms
    as_role(P)
    assert client.get("/rapports").status_code == 403
    assert client.get("/objets").status_code == 200


def test_edit_can_rename_builtin(client, as_role):
    admin = as_role(A)
    client.post("/roles/emprunteur/modifier", data=role_form("Musicien", ["myloans.view"]))
    assert db.session.get(Role, E).name == "Musicien"
    assert make_user(E, email="m@ohn.test").role_label == "Musicien"
    assert admin.role_label == "Administrateur"


def test_admin_rights_cannot_be_removed(client, as_role):
    as_role(A)
    r = client.get("/roles/admin/modifier")
    assert "disabled" in r.text
    client.post("/roles/admin/modifier", data=role_form("Administrateur", []))
    assert client.get("/roles").status_code == 200
    assert permissions_of(A) == set(PERMISSIONS)


def test_edit_unknown_role_404(client, as_role):
    as_role(A)
    assert client.get("/roles/nope/modifier").status_code == 404


def test_delete_custom_role(client, as_role):
    as_role(A)
    add_role("regie", "Régie", [])
    r = client.post("/roles/regie/supprimer", follow_redirects=True)
    assert "supprimé" in r.text and db.session.get(Role, "regie") is None


def test_delete_refused_while_assigned(client, as_role):
    as_role(A)
    add_role("regie", "Régie", [])
    person_with("regie", "r@ohn.test")
    r = client.post("/roles/regie/supprimer", follow_redirects=True)
    assert "1 personne" in r.text and db.session.get(Role, "regie") is not None


def test_builtin_role_cannot_be_deleted(client, as_role):
    as_role(A)
    add_role(P, "Professeur", [])
    r = client.post("/roles/prof/supprimer", follow_redirects=True)
    assert "ne peuvent pas" in r.text and db.session.get(Role, P) is not None


def test_nav_link_only_for_admin(client, as_role):
    as_role(A)
    assert 'href="/roles"' in client.get("/").text
    as_role(P)
    assert 'href="/roles"' not in client.get("/").text


# --- Attribution d'un rôle personnalisé ----------------------------------------

def test_custom_role_grants_exactly_its_rights(client, users):
    add_role("regie", "Régie", ["inventory.view", "myloans.view"])
    user = person_with("regie", "regie@ohn.test")
    login(client, user)
    assert client.get("/objets").status_code == 200
    assert client.get("/rapports").status_code == 403
    assert client.get("/prets").status_code == 403
    assert "Régie" in client.get("/").text


def test_admin_assigns_custom_role(client, as_role, users):
    as_role(A)
    add_role("regie", "Régie", ["inventory.view"])
    r = client.get(f"/utilisateurs/{users[E].id}/modifier")
    assert '<option value="regie">Régie</option>' in r.text
    client.post(f"/utilisateurs/{users[E].id}/modifier", data={
        "first_name": "E", "last_name": "T", "email": users[E].email, "member_status": "", "role": "regie",
        "active": "y"})
    db.session.refresh(users[E])
    assert users[E].role == "regie"
    assert "Régie" in client.get("/utilisateurs?role=regie").text


def test_user_manager_cannot_grant_or_touch_admin(client, users):
    add_role("secretariat", "Secrétariat", ["user.manage", "borrower.create", "myloans.view"])
    manager = person_with("secretariat", "secr@ohn.test")
    login(client, manager)
    r = client.get(f"/utilisateurs/{users[E].id}/modifier")
    assert 'value="admin"' not in r.text
    r = client.post(f"/utilisateurs/{users[E].id}/modifier", data={
        "first_name": "E", "last_name": "T", "email": users[E].email, "member_status": "", "role": A,
        "active": "y"})
    db.session.refresh(users[E])
    assert users[E].role == E
    assert client.get(f"/utilisateurs/{users[A].id}/modifier").status_code == 403
    assert client.get("/roles").status_code == 403
    assert f'/utilisateurs/{users[A].id}/modifier' not in client.get("/utilisateurs").text


def test_user_manager_cannot_lock_self_out(client, users):
    add_role("secretariat", "Secrétariat", ["user.manage", "borrower.create", "myloans.view"])
    manager = person_with("secretariat", "secr@ohn.test")
    login(client, manager)
    r = client.post(f"/utilisateurs/{manager.id}/modifier", data={
        "first_name": "S", "last_name": "T", "email": manager.email, "member_status": "", "role": E,
        "active": "y"}, follow_redirects=True)
    assert "vos propres droits" in r.text
    db.session.refresh(manager)
    assert manager.role == "secretariat"


# --- Prêts : garant -------------------------------------------------------------

def test_custom_role_with_guarantee_can_be_guarantor(client, as_role, users):
    add_role("chef", "Chef de pupitre", ["loan.guarantee", "myloans.view"])
    chef = person_with("chef", "chef@ohn.test")
    as_role(A)
    r = client.get("/prets/nouveau")
    assert f'<option value="{chef.id}">' in r.text
    assert f'<option value="{users[E].id}">' in r.text  # parmi les emprunteurs, pas les garants
    guarantor_select = r.text.split('name="guarantor_id"')[1].split("</select>")[0]
    assert f'value="{chef.id}"' in guarantor_select and f'value="{users[E].id}"' not in guarantor_select


def test_mine_shows_guarantor_section_by_right(client, users):
    add_role("chef", "Chef de pupitre", ["loan.guarantee", "myloans.view"])
    login(client, person_with("chef", "chef@ohn.test"))
    assert "dont je suis garant" in client.get("/mes-prets").text
    client.post("/deconnexion")
    login(client, users[E])
    assert "dont je suis garant" not in client.get("/mes-prets").text
