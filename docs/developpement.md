# Développement

Ce guide explique comment installer l'environnement, lancer l'appli et les tests, faire évoluer la base, et respecter les conventions du projet. Pour comprendre l'organisation du code, lisez d'abord [Architecture](architecture.md).

## Sommaire

- [Installer l'environnement](#installer-lenvironnement)
- [Lancer l'appli](#lancer-lappli)
- [Essayer avec des données de démo](#essayer-avec-des-données-de-démo)
- [Tests](#tests)
- [Migrations](#migrations)
- [Conventions](#conventions)
- [Ajouter une fonctionnalité](#ajouter-une-fonctionnalité)
- [Captures d'écran de la documentation](#captures-décran-de-la-documentation)

## Installer l'environnement

Il faut Python 3.12 ou plus récent.

```sh
git clone <url-du-depot> inventaire && cd inventaire
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium     # navigateur des tests de bout en bout
cp .env.example .env
```

Dans `.env`, pour le développement :

```ini
DATA_DIR=./data
FLASK_DEBUG=1
BASE_URL=http://localhost:8000
```

En mode debug, `SECRET_KEY` peut rester à sa valeur par défaut.

## Lancer l'appli

```sh
export FLASK_APP=wsgi.py
.venv/bin/flask db upgrade          # crée ou met à jour la base dans data/
.venv/bin/flask seed                # catégories et lieu par défaut
.venv/bin/flask create-admin        # votre compte administrateur
.venv/bin/flask run --debug --port 8000
```

Ouvrez <http://localhost:8000>.

> `flask run` n'écoute que sur `127.0.0.1` : l'appli n'est pas visible depuis le réseau, ce qui est préférable sur un Wi-Fi public. Pour la tester depuis un téléphone, passez plutôt par un tunnel HTTPS, ou déployez-la sur un serveur de test. Exposer le serveur de développement sur `0.0.0.0` le rend accessible à tout le réseau local.

Commandes utiles :

| Commande | Effet |
|---|---|
| `flask seed` | Crée les catégories et le lieu par défaut, s'ils n'existent pas |
| `flask create-admin` | Crée un administrateur, ou promeut un compte existant et redéfinit son mot de passe |
| `flask run-reminders` | Calcule les rappels et envoie les emails en attente |
| `flask routes` | Liste toutes les routes |
| `flask shell` | Console Python avec l'appli chargée |

## Essayer avec des données de démo

[scripts/demo_data.py](../scripts/demo_data.py) remplit une base **vide** avec des données fictives : 7 personnes, une vingtaine d'objets, un kit, des consommables, des prêts dans tous les états, des entretiens et un inventaire physique en cours.

```sh
export FLASK_APP=wsgi.py
DATA_DIR=./demo .venv/bin/flask db upgrade
DATA_DIR=./demo .venv/bin/python scripts/demo_data.py
DATA_DIR=./demo .venv/bin/flask run --debug --port 8000
```

| Compte | Rôle |
|---|---|
| `claire.dubois@exemple.org` | Administratrice |
| `marc.lefevre@exemple.org` | Prof (clarinettes) |
| `sophie.bernard@exemple.org` | Prof (percussions) |
| `lucas.martin@exemple.org` | Emprunteur |

Mot de passe commun : `demo-harmonie`. Le script refuse de tourner sur une base qui contient déjà des objets. Supprimez le dossier `demo/` pour repartir de zéro.

## Tests

```sh
.venv/bin/pytest                      # toute la suite, navigateur compris (~50 s)
.venv/bin/pytest -m "not e2e"         # sans navigateur (plus rapide)
.venv/bin/pytest tests/test_loans_routes.py -k checkin    # un fichier, un filtre
HYPOTHESIS_PROFILE=deep .venv/bin/pytest tests/test_properties.py   # 1 000 cas par propriété
```

Chaque test part d'une base SQLite en mémoire, vide. La protection CSRF y est désactivée, sauf dans les tests de bout en bout, qui tournent comme en production.

| Fichier | Ce qu'il couvre |
|---|---|
| `test_permissions.py` | Chaque droit de `can()` pour chaque rôle |
| `test_sweep.py` | **Toutes** les routes avec chaque rôle, et un parcours de tous les liens des pages |
| `test_loan_service.py`, `test_loans_routes.py` | Disponibilité, conflits, transitions, retours partiels, prolongations |
| `test_properties.py` | Propriétés (Hypothesis) : jamais de double réservation, de quantité dépassée ou de stock négatif |
| `test_kits_stock.py` | Kits et mouvements de stock |
| `test_audits.py` | Inventaire physique |
| `test_items_routes.py`, `test_item_services.py` | Objets, recherche, champs personnalisés, photos |
| `test_locations_referentiel.py` | Lieux, catégories, champs personnalisés |
| `test_reports_transfer.py` | Rapports, import et export |
| `test_calendar_reminders.py` | Calendrier, flux iCal, rappels, emails |
| `test_scan.py` | QR codes, scanner, étiquettes |
| `test_users.py`, `test_auth.py` | Comptes, connexion, mots de passe |
| `test_oidc.py` | Connexion OIDC avec un faux fournisseur qui signe de vrais jetons |
| `test_security.py` | Redirections, limitation des tentatives, en-têtes, images piégées, accès aux fichiers |
| `test_migrations.py` | Migrations jouées sur une base remplie |
| `test_e2e.py` | Parcours complets dans Chromium, sur ordinateur et sur mobile |

Les fixtures communes sont dans [tests/conftest.py](../tests/conftest.py) :

- `users` : un compte par rôle ;
- `as_role("prof")` : connecte le client de test avec le compte du rôle demandé ;
- `make_user(...)` : crée un compte sur mesure.

**Écrire les tests en même temps que le code.** Chaque fonctionnalité arrive avec ses tests : cas nominal, refus de droits, cas limites. Une nouvelle route doit aussi être prise en compte par `test_sweep.py`.

## Migrations

Le schéma est géré par Alembic, via Flask-Migrate.

```sh
# après une modification de app/models.py
.venv/bin/flask db migrate -m "description courte"
# relire le fichier généré dans migrations/versions/, puis :
.venv/bin/flask db upgrade
.venv/bin/flask db downgrade && .venv/bin/flask db upgrade   # vérifier l'aller-retour
.venv/bin/flask db check                                      # le modèle et la base concordent ?
```

### Pièges de SQLite

SQLite ne sait pas modifier une contrainte existante. Alembic recrée alors la table entière : copie, suppression, renommage. Or l'appli active les clés étrangères (`PRAGMA foreign_keys=ON`). **Supprimer une table référencée par d'autres échoue** dès que la base contient des données (`FOREIGN KEY constraint failed`). C'est le cas de `user`, `item`, `loan`…

Sur une base vide, comme celles des tests, rien ne se voit. Pour l'éviter :

| Au lieu de | Préférez |
|---|---|
| `unique=True` seul sur une nouvelle colonne (contrainte) | `unique=True, index=True` : un **index unique**, ajouté sans recréer la table |
| `batch_op.drop_column(...)` dans un `downgrade` | `op.drop_index(...)` puis `op.drop_column(...)` hors mode batch (`ALTER TABLE DROP COLUMN` natif) |
| Une colonne `NOT NULL` sans valeur par défaut | Ajouter `server_default=...` |

Après toute migration, lancez `tests/test_migrations.py` et ajoutez-y un cas si la migration touche une table référencée. Testez aussi sur une **copie** d'une vraie base :

```sh
mkdir -p /tmp/copie && sqlite3 data/inventaire.db ".backup /tmp/copie/inventaire.db"
DATA_DIR=/tmp/copie .venv/bin/flask db upgrade
```

## Conventions

- **Interface en français**, de même que les messages d'erreur, les commentaires et les docstrings. Les noms de code (variables, fonctions) sont en anglais. Les URL sont en français (`/prets/nouveau`).
- **Droits** : toujours via `can()`, `@require` ou `ensure()` ([permissions.py](../app/permissions.py)), jamais en testant le rôle directement dans une vue ou un template. Un nouveau droit s'ajoute à `ROLE_PERMISSIONS` et à `tests/test_permissions.py`.
- **Règles métier dans `services/`**, pas dans les routes. Un service lève une exception métier au message lisible par l'utilisateur, et **ne fait pas de commit**.
- **Historique** : toute modification visible d'un objet, d'un kit ou d'un prêt écrit une entrée avec `activity.log(...)`, en précisant les changements (`changes={champ: [ancien, nouveau]}`) quand c'est utile.
- **Notifications** : `notify(...)`. Pour un rappel périodique, ajoutez une `dedup_key` qui inclut la période.
- **Dates** : `datetime.now()` (heure locale), jamais `utcnow()` ni `CURRENT_TIMESTAMP`. Voir [Architecture › Choix techniques](architecture.md#choix-techniques).
- **Recherche** : `fold_contains(colonne, texte)` pour une recherche sans accents ni casse.
- **HTMX** : la route teste `request.headers.get("HX-Request")` et renvoie un fragment, nommé `_quelque_chose.html` ; sinon, elle redirige avec un message flash.
- **Style du code** : lignes de 120 caractères au plus. Imitez le code voisin : densité de commentaires, nommage, tournures.

## Ajouter une fonctionnalité

Liste de contrôle :

1. **Modèle** (si besoin) : modifier [app/models.py](../app/models.py), générer la migration, la relire en pensant aux [pièges de SQLite](#pièges-de-sqlite), et tester l'aller-retour.
2. **Droits** : ajouter l'action à `ROLE_PERMISSIONS`, avec son test.
3. **Service** : la règle métier, avec ses exceptions et ses tests unitaires.
4. **Route** : dans le blueprint du domaine, protégée par `@require` ; commit en fin de route ; réponse HTMX si pertinent.
5. **Template** : boutons masqués avec `can(...)` ; vérifier l'affichage sur mobile (moins de 960 px).
6. **Historique et notifications** : `log(...)`, et `notify(...)` si quelqu'un doit être prévenu.
7. **Tests** de routes, dont les refus pour les rôles non autorisés. `test_sweep.py` doit passer.
8. **Documentation** : mettre à jour le [guide d'utilisation](guide-utilisateur.md) (et son tableau « Qui peut faire quoi »), la [configuration](configuration.md) si une variable apparaît, et le [journal des versions](../CHANGELOG.md).

## Captures d'écran de la documentation

Les images de `docs/images/` sont produites par [scripts/captures.py](../scripts/captures.py). Le script :

1. crée une base temporaire avec les données de démo ;
2. lance l'appli sur `127.0.0.1` ;
3. photographie chaque page avec Chromium.

```sh
.venv/bin/python scripts/captures.py
```

Relancez-le après une modification visible de l'interface. Pour ajouter une capture, ajoutez une ligne à la liste `SHOTS` du script.
