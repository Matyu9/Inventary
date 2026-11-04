"""API en lecture seule (/api/v1) et jetons d'accès (Mon compte, fiche personne)."""
import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import (Activity, ApiToken, Category, ConsumptionMode, Item, ItemStatus, Kit, LoanStatus, LoanType,
                        Location, Role, Tag, Tracking)
from app.services import api_tokens

from .conftest import make_user
from .test_loans_routes import gear, make_loan, soon  # noqa: F401  (fixture gear)

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


def token_for(user, days=None):
    raw, _token = api_tokens.issue(user, "Test", days)
    db.session.commit()
    return raw


@pytest.fixture
def api(client, users):
    """api("prof").get("/items") : appels authentifiés avec un jeton du compte du rôle."""
    class Caller:
        def __init__(self, raw):
            self.raw = raw

        def get(self, path, **kw):
            return client.get("/api/v1" + path, headers={"Authorization": f"Bearer {self.raw}"}, **kw)

    def _for(role_or_user):
        user = users[role_or_user] if isinstance(role_or_user, str) else role_or_user
        return Caller(token_for(user))
    return _for


def grant_borrowers_api():
    role = db.session.get(Role, E) or Role(key=E, name="Emprunteur", permissions=[])
    from app.permissions import DEFAULT_PERMISSIONS
    role.permissions = sorted(DEFAULT_PERMISSIONS[E] | {"api.use"})
    db.session.add(role)
    db.session.commit()


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------

def test_missing_token_is_401_json(client, users):
    res = client.get("/api/v1/me")
    assert res.status_code == 401
    assert res.is_json and res.json["error"]["code"] == 401
    assert "jeton" in res.json["error"]["message"].lower()
    assert res.headers["WWW-Authenticate"].lower().startswith("bearer")


@pytest.mark.parametrize("header", ["Bearer ohn_faux", "Bearer pas-un-jeton", "Basic abc", "Bearer", ""])
def test_invalid_tokens_are_refused(client, users, header):
    token_for(users[A])
    assert client.get("/api/v1/me", headers={"Authorization": header}).status_code == 401


def test_session_cookie_is_not_enough(client, as_role):
    as_role(A)
    assert client.get("/api/v1/me").status_code == 401


def test_me(api, users):
    res = api(P).get("/me")
    assert res.status_code == 200
    assert res.json["id"] == users[P].id and res.json["role"] == P
    assert "inventory.view" in res.json["permissions"] and "user.manage" not in res.json["permissions"]
    assert res.json["token"]["name"] == "Test"
    assert res.headers["Cache-Control"] == "no-store"


def test_only_the_digest_is_stored(app, users):
    raw = token_for(users[A])
    token = db.session.scalar(db.select(ApiToken))
    assert raw.startswith("ohn_") and token.id != raw and raw not in (token.id, token.prefix)
    assert raw.startswith(token.prefix)


def test_expired_token(client, users):
    raw = token_for(users[A], days=30)
    db.session.scalar(db.select(ApiToken)).expires_at = datetime.now() - timedelta(seconds=1)
    db.session.commit()
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


@pytest.mark.parametrize("change", ["inactive", "archived"])
def test_owner_without_access_cannot_use_token(client, users, change):
    raw = token_for(users[P])
    if change == "inactive":
        users[P].active = False
    else:
        users[P].archived_at = datetime.now()
    db.session.commit()
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_role_without_api_right_is_403(client, users):
    raw = token_for(users[P])
    db.session.add(Role(key=P, name="Professeur", permissions=["inventory.view"]))
    db.session.commit()
    res = client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"})
    assert res.status_code == 403 and "API" in res.json["error"]["message"]


def test_borrowers_have_no_api_by_default(api):
    assert api(E).get("/me").status_code == 403


def test_last_used_is_recorded_sparingly(api, users):
    caller = api(A)
    caller.get("/me")
    token = db.session.scalar(db.select(ApiToken))
    first = token.last_used_at
    assert first is not None
    caller.get("/me")
    db.session.refresh(token)
    assert token.last_used_at == first
    token.last_used_at = datetime.now() - timedelta(minutes=10)
    db.session.commit()
    caller.get("/me")
    db.session.refresh(token)
    assert token.last_used_at > first


def test_rate_limit(api, monkeypatch):
    from app.blueprints import api as api_bp
    monkeypatch.setattr(api_bp._rate, "limit", 3)
    caller = api(A)
    assert [caller.get("/me").status_code for _ in range(4)] == [200, 200, 200, 429]
    res = caller.get("/me")
    assert res.json["error"]["code"] == 429 and res.headers["Retry-After"] == "60"


def test_read_only(client, users):
    raw = token_for(users[A])
    headers = {"Authorization": f"Bearer {raw}"}
    for method in (client.post, client.put, client.patch, client.delete):
        res = method("/api/v1/items", headers=headers)
        assert res.status_code == 405 and res.is_json


def test_unknown_route_is_json_404(api):
    res = api(A).get("/nimporte-quoi")
    assert res.status_code == 404 and res.json["error"]["message"] == "Page introuvable"


def test_html_errors_unchanged_outside_api(client):
    res = client.get("/page-qui-nexiste-pas")
    assert res.status_code == 404 and not res.is_json


# ---------------------------------------------------------------------------
# Inventaire
# ---------------------------------------------------------------------------

def test_items_list_and_pagination(api, gear):
    res = api(P).get("/items?per_page=2")
    assert res.status_code == 200
    assert res.json["meta"] == {"page": 1, "per_page": 2, "total": 5, "pages": 3}
    assert [i["code"] for i in res.json["data"]] == ["OHN-0001", "OHN-0002"]
    first = res.json["data"][0]
    assert first["category"]["name"] == "Bois" and first["status"] == "disponible"
    assert first["url"].endswith(f"/objets/{gear['sax'].id}")
    assert "quantity" not in first
    assert api(P).get("/items?page=3&per_page=2").json["data"][0]["code"] == "OHN-0005"
    assert api(P).get("/items?page=99").json["data"] == []


def test_items_per_page_is_capped(api, gear):
    assert api(P).get("/items?per_page=5000").json["meta"]["per_page"] == 100


def test_items_filters(api, gear):
    caller = api(P)
    assert [i["code"] for i in caller.get("/items?q=flute").json["data"]] == ["OHN-0002"]
    assert [i["code"] for i in caller.get("/items?status=réformé").json["data"]] == ["OHN-0003"]
    assert [i["code"] for i in caller.get(f"/items?kit={gear['kit'].id}").json["data"]] == ["OHN-0005"]
    quantity = caller.get("/items?tracking=quantite").json["data"]
    assert [i["code"] for i in quantity] == ["OHN-0004"] and quantity[0]["quantity"] == 8


def test_items_location_filter_includes_sublocations(api, gear):
    local = Location(name="Local")
    armoire = Location(name="Armoire", parent=local)
    gear["sax"].location = armoire
    db.session.add_all([local, armoire])
    db.session.commit()
    data = api(P).get(f"/items?location={local.id}").json["data"]
    assert [i["code"] for i in data] == ["OHN-0001"]
    assert data[0]["location"]["path"] == "Local > Armoire"


def test_bad_integer_parameter_is_400(api, gear):
    res = api(P).get("/items?page=abc")
    assert res.status_code == 400 and "page" in res.json["error"]["message"]


def test_item_by_code_or_id(api, gear):
    caller = api(P)
    by_code = caller.get("/items/ohn-0001")
    assert by_code.status_code == 200 and by_code.json["name"] == "Saxophone"
    assert caller.get(f"/items/{gear['sax'].id}").json["code"] == "OHN-0001"
    assert caller.get("/items/OHN-9999").status_code == 404
    assert caller.get("/items/99999999999999999999999").status_code == 404


def test_item_detail(api, gear, users):
    sax = gear["sax"]
    sax.purchase_price = 1200
    sax.tags = [Tag(name="Prêt élève")]
    db.session.commit()
    loan = make_loan(users, sax, status=LoanStatus.VALIDE)
    make_loan(users, gear["flute"], status=LoanStatus.VALIDE)
    data = api(P).get("/items/OHN-0001").json
    assert data["purchase_price"] == 1200.0 and data["tags"] == ["Prêt élève"]
    assert [entry["id"] for entry in data["loans"]] == [loan.id]
    assert data["loans"][0]["borrower"]["name"] == users[E].full_name


def test_item_detail_shows_kit_loans(api, gear, users):
    loan = make_loan(users, kits=[gear["kit"]], status=LoanStatus.EN_COURS)
    assert [entry["id"] for entry in api(P).get("/items/OHN-0005").json["loans"]] == [loan.id]


def test_item_in_trash_is_hidden(api, gear):
    gear["sax"].deleted_at = datetime.now()
    db.session.commit()
    caller = api(P)
    assert caller.get("/items/OHN-0001").status_code == 404
    assert "OHN-0001" not in [i["code"] for i in caller.get("/items").json["data"]]


def test_inventory_requires_inventory_view(api, gear):
    grant_borrowers_api()
    caller = api(E)
    for path in ("/items", "/items/OHN-0001", "/kits", "/consumables", "/locations", "/categories", "/tags",
                 "/availability", "/stats"):
        assert caller.get(path).status_code == 403, path


def test_kits(api, gear, users):
    caller = api(P)
    data = caller.get("/kits").json["data"]
    assert data[0]["name"] == "Kit sono" and data[0]["item_count"] == 1 and data[0]["status"] == "disponible"
    assert caller.get("/kits?q=SONO").json["meta"]["total"] == 1
    loan = make_loan(users, kits=[gear["kit"]], status=LoanStatus.VALIDE)
    detail = caller.get(f"/kits/{gear['kit'].id}").json
    assert [i["code"] for i in detail["items"]] == ["OHN-0005"]
    assert [entry["id"] for entry in detail["loans"]] == [loan.id]
    assert caller.get("/kits/999").status_code == 404


def test_consumables_and_low_stock(api, gear):
    anches = Item(code="OHN-0010", name="Anches", category=gear["sax"].category, tracking=Tracking.QUANTITE,
                  quantity=2, low_stock_threshold=5, consumption_mode=ConsumptionMode.CONSOMME, unit="boîtes")
    db.session.add(anches)
    db.session.commit()
    caller = api(P)
    assert [i["code"] for i in caller.get("/consumables").json["data"]] == ["OHN-0010", "OHN-0004"]
    low = caller.get("/consumables?low_stock=1").json["data"]
    assert [i["code"] for i in low] == ["OHN-0010"] and low[0]["low_stock"] is True and low[0]["unit"] == "boîtes"
    assert [i["code"] for i in caller.get("/items?low_stock=oui").json["data"]] == ["OHN-0010"]


def test_locations_categories_tags(api, gear):
    local = Location(name="Local")
    armoire = Location(name="Armoire", parent=local)
    gear["sax"].location = armoire
    gear["sax"].tags = [Tag(name="Concert")]
    db.session.add_all([local, armoire, Category(name="Percussions")])
    db.session.commit()
    caller = api(P)
    locations = caller.get("/locations").json["data"]
    assert [(loc["path"], loc["item_count"]) for loc in locations] == [("Local", 0), ("Local > Armoire", 1)]
    assert locations[1]["parent_id"] == local.id
    categories = {c["name"]: c["item_count"] for c in caller.get("/categories").json["data"]}
    assert categories == {"Bois": 5, "Percussions": 0}
    assert caller.get("/tags").json["data"] == [{"id": gear["sax"].tags[0].id, "name": "Concert", "item_count": 1}]


# ---------------------------------------------------------------------------
# Prêts
# ---------------------------------------------------------------------------

def test_loans_for_staff(api, gear, users):
    other = make_user(E, email="autre@ohn.test")
    active = make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    theirs = make_loan(users, gear["flute"], status=LoanStatus.DEMANDE, borrower=other)
    returned = make_loan(users, gear["cable"], status=LoanStatus.RENDU)
    caller = api(P)
    assert {loan["id"] for loan in caller.get("/loans").json["data"]} == {active.id, theirs.id}
    assert [loan["id"] for loan in caller.get("/loans?view=historique").json["data"]] == [returned.id]
    assert caller.get("/loans?view=tous").json["meta"]["total"] == 3
    assert [loan["id"] for loan in caller.get(f"/loans?borrower={other.id}").json["data"]] == [theirs.id]
    other.last_name = "Lefèvre"
    db.session.commit()
    assert [loan["id"] for loan in caller.get("/loans?q=lefevre").json["data"]] == [theirs.id]
    detail = caller.get(f"/loans/{active.id}").json
    assert detail["lines"][0]["item"]["code"] == "OHN-0001"
    assert detail["borrower"]["id"] == users[E].id and detail["guarantor"]["id"] == users[P].id
    assert detail["url"].endswith(f"/prets/{active.id}")


def test_loan_views_and_type(api, gear, users):
    late = make_loan(users, gear["sax"], status=LoanStatus.EN_COURS, start=soon(-5), end=soon(-1))
    garde = make_loan(users, gear["flute"], status=LoanStatus.EN_COURS, loan_type=LoanType.GARDE)
    caller = api(A)
    retard = caller.get("/loans?view=retard").json["data"]
    assert [loan["id"] for loan in retard] == [late.id] and retard[0]["overdue"] is True
    assert [loan["id"] for loan in caller.get("/loans?type=garde").json["data"]] == [garde.id]
    assert caller.get("/loans?view=nope").status_code == 400
    assert caller.get("/loans?type=nope").status_code == 400


def test_kit_loan_lines(api, gear, users):
    loan = make_loan(users, kits=[gear["kit"]], status=LoanStatus.VALIDE)
    line = api(A).get(f"/loans/{loan.id}").json["lines"][0]
    assert line["kit"]["name"] == "Kit sono" and [i["code"] for i in line["kit"]["items"]] == ["OHN-0005"]


def test_borrower_only_sees_own_loans(api, gear, users):
    grant_borrowers_api()
    other = make_user(E, email="autre@ohn.test")
    mine = make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    theirs = make_loan(users, gear["flute"], status=LoanStatus.VALIDE, borrower=other)
    caller = api(E)
    assert [loan["id"] for loan in caller.get("/loans").json["data"]] == [mine.id]
    # Le filtre par emprunteur est réservé à qui voit tous les prêts : ignoré ici.
    assert [loan["id"] for loan in caller.get(f"/loans?borrower={other.id}").json["data"]] == [mine.id]
    assert caller.get(f"/loans/{mine.id}").status_code == 200
    assert caller.get(f"/loans/{theirs.id}").status_code == 403
    assert caller.get("/loans/9999").status_code == 404


def test_mine_filter(api, gear, users):
    as_guarantor = make_loan(users, gear["sax"], status=LoanStatus.VALIDE)
    make_loan(users, gear["flute"], status=LoanStatus.VALIDE, guarantor=users[A])
    assert [loan["id"] for loan in api(P).get("/loans?mine=1").json["data"]] == [as_guarantor.id]


def test_loans_need_some_loan_right(client, users):
    db.session.add(Role(key=E, name="Emprunteur", permissions=["api.use"]))
    db.session.commit()
    raw = token_for(users[E])
    assert client.get("/api/v1/loans", headers={"Authorization": f"Bearer {raw}"}).status_code == 403


# ---------------------------------------------------------------------------
# Disponibilité et chiffres clés
# ---------------------------------------------------------------------------

def codes(res):
    return [i["code"] for i in res.json["items"]]


def test_availability(api, gear, users):
    make_loan(users, gear["sax"], status=LoanStatus.VALIDE, start=soon(1), end=soon(2))
    make_loan(users, gear["flute"], status=LoanStatus.DEMANDE, start=soon(1), end=soon(2))  # ne bloque rien
    cables = make_loan(users, gear["cable"], status=LoanStatus.VALIDE, start=soon(1), end=soon(2))
    cables.lines[0].quantity = 3
    db.session.commit()
    day = soon(1).date().isoformat()
    res = api(P).get(f"/availability?start={day}")
    assert res.status_code == 200
    assert codes(res) == ["OHN-0002", "OHN-0004"]  # ni le sax pris, ni le cornet réformé, ni l'enceinte (kit)
    assert next(i for i in res.json["items"] if i["code"] == "OHN-0004")["available"] == 5
    assert [k["name"] for k in res.json["kits"]] == ["Kit sono"]
    later = (soon(1) + timedelta(days=10)).date().isoformat()
    assert "OHN-0001" in codes(api(P).get(f"/availability?start={later}&end={later}"))


def test_availability_kit_taken(api, gear, users):
    make_loan(users, kits=[gear["kit"]], status=LoanStatus.EN_COURS, start=soon(-1), end=soon(3))
    res = api(P).get("/availability")
    assert res.json["kits"] == []


def test_availability_filters_and_maintenance(api, gear):
    gear["flute"].status = ItemStatus.EN_ENTRETIEN
    db.session.commit()
    caller = api(P)
    assert "OHN-0002" not in codes(caller.get("/availability"))
    assert codes(caller.get("/availability?q=saxo")) == ["OHN-0001"]
    assert caller.get("/availability?q=saxo").json["kits"] == []
    assert [k["name"] for k in caller.get("/availability?q=sono").json["kits"]] == ["Kit sono"]


@pytest.mark.parametrize("query", ["start=demain", "start=2026-12-14&end=2026-12-13", "end=2020-01-01T10:00"])
def test_availability_bad_periods(api, gear, query):
    res = api(P).get("/availability?" + query)
    assert res.status_code == 400 and res.is_json


def test_availability_date_only_end_covers_the_day(api, gear):
    data = api(P).get("/availability?start=2026-12-14&end=2026-12-14").json
    assert data["start"] == "2026-12-14T00:00:00" and data["end"] == "2026-12-15T00:00:00"


def test_stats(api, gear, users):
    make_loan(users, gear["sax"], status=LoanStatus.EN_COURS, start=soon(-5), end=soon(-1))
    make_loan(users, gear["flute"], status=LoanStatus.DEMANDE)
    data = api(P).get("/stats").json
    assert data["items"]["total"] == 5 and data["items"]["by_status"]["réformé"] == 1
    assert data["kits"] == 1 and data["low_stock"] == 0
    assert data["loans"]["overdue"] == 1 and data["loans"]["to_validate"] == 1 and data["loans"]["in_progress"] == 1


def test_stats_without_loan_list_right(client, users, gear):
    db.session.add(Role(key=P, name="Professeur", permissions=["api.use", "inventory.view"]))
    db.session.commit()
    raw = token_for(users[P])
    data = client.get("/api/v1/stats", headers={"Authorization": f"Bearer {raw}"}).json
    assert "loans" not in data and data["items"]["total"] == 5


# ---------------------------------------------------------------------------
# Mon compte et fiche personne
# ---------------------------------------------------------------------------

def test_account_section_visible_with_right(client, as_role):
    as_role(P)
    assert "Accès à l'API" in client.get("/mon-compte").get_data(as_text=True)
    as_role(E)
    assert "Accès à l'API" not in client.get("/mon-compte").get_data(as_text=True)


def test_create_token_from_account(client, as_role):
    user = as_role(P)
    res = client.post("/mon-compte/api/jetons", data={"name": "Tableur", "lifetime": "30"})
    assert res.status_code == 200 and res.headers["Cache-Control"] == "no-store"
    raw = re.search(r'value="(ohn_[^"]+)"', res.get_data(as_text=True)).group(1)
    token = db.session.scalar(db.select(ApiToken))
    assert token.user_id == user.id and token.name == "Tableur"
    assert timedelta(days=29) < token.expires_at - datetime.now() <= timedelta(days=30)
    assert db.session.scalar(db.select(Activity).where(Activity.action == "account.api_token")) is not None
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"}).json["id"] == user.id
    page = client.get("/mon-compte").get_data(as_text=True)
    assert "Tableur" in page and token.prefix in page and raw not in page


def test_create_token_without_expiry(client, as_role):
    as_role(P)
    client.post("/mon-compte/api/jetons", data={"name": "Claude", "lifetime": ""})
    assert db.session.scalar(db.select(ApiToken)).expires_at is None


def test_create_token_needs_a_name(client, as_role):
    as_role(P)
    res = client.post("/mon-compte/api/jetons", data={"name": " ", "lifetime": "90"})
    assert res.status_code == 200 and db.session.scalar(db.select(ApiToken)) is None


def test_borrower_cannot_create_token(client, as_role):
    as_role(E)
    assert client.post("/mon-compte/api/jetons", data={"name": "x", "lifetime": "90"}).status_code == 403


def test_revoke_own_token(client, as_role):
    user = as_role(P)
    raw = token_for(user)
    token = db.session.scalar(db.select(ApiToken))
    res = client.post(f"/mon-compte/api/jetons/{token.id}/supprimer")
    assert res.status_code == 302 and res.headers["Location"].endswith("#api")
    assert db.session.scalar(db.select(ApiToken)) is None
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_cannot_revoke_someone_elses_token_from_account(client, as_role, users):
    token_for(users[A])
    token = db.session.scalar(db.select(ApiToken))
    as_role(P)
    client.post(f"/mon-compte/api/jetons/{token.id}/supprimer")
    assert db.session.scalar(db.select(ApiToken)) is not None


def test_admin_sees_and_revokes_tokens(client, as_role, users):
    token_for(users[P])
    token = db.session.scalar(db.select(ApiToken))
    as_role(A)
    assert token.prefix in client.get(f"/utilisateurs/{users[P].id}").get_data(as_text=True)
    client.post(f"/utilisateurs/{users[P].id}/jetons-api/{token.id}/supprimer")
    assert db.session.scalar(db.select(ApiToken)) is None
    assert db.session.scalar(db.select(Activity).where(Activity.action == "user.api_token")).target_user_id == users[P].id


def test_prof_cannot_revoke_others_tokens(client, as_role, users):
    token_for(users[A])
    token = db.session.scalar(db.select(ApiToken))
    as_role(P)
    assert client.post(f"/utilisateurs/{users[A].id}/jetons-api/{token.id}/supprimer").status_code == 403


def test_deleting_a_person_deletes_tokens(app, users):
    stranger = make_user(E, email="x@ohn.test")
    token_for(stranger)
    db.session.delete(stranger)
    db.session.commit()
    assert db.session.scalar(db.select(ApiToken)) is None
