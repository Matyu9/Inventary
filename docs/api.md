# API en lecture seule et serveur MCP

L'appli expose une API JSON en **lecture seule** sous `/api/v1`. Elle sert à brancher un tableur, un script, Home Assistant ou un assistant comme Claude, via le [serveur MCP](#serveur-mcp) fourni. Rien ne peut être modifié par cette voie : l'API n'accepte que des requêtes `GET`.

## Sommaire

- [Jetons d'accès](#jetons-daccès)
- [Appeler l'API](#appeler-lapi)
- [Ce que chacun voit](#ce-que-chacun-voit)
- [Points d'accès](#points-daccès)
- [Erreurs](#erreurs)
- [Serveur MCP](#serveur-mcp)
- [Sécurité](#sécurité)

## Jetons d'accès

Dans **Mon compte → Accès à l'API**, donnez un nom au jeton (« Tableur du bureau », « Claude »…) et choisissez sa durée : 30 jours, 90 jours, 1 an ou sans expiration. Le jeton, qui commence par `ohn_`, n'est **affiché qu'une fois** : copiez-le aussitôt. Si vous le perdez, supprimez-le et créez-en un autre.

- La section n'apparaît qu'avec le droit **« Créer des jetons d'accès à l'API »**. Par défaut, les admins et les profs l'ont, les emprunteurs non. Il se donne à d'autres rôles dans l'écran **Rôles**.
- La liste des jetons montre le début de chacun, sa date d'expiration et sa dernière utilisation (précise à 5 minutes près).
- Un admin voit et supprime les jetons d'une personne depuis sa fiche (section « Jetons d'accès à l'API »).
- Un jeton cesse de fonctionner dès que :
  - il expire ou il est supprimé ;
  - son propriétaire perd l'accès à l'appli (fiche désactivée ou archivée) ;
  - le rôle de son propriétaire n'a plus le droit d'utiliser l'API.
- La création et la suppression d'un jeton sont notées au journal.

## Appeler l'API

Envoyez le jeton dans l'en-tête `Authorization` :

```sh
curl -H "Authorization: Bearer ohn_…" https://inventaire.example.org/api/v1/me
```

Le cookie de session de l'appli ne suffit pas : même connecté dans le navigateur, il faut un jeton.

- **Format** : JSON en UTF-8. Les dates suivent l'ISO 8601 (`2026-12-14T18:30:00`), à l'heure locale du serveur. Les montants sont des nombres en euros.
- **Valeurs en français** : statuts et états gardent leurs noms de l'appli (`disponible`, `en garde`, `à réparer`…).
- **Listes paginées** : paramètres `page` (à partir de 1) et `per_page` (50 par défaut, 100 au plus). La réponse a la forme suivante :

  ```json
  {"data": [...], "meta": {"page": 1, "per_page": 50, "total": 128, "pages": 3}}
  ```

- **Paramètres oui/non** : `1`, `true`, `oui` ou `yes`.
- **Liens** : chaque objet, kit et prêt porte un champ `url` vers sa page dans l'appli.
- **Limite** : 300 appels par minute et par jeton. Au-delà, l'API répond `429` avec un en-tête `Retry-After`.

## Ce que chacun voit

L'API applique les droits du propriétaire du jeton, exactement comme l'appli :

| Données | Droit nécessaire |
|---|---|
| Objets, kits, consommables, lieux, catégories, tags, disponibilités, chiffres clés | « Voir l'inventaire » |
| Tous les prêts, filtre par emprunteur, chiffres des prêts dans `/stats` | « Voir tous les prêts » |
| Ses propres prêts (comme emprunteur ou garant) | « Voir ses propres prêts » |

Les coordonnées des personnes (email, téléphone) ne sont jamais exposées, sauf celles du propriétaire dans `/me`.

## Points d'accès

| Chemin | Contenu | Paramètres |
|---|---|---|
| `GET /me` | Le propriétaire du jeton : nom, rôle, droits, nom et expiration du jeton | — |
| `GET /items` | Objets, triés par code | `q` (sans accents : code, nom, marque, modèle, n° de série, description, tags, champs personnalisés), `category`, `tag`, `location` (sous-lieux compris), `status`, `condition`, `kit`, `tracking` (`individuel` ou `quantite`), `low_stock` |
| `GET /items/<id ou code>` | Fiche complète : achat, valeur, champs personnalisés, 10 derniers entretiens, nombre de photos et de documents, prêts ouverts | — |
| `GET /kits` | Kits, avec leur statut global (`disponible`, `en garde`, `sorti`, `incomplet`, `vide`) | `q` |
| `GET /kits/<id>` | Un kit, ses objets et ses prêts ouverts | — |
| `GET /consumables` | Matériel suivi en quantité : stock, unité, seuil, mode (`consomme` ou `retournable`) | `low_stock` |
| `GET /locations` | Lieux, avec leur chemin complet et le nombre d'objets rangés directement dedans | — |
| `GET /categories` | Catégories et nombre d'objets | — |
| `GET /tags` | Tags et nombre d'objets | — |
| `GET /loans` | Prêts, avec leur matériel ligne par ligne | `view`, `type` (`garde` ou `reservation`), `q` (nom de l'emprunteur ou du garant, titre), `borrower` (identifiant, avec « Voir tous les prêts »), `mine` |
| `GET /loans/<id>` | Un prêt | — |
| `GET /availability` | Objets et kits libres sur une période | `start`, `end`, `q`, `category`, `tag`, `location` |
| `GET /stats` | Chiffres clés | — |

Les objets et kits à la corbeille n'apparaissent nulle part.

**Vues de `/loans`** (paramètre `view`, `actifs` par défaut) :

| Vue | Prêts |
|---|---|
| `actifs` | demandés, validés ou en cours |
| `demande` | à valider |
| `garant` | en attente du garant |
| `valide` | validés |
| `en_cours` | en cours |
| `retard` | en cours, date de retour dépassée |
| `historique` | rendus, refusés ou annulés |
| `tous` | tous |

**Disponibilités** (`/availability`) :

- **Période** : `start` et `end` acceptent une date (`2026-12-14`) ou une date et heure (`2026-12-14T18:00`). Une date de fin seule couvre toute la journée. Par défaut, la période commence maintenant et dure 24 heures.
- **Ce qui est libre** : un objet l'est s'il n'est ni réformé, ni en entretien, ni pris par un prêt validé ou en cours sur la période. Les simples demandes ne bloquent rien.
- **Quantités** : `available` donne la quantité libre. Pour un consommable distribué, c'est le stock.
- **Kits** : les objets d'un kit ne sont pas listés seuls, puisqu'ils se prêtent avec lui. Un kit est libre si tous ses objets le sont.

**Exemple** : les instruments en garde rangés au Local, sous-lieux compris.

```sh
curl -H "Authorization: Bearer $JETON" \
  "https://inventaire.example.org/api/v1/items?status=en%20garde&location=3&per_page=100"
```

## Erreurs

Toute erreur renvoie un JSON avec un message en français :

```json
{"error": {"code": 403, "message": "Votre rôle ne donne pas accès à ces données."}}
```

| Code | Cas |
|---|---|
| `400` | Paramètre invalide : nombre attendu, date illisible, vue inconnue, fin avant le début |
| `401` | Jeton absent, invalide ou expiré, ou propriétaire sans accès à l'appli |
| `403` | Droit manquant |
| `404` | Objet, kit, prêt ou chemin introuvable |
| `405` | Autre méthode que `GET` |
| `429` | Trop d'appels, voir `Retry-After` |
| `500` | Erreur interne, avec une référence à retrouver dans les logs |

## Serveur MCP

Le dossier [`mcp_server/`](../mcp_server) contient un serveur [MCP](https://modelcontextprotocol.io) qui donne à un assistant (Claude Desktop, Claude Code…) un accès **en lecture seule** à l'Inventaire. Il tourne sur l'ordinateur de la personne et interroge l'API avec son jeton : l'assistant voit exactement ce qu'elle voit.

Questions possibles :

- « Quels instruments sont libres le 14 décembre ? »
- « Qui a une clarinette en garde cette année ? »
- « Quels prêts sont en retard ? »
- « Il reste combien d'anches ? »

**Installation**, depuis une copie du dépôt, avec Python 3.11 ou plus :

```sh
python3 -m venv .venv-mcp
.venv-mcp/bin/pip install -r mcp_server/requirements.txt
```

**Configuration** : deux variables d'environnement.

- `INVENTAIRE_URL` : adresse de l'appli, par exemple `https://inventaire.example.org`.
- `INVENTAIRE_TOKEN` : un jeton créé dans Mon compte. Donnez-lui un nom clair (« Claude ») pour pouvoir le supprimer seul.

**Claude Code** (lancé depuis le dossier du dépôt) :

```sh
claude mcp add inventaire \
  -e INVENTAIRE_URL=https://inventaire.example.org \
  -e INVENTAIRE_TOKEN=ohn_… \
  -e PYTHONPATH="$PWD" \
  -- "$PWD/.venv-mcp/bin/python" -m mcp_server
```

**Claude Desktop** (`claude_desktop_config.json`) :

```json
{
  "mcpServers": {
    "inventaire": {
      "command": "/chemin/vers/Inventaire/.venv-mcp/bin/python",
      "args": ["-m", "mcp_server"],
      "env": {
        "PYTHONPATH": "/chemin/vers/Inventaire",
        "INVENTAIRE_URL": "https://inventaire.example.org",
        "INVENTAIRE_TOKEN": "ohn_…"
      }
    }
  }
}
```

**Outils proposés** (tous annotés « lecture seule ») :

| Outil | Rôle |
|---|---|
| `qui_suis_je` | Compte du jeton et ses droits |
| `rechercher_objets` | Recherche et filtres de l'inventaire |
| `fiche_objet` | Fiche d'un objet, par code ou identifiant |
| `lister_kits`, `fiche_kit` | Kits et leur contenu |
| `consommables` | Stocks, avec un filtre « stock bas » |
| `lieux`, `categories` | Référentiel : lieux, catégories et tags |
| `prets`, `fiche_pret` | Prêts, par vue, type, personne |
| `disponibilite` | Matériel libre sur une période |
| `chiffres_cles` | Objets par statut, stocks bas, prêts à valider, en cours ou en retard |

## Sécurité

- **Stockage** : la base ne garde que l'empreinte SHA-256 du jeton. Une fuite de la base ne donne accès à rien.
- **Pas de cache** : les réponses de l'API portent `Cache-Control: no-store`, tout comme la page qui affiche un nouveau jeton. Le jeton n'apparaît jamais dans un message flash, qui passerait par le cookie de session.
- **Jeton égaré** : supprimez-le dans Mon compte. Il cesse de fonctionner immédiatement.
- **Un usage, un jeton** : un jeton par usage (tableur, assistant, script), avec une expiration, se supprime sans couper les autres.
- **MCP** : un assistant branché sur le serveur MCP lit des données personnelles (noms des emprunteurs). Choisissez un compte dont les droits correspondent à ce que l'assistant doit voir.
