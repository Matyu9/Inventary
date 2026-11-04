from app.extensions import db
from app.models import Activity, Role
from app.permissions import DEFAULT_PERMISSIONS
from app.services.activity import log

from .conftest import login, make_user

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


def actions():
    return [a.action for a in db.session.scalars(db.select(Activity).order_by(Activity.id))]


def test_login_logout_are_logged(client, users):
    login(client, users[P])
    client.post("/deconnexion")
    entries = db.session.scalars(db.select(Activity).order_by(Activity.id)).all()
    assert [a.action for a in entries] == ["auth.login", "auth.logout"]
    assert all(a.user_id == users[P].id for a in entries)
    assert "Connexion de Prof Test" in entries[0].message and "Déconnexion de Prof Test" in entries[1].message


def test_failed_login_is_logged_without_author(client, users):
    login(client, users[P], password="faux")
    entry = db.session.scalar(db.select(Activity))
    assert entry.action == "auth.failed" and entry.user_id is None and entry.target_user_id == users[P].id
    assert users[P].email in entry.message


def test_failed_login_unknown_email(client, users):
    client.post("/connexion", data={"email": "Inconnu@ohn.test", "password": "x"})
    entry = db.session.scalar(db.select(Activity))
    assert entry.action == "auth.failed" and entry.target_user_id is None and "inconnu@ohn.test" in entry.message


def test_throttled_attempts_are_not_logged(client, users):
    for _ in range(12):
        login(client, users[P], password="faux")
    assert actions().count("auth.failed") == 8


def test_inactive_account_login_is_a_failure(client, app):
    user = make_user(E, email="fiche@ohn.test", active=False)
    login(client, user)
    assert actions() == ["auth.failed"]


def test_journal_is_admin_only_by_default(client, users):
    assert "activity.view" in DEFAULT_PERMISSIONS[A]
    assert "activity.view" not in DEFAULT_PERMISSIONS[P] | DEFAULT_PERMISSIONS[E]
    for role in (P, E):
        client.post("/deconnexion")
        login(client, users[role])
        assert client.get("/journal").status_code == 403
        assert 'href="/journal"' not in client.get("/").text
    client.post("/deconnexion")
    assert client.get("/journal").status_code == 302


def test_journal_lists_everything(client, users):
    login(client, users[P], password="faux")
    login(client, users[P])
    client.post("/deconnexion")
    login(client, users[A])
    r = client.get("/journal")
    assert r.status_code == 200 and 'href="/journal"' in r.text
    for text in ("Échec de connexion pour prof@ohn.test", "Connexion de Prof Test", "Déconnexion de Prof Test",
                 "Connexion de Admin Test"):
        assert text in r.text


def test_journal_newest_first(client, users):
    login(client, users[A])
    log("item.create", "Premier évènement", user=users[P])
    log("item.create", "Second évènement", user=users[P])
    db.session.commit()
    r = client.get("/journal")
    assert r.text.index("Second évènement") < r.text.index("Premier évènement")


def test_journal_filters(client, users):
    login(client, users[A])
    log("loan.validate", "Prêt validé ici", user=users[A])
    log("item.update", "Objet modifié par le prof", user=users[P])
    log("user.role", "Rôle changé", user=users[A], target=users[E])
    db.session.commit()

    r = client.get("/journal?type=prets")
    assert "Prêt validé ici" in r.text and "Objet modifié" not in r.text and "Connexion de" not in r.text
    r = client.get("/journal?type=connexions")
    assert "Connexion de Admin Test" in r.text and "Prêt validé" not in r.text
    r = client.get("/journal?type=inconnu")
    assert "Prêt validé ici" in r.text and "Objet modifié" in r.text

    r = client.get(f"/journal?personne={users[P].id}")
    assert "Objet modifié par le prof" in r.text and "Prêt validé" not in r.text and "voir tout le monde" in r.text
    r = client.get(f"/journal?personne={users[E].id}")  # concerné sans être l'auteur
    assert "Rôle changé" in r.text and "Objet modifié" not in r.text
    assert "Prêt validé ici" in client.get("/journal?personne=9999").text  # personne inconnue : ignorée

    r = client.get("/journal?q=OBJET MODIFIE")  # sans casse ni accents
    assert "Objet modifié par le prof" in r.text and "Prêt validé" not in r.text
    assert "Aucune action ne correspond" in client.get("/journal?q=introuvable").text
    r = client.get(f"/journal?type=materiel&personne={users[A].id}")
    assert "Aucune action ne correspond" in r.text


def test_journal_links_to_objects(client, users):
    login(client, users[A])
    log("user.role", "Rôle changé", user=users[A], target=users[E])
    db.session.commit()
    r = client.get("/journal")
    assert f'href="/journal?personne={users[A].id}"' in r.text
    assert f"/utilisateurs/{users[E].id}" in r.text


def test_journal_pagination(client, users):
    from app.blueprints.journal import PER_PAGE
    login(client, users[A])
    for i in range(PER_PAGE + 5):
        log("item.update", f"Modif n°{i:03d}", user=users[P])
    db.session.commit()
    r = client.get("/journal?type=materiel")
    assert "Page 1 / 2" in r.text and "Plus ancien" in r.text and "Modif n°000" not in r.text
    r = client.get("/journal?type=materiel&page=2")
    assert "Modif n°000" in r.text and "Plus récent" in r.text and "type=materiel" in r.text
    assert client.get("/journal?page=0").status_code == 200
    assert "Aucune action" in client.get("/journal?page=999").text


def test_custom_role_can_get_journal(client, users, app):
    from app.models import Role as RoleModel
    db.session.add(RoleModel(key="secretariat", name="Secrétariat", permissions=["activity.view"]))
    db.session.commit()
    user = make_user("secretariat", email="secr@ohn.test")
    login(client, user)
    assert client.get("/journal").status_code == 200
