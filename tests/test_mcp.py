"""Serveur MCP (mcp_server/) branché sur la vraie API, via le client de test Flask : pas de réseau."""
import asyncio
from urllib.parse import urlencode, urlsplit

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models import LoanStatus, Role  # noqa: E402
from app.services import api_tokens  # noqa: E402
from mcp_server.client import ApiError, InventaireClient  # noqa: E402
from mcp_server.server import build_server  # noqa: E402

from .test_loans_routes import gear, make_loan, soon  # noqa: E402,F401  (fixture gear)

A, P, E = Role.ADMIN, Role.PROF, Role.EMPRUNTEUR


class FlaskSession:
    """Remplace requests.Session : envoie les GET au client de test Flask."""

    def __init__(self, client):
        self.client, self.calls = client, []

    def get(self, url, params=None, headers=None, timeout=None):
        path = urlsplit(url).path + ("?" + urlencode(params) if params else "")
        self.calls.append(path)
        return _Response(self.client.get(path, headers=headers))


class _Response:
    def __init__(self, res):
        self.status_code, self._res = res.status_code, res

    def json(self):
        if not self._res.is_json:
            raise ValueError
        return self._res.json


@pytest.fixture
def session(client):
    return FlaskSession(client)


@pytest.fixture
def server(session, users):
    def _for(role=P):
        raw, _ = api_tokens.issue(users[role], "Claude")
        db.session.commit()
        return build_server(InventaireClient("http://localhost", raw, session=session))
    return _for


def run(server, name, **arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    assert not result.is_error
    return result.structured_content


def test_all_tools_are_read_only(server):
    tools = asyncio.run(server().list_tools())
    names = {t.name for t in tools}
    assert names == {"qui_suis_je", "rechercher_objets", "fiche_objet", "lister_kits", "fiche_kit", "consommables",
                     "lieux", "categories", "prets", "fiche_pret", "disponibilite", "chiffres_cles"}
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False, t.name
        assert t.description


def test_qui_suis_je(server, users):
    assert run(server(), "qui_suis_je")["name"] == users[P].full_name


def test_rechercher_objets_maps_parameters(server, session, gear):
    data = run(server(), "rechercher_objets", texte="flute", statut="disponible")
    assert [i["code"] for i in data["data"]] == ["OHN-0002"]
    assert session.calls[-1] == "/api/v1/items?q=flute&status=disponible&page=1"


def test_false_flags_and_empty_values_are_omitted(server, session, gear):
    run(server(), "consommables")
    assert session.calls[-1] == "/api/v1/consumables?page=1"
    run(server(), "consommables", stock_bas=True)
    assert "low_stock=True" in session.calls[-1]


def test_fiche_objet_and_kit(server, gear):
    srv = server()
    assert run(srv, "fiche_objet", code_ou_id=" ohn-0001 ")["name"] == "Saxophone"
    assert [i["code"] for i in run(srv, "fiche_kit", kit_id=gear["kit"].id)["items"]] == ["OHN-0005"]
    assert run(srv, "lister_kits", texte="sono")["meta"]["total"] == 1


def test_categories_combines_tags(server, gear):
    data = run(server(), "categories")
    assert [c["name"] for c in data["categories"]] == ["Bois"] and data["tags"] == []


def test_prets_and_disponibilite(server, gear, users):
    loan = make_loan(users, gear["sax"], status=LoanStatus.VALIDE, start=soon(1), end=soon(2))
    srv = server()
    assert [entry["id"] for entry in run(srv, "prets")["data"]] == [loan.id]
    assert run(srv, "fiche_pret", pret_id=loan.id)["lines"][0]["item"]["code"] == "OHN-0001"
    free = run(srv, "disponibilite", debut=soon(1).date().isoformat())
    assert "OHN-0001" not in [i["code"] for i in free["items"]]
    assert run(srv, "chiffres_cles")["loans"]["validated"] == 1


def test_api_errors_become_tool_errors(server, gear):
    with pytest.raises(ToolError, match="Objet introuvable"):
        asyncio.run(server().call_tool("fiche_objet", {"code_ou_id": "OHN-9999"}))


def test_rights_of_the_token_owner_apply(server, users):
    role = Role(key=E, name="Emprunteur", permissions=["api.use", "myloans.view"])
    db.session.add(role)
    db.session.commit()
    with pytest.raises(ToolError, match="droits|accès"):
        asyncio.run(server(E).call_tool("rechercher_objets", {}))


def test_invalid_token(session, users):
    srv = build_server(InventaireClient("http://localhost/", "ohn_faux", session=session))
    with pytest.raises(ToolError, match="Jeton"):
        asyncio.run(srv.call_tool("qui_suis_je", {}))


def test_client_base_url():
    assert InventaireClient("https://x.org", "t").base_url == "https://x.org/api/v1"
    assert InventaireClient("https://x.org/api/v1/", "t").base_url == "https://x.org/api/v1"


def test_client_unreachable():
    import requests

    class Down:
        def get(self, *a, **kw):
            raise requests.ConnectionError("refusé")

    with pytest.raises(ApiError, match="injoignable"):
        InventaireClient("http://localhost:1", "t", session=Down()).get("/me")


def test_main_requires_configuration(monkeypatch):
    from mcp_server import server as module
    monkeypatch.delenv("INVENTAIRE_URL", raising=False)
    monkeypatch.delenv("INVENTAIRE_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="INVENTAIRE_URL"):
        module.main()
