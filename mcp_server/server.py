"""Outils MCP de l'Inventaire, tous en lecture seule.

Lancement (stdio) : INVENTAIRE_URL=https://inventaire.example.org INVENTAIRE_TOKEN=ohn_… python -m mcp_server
Le jeton se crée dans l'appli, « Mon compte » → Accès à l'API ; l'assistant voit ce que voit son propriétaire.
Mode d'emploi : docs/api.md, section « Serveur MCP ».
"""
import os
import sys
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from .client import ApiError, InventaireClient

INSTRUCTIONS = """Inventaire du matériel d'une association musicale (instruments, matériel, kits, consommables)
et de ses prêts (gardes à l'année et réservations datées). Accès en lecture seule : aucun outil ne modifie rien.
Les objets ont un code (ex. OHN-0042) ; statuts : disponible, en garde, sorti, en entretien, réformé.
Pour savoir si du matériel est libre à une date, utilisez `disponibilite`. Les dates sont à l'heure locale.
Donnez à l'utilisateur le lien `url` des fiches quand c'est utile."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

Page = Annotated[int, Field(ge=1, description="Page de résultats (50 par page).")]


def build_server(client):
    server = MCPServer("inventaire", title="Inventaire", instructions=INSTRUCTIONS)

    def call(path, **params):
        try:
            return client.get(path, **params)
        except ApiError as e:
            raise ToolError(str(e)) from None

    def tool(fn):
        return server.tool(annotations=READ_ONLY)(fn)

    @tool
    def qui_suis_je() -> dict[str, Any]:
        """Compte auquel appartient le jeton : nom, rôle et droits (ce que l'assistant peut voir)."""
        return call("/me")

    @tool
    def rechercher_objets(
        texte: Annotated[str | None, Field(description="Recherche sans accents ni majuscules : code, nom, "
                                                      "marque, modèle, n° de série, tags, champs personnalisés.")] = None,
        statut: Literal["disponible", "en garde", "sorti", "en entretien", "réformé"] | None = None,
        etat: Literal["neuf", "bon", "usé", "à réparer", "HS"] | None = None,
        categorie_id: Annotated[int | None, Field(description="Voir l'outil `categories`.")] = None,
        lieu_id: Annotated[int | None, Field(description="Sous-lieux compris. Voir l'outil `lieux`.")] = None,
        tag_id: int | None = None,
        kit_id: int | None = None,
        stock_bas: Annotated[bool, Field(description="Seulement les consommables sous leur seuil.")] = False,
        page: Page = 1,
    ) -> dict[str, Any]:
        """Cherche des objets de l'inventaire. Renvoie `data` (les objets) et `meta` (pagination, total)."""
        return call("/items", q=texte, status=statut, condition=etat, category=categorie_id, location=lieu_id,
                    tag=tag_id, kit=kit_id, low_stock=stock_bas, page=page)

    @tool
    def fiche_objet(
        code_ou_id: Annotated[str, Field(description="Code (ex. OHN-0042) ou identifiant numérique.")],
    ) -> dict[str, Any]:
        """Fiche complète d'un objet : description, achat, champs personnalisés, entretiens, prêts en cours."""
        return call(f"/items/{code_ou_id.strip()}")

    @tool
    def lister_kits(texte: str | None = None, page: Page = 1) -> dict[str, Any]:
        """Kits (ensembles prêtés d'un bloc, ex. sono) avec leur statut global."""
        return call("/kits", q=texte, page=page)

    @tool
    def fiche_kit(kit_id: int) -> dict[str, Any]:
        """Un kit : ses objets et ses prêts en cours."""
        return call(f"/kits/{kit_id}")

    @tool
    def consommables(stock_bas: bool = False, page: Page = 1) -> dict[str, Any]:
        """Matériel suivi en quantité (anches, câbles…) avec le stock et le seuil d'alerte."""
        return call("/consumables", low_stock=stock_bas, page=page)

    @tool
    def lieux() -> dict[str, Any]:
        """Lieux de rangement (chemin complet, ex. « Local > Armoire ») et nombre d'objets dans chacun."""
        return call("/locations")

    @tool
    def categories() -> dict[str, Any]:
        """Catégories d'objets et tags, avec le nombre d'objets de chacun."""
        return {"categories": call("/categories")["data"], "tags": call("/tags")["data"]}

    @tool
    def prets(
        vue: Annotated[Literal["actifs", "demande", "garant", "valide", "en_cours", "retard", "historique", "tous"],
                       Field(description="actifs : demandés, validés ou en cours ; demande : à valider ; garant : "
                                         "en attente du garant ; retard : en cours et date de retour dépassée.")]
        = "actifs",
        type: Literal["garde", "reservation"] | None = None,
        recherche: Annotated[str | None, Field(description="Nom de l'emprunteur ou du garant, ou titre.")] = None,
        emprunteur_id: int | None = None,
        mes_prets: Annotated[bool, Field(description="Seulement ceux dont on est emprunteur ou garant.")] = False,
        page: Page = 1,
    ) -> dict[str, Any]:
        """Prêts visibles avec le jeton (tous, ou seulement les siens selon les droits), avec leur matériel."""
        return call("/loans", view=vue, type=type, q=recherche, borrower=emprunteur_id, mine=mes_prets, page=page)

    @tool
    def fiche_pret(pret_id: int) -> dict[str, Any]:
        """Un prêt : emprunteur, garant, période, caution, matériel ligne par ligne."""
        return call(f"/loans/{pret_id}")

    @tool
    def disponibilite(
        debut: Annotated[str | None, Field(description="AAAA-MM-JJ ou AAAA-MM-JJTHH:MM ; par défaut maintenant.")]
        = None,
        fin: Annotated[str | None, Field(description="Une date seule couvre toute la journée ; par défaut 24 h "
                                                    "après le début.")] = None,
        texte: str | None = None,
        categorie_id: int | None = None,
        lieu_id: int | None = None,
    ) -> dict[str, Any]:
        """Objets et kits libres sur une période (ni pris par un prêt validé ou en cours, ni en entretien).
        `available` donne la quantité libre. Les objets d'un kit ne se prêtent qu'avec leur kit."""
        return call("/availability", start=debut, end=fin, q=texte, category=categorie_id, location=lieu_id)

    @tool
    def chiffres_cles() -> dict[str, Any]:
        """Chiffres clés : objets par statut, kits, stocks bas et, selon les droits, prêts à valider, en cours,
        en retard."""
        return call("/stats")

    return server


def main():
    url, token = os.environ.get("INVENTAIRE_URL"), os.environ.get("INVENTAIRE_TOKEN")
    if not url or not token:
        sys.exit("Renseignez INVENTAIRE_URL (adresse de l'appli) et INVENTAIRE_TOKEN (jeton créé dans "
                 "« Mon compte » → Accès à l'API).")
    build_server(InventaireClient(url, token)).run("stdio")
