"""Tableau de bord à la carte : blocs affichés et ordre choisis par chacun."""
import re

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import LoanStatus, Role, User
from app.services import dashboard

from .conftest import login
from .test_loans_routes import gear, make_loan  # noqa: F401 (fixture)

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR
ALL = list(dashboard.WIDGETS)
HEADINGS = {"stats": 'class="stats"', "pending": "Demandes à valider", "overdue": "Prêts en retard",
            "upcoming": "Sorties des 7 prochains jours", "maintenances": "Entretiens à prévoir",
            "low_stock": "Stocks bas"}


def shown_order(html):
    """Blocs du tableau de bord dans l'ordre de la page."""
    positions = {key: html.find(marker) for key, marker in HEADINGS.items() if marker in html}
    return sorted(positions, key=positions.get)


def post(client, order, shown, move=None):
    data = {"order": order, "shown": shown}
    if move:
        data["move"] = move
    return client.post("/tableau-de-bord", data=data)


def reload(user):
    return db.session.get(User, user.id)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

def test_available_depends_on_permissions(users):
    assert dashboard.available(users[A]) == ALL
    assert dashboard.available(users[P]) == [k for k in ALL if k != "pending"]  # pas de loan.validate


def test_default_layout(users):
    assert dashboard.layout(users[A]) == [(k, True) for k in ALL]
    assert dashboard.visible(users[A]) == ALL


def test_save_default_stores_nothing(users):
    dashboard.save(users[A], ALL, ALL)
    assert users[A].dashboard is None


def test_save_order_and_hidden(users):
    dashboard.save(users[A], ["low_stock", "stats"], ["low_stock", "overdue"])
    assert users[A].dashboard["order"][:2] == ["low_stock", "stats"]
    assert dashboard.visible(users[A]) == ["low_stock", "overdue"]


def test_save_ignores_unknown_forbidden_and_duplicate_keys(users):
    dashboard.save(users[P], ["pending", "pirate", "overdue", "overdue"], ["pending", "pirate", "overdue"])
    assert dashboard.visible(users[P]) == ["overdue"]
    assert "pending" not in users[P].dashboard["order"] and "pirate" not in users[P].dashboard["order"]


def test_layout_tolerates_corrupt_storage(users):
    for value in ([], "texte", {"order": "stats", "hidden": None}, {"order": [1, None], "hidden": [2]}):
        users[A].dashboard = value
        assert [k for k, _ in dashboard.layout(users[A])] == ALL


def test_new_widget_is_appended_visible(users):
    users[A].dashboard = {"order": ["overdue", "stats"], "hidden": ["stats"]}
    layout = dashboard.layout(users[A])
    assert layout[:2] == [("overdue", True), ("stats", False)]
    assert [k for k, _ in layout[2:]] == [k for k in ALL if k not in ("overdue", "stats")]
    assert all(shown for _, shown in layout[2:])


def test_permission_gained_later_shows_widget(users):
    dashboard.save(users[P], ["overdue"], ["overdue"])
    users[P].role = A
    assert "pending" in dashboard.visible(users[P])  # nouveau droit : bloc ajouté, affiché


@pytest.mark.parametrize("key,delta,expected", [
    ("b", -1, ["b", "a", "c"]), ("b", 1, ["a", "c", "b"]), ("a", -1, ["a", "b", "c"]),
    ("c", 1, ["a", "b", "c"]), ("z", 1, ["a", "b", "c"]), ("a", 5, ["b", "c", "a"]),
])
def test_move(key, delta, expected):
    assert dashboard.move(["a", "b", "c"], key, delta) == expected


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def test_default_dashboard_unchanged(client, as_role):
    as_role(A)
    html = client.get("/").get_data(as_text=True)
    assert shown_order(html) == ["stats", "pending", "overdue", "upcoming", "maintenances", "low_stock"]
    assert 'href="/tableau-de-bord"' in html


def test_prof_dashboard_has_no_pending(client, as_role):
    as_role(P)
    assert "Demandes à valider" not in client.get("/").get_data(as_text=True)


def test_customize_page_lists_widgets(client, as_role):
    as_role(P)
    html = client.get("/tableau-de-bord").get_data(as_text=True)
    for key in ALL:
        assert (f'value="{key}"' in html) == (key != "pending")
    assert html.count('type="checkbox"') == len(ALL) - 1
    assert "affiché seulement s&#39;il y en a" in html
    # premier « monter » et dernier « descendre » désactivés
    assert re.search(r'value="stats:up"[^>]*disabled', html)
    assert re.search(r'value="low_stock:down"[^>]*disabled', html)


def test_hide_widgets(client, as_role):
    user = as_role(A)
    r = post(client, ALL, ["overdue", "low_stock"])
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert reload(user).dashboard["hidden"] == ["stats", "to_confirm", "pending", "upcoming", "maintenances"]
    html = client.get("/").get_data(as_text=True)
    assert shown_order(html) == ["overdue", "low_stock"]
    assert "Tableau de bord enregistré." in html


def test_move_widget_up_and_down(client, as_role):
    user = as_role(A)
    r = post(client, ALL, ALL, move="low_stock:up")
    assert r.status_code == 302 and r.headers["Location"].endswith("/tableau-de-bord#w-low_stock")
    assert dashboard.layout(reload(user))[-2:] == [("low_stock", True), ("maintenances", True)]
    post(client, [k for k, _ in dashboard.layout(reload(user))], ALL, move="stats:down")
    assert [k for k, _ in dashboard.layout(reload(user))][:3] == ["to_confirm", "stats", "pending"]
    post(client, [k for k, _ in dashboard.layout(reload(user))], ALL, move="stats:down")
    assert shown_order(client.get("/").get_data(as_text=True)) == [
        "pending", "stats", "overdue", "upcoming", "low_stock", "maintenances"]


def test_move_keeps_unsaved_checkboxes(client, as_role):
    user = as_role(A)
    post(client, ALL, ["stats"], move="stats:down")
    assert dashboard.visible(reload(user)) == ["stats"]


@pytest.mark.parametrize("move", ["stats:sideways", "pirate:up", "", ":up"])
def test_odd_move_values_just_save(client, as_role, move):
    user = as_role(A)
    post(client, ALL, ["overdue"], move=move)
    assert dashboard.visible(reload(user)) == ["overdue"]


def test_all_hidden_shows_hint(client, as_role):
    as_role(A)
    post(client, ALL, [])
    html = client.get("/").get_data(as_text=True)
    assert "Aucun bloc à afficher." in html and shown_order(html) == []


def test_reset(client, as_role):
    user = as_role(A)
    post(client, ALL, ["overdue"])
    r = client.post("/tableau-de-bord/reinitialiser", follow_redirects=True)
    assert "Tableau de bord remis par défaut." in r.get_data(as_text=True)
    assert reload(user).dashboard is None
    assert shown_order(r.get_data(as_text=True))[0] == "stats"


def test_layout_is_per_user(client, users):
    login(client, users[A])
    post(client, ALL, ["overdue"])
    client.post("/deconnexion")
    login(client, users[P])
    assert "stats" in shown_order(client.get("/").get_data(as_text=True))


def test_prof_cannot_add_pending_by_posting_it(client, as_role):
    user = as_role(P)
    post(client, ["pending"] + ALL, ALL)
    assert "pending" not in dashboard.visible(reload(user))
    assert "Demandes à valider" not in client.get("/").get_data(as_text=True)


def test_borrower_cannot_customize(client, as_role):
    as_role(E)
    assert client.get("/tableau-de-bord").status_code == 403
    assert post(client, ALL, []).status_code == 403
    assert client.post("/tableau-de-bord/reinitialiser").status_code == 403


def test_anonymous_redirected(client):
    r = client.get("/tableau-de-bord")
    assert r.status_code == 302 and "/connexion" in r.headers["Location"]


# ---------------------------------------------------------------------------
# Contenu et calcul
# ---------------------------------------------------------------------------

def test_hidden_widget_data_not_shown(client, as_role, users, gear):
    loan = make_loan(users, gear["sax"])  # demande à valider
    as_role(A)
    assert f"/prets/{loan.id}" in client.get("/").get_data(as_text=True)
    post(client, ALL, [k for k in ALL if k != "pending"])
    assert f"/prets/{loan.id}" not in client.get("/").get_data(as_text=True)


def test_to_confirm_only_when_non_empty(client, as_role, users, gear):
    as_role(P)
    assert "Demandes à confirmer comme garant" not in client.get("/").get_data(as_text=True)
    loan = make_loan(users, gear["sax"])
    loan.awaiting_guarantor = True
    db.session.commit()
    html = client.get("/").get_data(as_text=True)
    assert "Demandes à confirmer comme garant" in html and f"/prets/{loan.id}" in html


def test_hidden_widgets_are_not_queried(client, as_role, app):
    as_role(A)
    post(client, ALL, ["stats"])
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        client.get("/")
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert not any("FROM loan" in s for s in statements)
    assert any("GROUP BY item.status" in s for s in statements)


def test_overdue_widget_still_counts(client, as_role, users, gear):
    from datetime import datetime, timedelta
    loan = make_loan(users, gear["sax"], status=LoanStatus.EN_COURS,
                     start=datetime.now() - timedelta(days=5), end=datetime.now() - timedelta(days=1))
    as_role(A)
    post(client, ALL, ["overdue"])
    html = client.get("/").get_data(as_text=True)
    assert f"/prets/{loan.id}" in html and shown_order(html) == ["overdue"]


@pytest.mark.parametrize("keys,wide", [
    (["maintenances", "low_stock"], set()),
    (["overdue", "maintenances", "low_stock"], {"overdue"}),
    (["maintenances", "overdue", "low_stock"], {"maintenances", "overdue", "low_stock"}),
    (["low_stock"], {"low_stock"}),
    (["stats", "low_stock", "maintenances", "upcoming"], {"stats", "upcoming"}),
    ([], set()),
])
def test_full_width(keys, wide):
    assert dashboard.full_width(keys) == wide


def half_cards(html):
    """Blocs demi-largeur affichés sur toute la largeur (classe span-2)."""
    return {name for name, marker in (("maintenances", "Entretiens à prévoir"), ("low_stock", "Stocks bas"))
            if re.search(rf'<section class="card span-2">\s*<h2>(?:(?!</h2>).)*{marker}', html, re.S)}


def test_lone_half_card_takes_full_width(client, as_role):
    as_role(A)
    assert half_cards(client.get("/").get_data(as_text=True)) == set()  # côte à côte par défaut
    order = ["stats", "to_confirm", "pending", "maintenances", "overdue", "upcoming", "low_stock"]
    post(client, order, ALL)
    assert half_cards(client.get("/").get_data(as_text=True)) == {"maintenances", "low_stock"}
    post(client, ALL, [k for k in ALL if k != "maintenances"])
    assert half_cards(client.get("/").get_data(as_text=True)) == {"low_stock"}


def test_empty_to_confirm_does_not_separate_half_cards(client, as_role):
    as_role(P)
    post(client, ["maintenances", "to_confirm", "low_stock"], ALL)
    assert half_cards(client.get("/").get_data(as_text=True)) == set()


def test_only_empty_to_confirm_shows_hint(client, as_role):
    as_role(P)
    post(client, ALL, ["to_confirm"])
    assert "Aucun bloc à afficher." in client.get("/").get_data(as_text=True)
