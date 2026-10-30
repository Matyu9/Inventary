from app.models import Role

from .conftest import login, make_user


def test_dashboard_requires_login(client):
    r = client.get("/")
    assert r.status_code == 302 and "/connexion" in r.headers["Location"]


def test_login_and_dashboard_by_role(client, users):
    login(client, users[Role.ADMIN])
    r = client.get("/")
    assert r.status_code == 200 and "Demandes à valider" in r.text

    client.post("/deconnexion")
    login(client, users[Role.PROF])
    r = client.get("/")
    assert "Tableau de bord" in r.text and "Demandes à valider" not in r.text

    client.post("/deconnexion")
    login(client, users[Role.EMPRUNTEUR])
    assert "Mes prêts en cours" in client.get("/").text


def test_inactive_account_cannot_login(client, app):
    user = make_user(Role.EMPRUNTEUR, email="fiche@ohn.test", active=False)
    r = login(client, user)
    assert "incorrect" in r.text
    assert client.get("/").status_code == 302


def test_wrong_password(client, users):
    r = login(client, users[Role.ADMIN], password="faux")
    assert "incorrect" in r.text


def test_open_redirect_blocked(client, users):
    r = client.post("/connexion?next=https://evil.example", data={
        "email": users[Role.ADMIN].email, "password": "motdepasse"})
    assert r.headers["Location"] == "/"
