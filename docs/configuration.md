# Configuration

L'appli se configure uniquement par **variables d'environnement**, en pratique dans le fichier `.env` à la racine du projet. Un modèle commenté est fourni : [`.env.example`](../.env.example).

- Avec Docker, `.env` est transmis aux conteneurs par `docker-compose.yml`.
- Sans Docker, `wsgi.py` et la commande `flask` le lisent depuis le dossier courant.
- Les variables sont lues **au démarrage** : redémarrez l'appli après une modification (`docker compose up -d`).

La page **Paramètres** de l'appli (admins) affiche les principales valeurs en cours.

## Essentiel

| Variable | Défaut | Description |
|---|---|---|
| `SECRET_KEY` | aucun | **Obligatoire.** Clé qui signe les cookies de session et les jetons CSRF. Générez-la avec `python3 -c "import secrets; print(secrets.token_hex(32))"`. L'appli refuse de démarrer si elle est vide ou vaut encore `change-moi`, sauf en mode debug. La changer déconnecte tout le monde. |
| `BASE_URL` | `http://localhost:8000` | Adresse publique de l'appli, sans `/` final. Elle est inscrite dans les **QR codes**, les emails, les liens iCal et l'URL de rappel OIDC. En `https://`, elle active aussi les cookies sécurisés. À fixer **avant** d'imprimer les étiquettes. |
| `ASSO_NAME` | `Mon association` | Nom affiché dans l'interface, les emails et le calendrier. |
| `ITEM_CODE_PREFIX` | `INV` | Préfixe des codes d'inventaire attribués automatiquement (`INV-0001`…), mis en majuscules. En changer n'affecte pas les codes existants : les nouveaux objets repartent à `<préfixe>-0001`. |

## Données et réseau

| Variable | Défaut | Description |
|---|---|---|
| `DATA_DIR` | `/data` dans Docker, `./data` sinon | Dossier des données : la base `inventaire.db` et les photos (`uploads/`). C'est lui qu'il faut sauvegarder. |
| `DATABASE_URL` | `sqlite:///<DATA_DIR>/inventaire.db` | URL SQLAlchemy de la base. **Seul SQLite est pris en charge** : l'appli utilise des fonctions propres à SQLite (recherche sans accents, verrouillage des écritures). |
| `TRUST_PROXY` | `0` | `1` derrière un reverse proxy qui ajoute les en-têtes `X-Forwarded-For`, `-Proto` et `-Host`. Laissez `0` sinon : ces en-têtes pourraient être falsifiés. |
| `TZ` | `Europe/Paris` | Fuseau horaire du conteneur. Les dates sont enregistrées et affichées dans l'heure locale du serveur. |

## Emails (facultatif)

Les emails sont activés dès que `SMTP_HOST` et un expéditeur (`SMTP_FROM` ou `SMTP_USER`) sont renseignés. Sans eux, les notifications restent dans l'appli.

| Variable | Défaut | Description |
|---|---|---|
| `SMTP_HOST` | (vide) | Serveur SMTP. |
| `SMTP_PORT` | `587` | Port. |
| `SMTP_USER` | (vide) | Identifiant. Vide = pas d'authentification. |
| `SMTP_PASSWORD` | (vide) | Mot de passe. |
| `SMTP_FROM` | (vide) | Expéditeur, par exemple `Inventaire <inventaire@exemple.org>`. À défaut, `SMTP_USER`. |
| `SMTP_TLS` | `true` | `true` : chiffrement STARTTLS. Le SSL implicite (port 465) n'est pas pris en charge. |

## Connexion OIDC (facultatif)

L'OIDC est activé dès que `OIDC_DISCOVERY_URL` et `OIDC_CLIENT_ID` sont renseignés. Le guide complet est dans [Connexion OIDC avec Authentik](oidc-authentik.md).

| Variable | Défaut | Description |
|---|---|---|
| `OIDC_DISCOVERY_URL` | (vide) | URL `…/.well-known/openid-configuration` du fournisseur. |
| `OIDC_CLIENT_ID` | (vide) | Identifiant du client. |
| `OIDC_CLIENT_SECRET` | (vide) | Secret du client. |
| `OIDC_PROVIDER_NAME` | `SSO` | Texte du bouton : « Se connecter avec … ». |
| `OIDC_SCOPES` | `openid email profile` | Scopes demandés. |
| `OIDC_GROUPS_CLAIM` | `groups` | Claim qui contient les groupes. |
| `OIDC_ADMIN_GROUPS` | (vide) | Groupes donnant le rôle Admin, séparés par des virgules. |
| `OIDC_PROF_GROUPS` | (vide) | Groupes donnant le rôle Prof, séparés par des virgules. |
| `OIDC_REQUIRE_VERIFIED_EMAIL` | `1` | Ne lier un compte existant par son email que si le fournisseur l'a vérifié (`email_verified`). |
| `OIDC_ONLY` | `0` | `1` : masque le formulaire email / mot de passe. Ignoré si l'OIDC n'est pas configuré. |

URL de rappel à déclarer chez le fournisseur : `<BASE_URL>/connexion/oidc/retour`.

## Variables techniques

Elles servent rarement en production.

| Variable | Où | Description |
|---|---|---|
| `FLASK_APP` | commande `flask` | `wsgi.py`. Déjà défini dans l'image Docker. |
| `FLASK_DEBUG` | développement | `1` : mode debug (rechargement automatique, pages d'erreur détaillées). Autorise aussi le démarrage sans `SECRET_KEY`. **Jamais en production.** |
| `SKIP_MIGRATIONS` | conteneur | `1` : ne pas lancer `flask db upgrade` au démarrage du conteneur (utilisé par le service `scheduler`). |
| `HYPOTHESIS_PROFILE` | tests | `deep` : 1 000 cas par test de propriété au lieu de 60. |

## Limites fixes

Ces valeurs sont dans le code et ne se règlent pas par variable :

| Limite | Valeur | Où la changer |
|---|---|---|
| Taille d'un envoi (photo, fichier d'import) | 16 Mo | `MAX_CONTENT_LENGTH`, [app/config.py](../app/config.py) |
| Lignes par import | 5 000 | `MAX_ROWS`, [app/services/transfer.py](../app/services/transfer.py) |
| Étiquettes par impression | 500 | `MAX_LABELS`, [app/blueprints/scan.py](../app/blueprints/scan.py) |
| Taille des photos enregistrées | 2 000 px (miniature 400 px) | [app/services/photos.py](../app/services/photos.py) |
| Tentatives de connexion | 8 échecs en 15 min, par email et par adresse IP | [app/blueprints/auth.py](../app/blueprints/auth.py) |
| Fréquence des rappels | 15 min | `docker-compose.yml`, service `scheduler` |
