# Installation et exploitation

Ce guide s'adresse à la personne qui héberge l'inventaire : installation, mise en ligne, sauvegardes, mises à jour et emails.

## Sommaire

- [Ce qu'il faut](#ce-quil-faut)
- [Installation avec Docker](#installation-avec-docker)
- [Mise en ligne derrière un reverse proxy](#mise-en-ligne-derrière-un-reverse-proxy)
- [Premiers pas après l'installation](#premiers-pas-après-linstallation)
- [Emails](#emails)
- [Rappels automatiques](#rappels-automatiques)
- [Sauvegarde et restauration](#sauvegarde-et-restauration)
- [Mise à jour](#mise-à-jour)
- [Installation sans Docker](#installation-sans-docker)
- [Journaux et diagnostic](#journaux-et-diagnostic)
- [Liste de contrôle de sécurité](#liste-de-contrôle-de-sécurité)

## Ce qu'il faut

- Un serveur Linux (un petit VPS ou un Raspberry Pi 4 suffisent) avec **Docker** et **Docker Compose**.
- Un **nom de domaine** ou sous-domaine, par exemple `inventaire.harmonie-neuville.fr`, pointant vers ce serveur.
- Un **reverse proxy HTTPS** : Caddy, Traefik, nginx… Le HTTPS est indispensable pour la caméra des téléphones (scan des QR codes) et pour sécuriser les cookies de session.
- Facultatif : un compte **SMTP** pour les emails, et un fournisseur **OIDC** pour la connexion unique.

Toutes les données tiennent dans un seul dossier, `data/` : la base SQLite et les photos. Aucune autre base de données n'est nécessaire.

## Installation avec Docker

```sh
git clone <url-du-depot> inventaire
cd inventaire
cp .env.example .env
```

Éditez `.env` (le détail de chaque variable est dans [Configuration](configuration.md)) :

```ini
SECRET_KEY=<résultat de la commande ci-dessous>
BASE_URL=https://inventaire.harmonie-neuville.fr
ASSO_NAME=Mon association
TRUST_PROXY=1
```

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"   # génère SECRET_KEY
```

Créez le dossier de données, puis démarrez :

```sh
mkdir -p data && sudo chown 1000:1000 data   # le conteneur tourne avec l'utilisateur 1000
docker compose up -d --build
docker compose exec app flask seed           # catégories et lieu par défaut
docker compose exec app flask create-admin   # premier administrateur (email, nom, mot de passe)
```

Deux services démarrent :

| Service | Rôle |
|---|---|
| `app` | L'application (Gunicorn, 2 workers), sur `127.0.0.1:8000` uniquement (réglable avec `BIND_ADDR`). Applique les migrations de la base à chaque démarrage. |
| `scheduler` | Lance les [rappels automatiques](#rappels-automatiques) toutes les 15 minutes. |

> L'appli **refuse de démarrer** si `SECRET_KEY` est vide ou vaut encore `change-moi`, la valeur du modèle. C'est voulu : cette clé signe les cookies de session, et une clé connue de tous permettrait de fabriquer une session administrateur.

`flask create-admin` peut être relancé : avec un email existant, il promeut le compte administrateur et redéfinit son mot de passe. C'est utile si plus personne n'a accès.

## Mise en ligne derrière un reverse proxy

Le service `app` n'écoute que sur `127.0.0.1:8000` : il n'est pas joignable directement depuis Internet. C'est le reverse proxy qui reçoit les visiteurs en HTTPS et leur transmet les requêtes.

**Caddy**, le plus simple (certificat HTTPS automatique) :

```
inventaire.harmonie-neuville.fr {
    reverse_proxy 127.0.0.1:8000
}
```

**nginx** :

```nginx
server {
    listen 443 ssl http2;
    server_name inventaire.harmonie-neuville.fr;
    # ssl_certificate / ssl_certificate_key : par exemple via certbot

    client_max_body_size 16m;   # photos et imports (limite de l'appli)

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }
}
```

**Proxy sur une autre machine** (Nginx Proxy Manager sur un autre serveur, par exemple) : il ne peut pas joindre `127.0.0.1`. Publiez le port sur l'IP privée par laquelle le proxy joint ce serveur, et n'autorisez que lui au pare-feu :

```ini
BIND_ADDR=10.30.0.2
```

```sh
docker compose up -d
# Docker contourne les règles INPUT d'ufw : filtrez dans la chaîne DOCKER-USER
sudo iptables -I DOCKER-USER -i <interface> -p tcp --dport 8000 ! -s <ip-du-proxy> -j DROP
```

Dans le proxy, l'hôte cible est `http://10.30.0.2:8000`.

Dans `.env` :

- `TRUST_PROXY=1` : l'appli lit l'adresse réelle des visiteurs et le protocole dans les en-têtes `X-Forwarded-*`. Laissez `0` si l'appli est exposée sans proxy, sinon n'importe qui pourrait falsifier son adresse IP.
- `BASE_URL=https://…` : l'adresse publique. Elle est inscrite dans les QR codes, les emails et les liens iCal, et elle active les cookies sécurisés.

> **Choisissez `BASE_URL` avant d'imprimer les étiquettes** : chaque QR code contient cette adresse. Si elle change plus tard, gardez une redirection de l'ancienne adresse vers la nouvelle sur le reverse proxy.

## Premiers pas après l'installation

1. Connectez-vous avec le compte créé par `flask create-admin`.
2. **Paramètres** : vérifiez le nom de l'association, l'adresse publique et l'état des emails.
3. **Catégories** et **Lieux** : adaptez ceux créés par `flask seed`.
4. **Champs perso** : ajoutez les champs propres à votre parc, si besoin.
5. Saisissez l'inventaire, ou **importez** votre tableur existant (Import / export, avec un essai à blanc avant l'import réel).
6. **Personnes** : créez les comptes des profs et des autres admins.
7. Imprimez les **étiquettes** QR.

Le [guide d'utilisation](guide-utilisateur.md) détaille chaque écran.

## Emails

Sans configuration SMTP, tout fonctionne : les notifications restent dans l'appli (la cloche). Pour les recevoir aussi par email, renseignez dans `.env` :

```ini
SMTP_HOST=smtp.exemple.org
SMTP_PORT=587
SMTP_USER=inventaire@exemple.org
SMTP_PASSWORD=…
SMTP_FROM=Pupitre <inventaire@exemple.org>
SMTP_TLS=true
```

- La connexion utilise **STARTTLS**, sur le port 587 en général. Le SSL implicite du port 465 n'est pas pris en charge.
- Redémarrez (`docker compose up -d`), puis testez avec **Paramètres → M'envoyer un email de test**.
- Les emails partent juste après l'action concernée, et à chaque passage des rappels. Une notification vieille de plus de 2 jours n'est plus envoyée, ce qui évite une avalanche d'emails si le SMTP est configuré après coup.
- Seules les personnes qui ont un email et un compte actif reçoivent des emails.

## Rappels automatiques

Le service `scheduler` exécute `flask run-reminders` toutes les 15 minutes : retards, retours et départs imminents, entretiens, stocks bas, demandes en attente, rappels manuels. Le [guide d'utilisation](guide-utilisateur.md#notifications-et-rappels-automatiques) donne la liste complète.

Pour les lancer à la main :

```sh
docker compose exec app flask run-reminders
```

ou, dans l'appli, **Paramètres → Lancer les rappels maintenant**. Chaque rappel n'est envoyé qu'une fois par période (jour, semaine ou échéance) : relancer la commande ne crée pas de doublons.

## Sauvegarde et restauration

Tout est dans `data/` : `inventaire.db` (la base) et `uploads/` (photos et documents joints).

### Sauvegarder

La base est en mode WAL : ne copiez pas `inventaire.db` seul pendant que l'appli tourne. Utilisez la fonction de sauvegarde de SQLite, qui produit une copie cohérente :

```sh
# depuis le conteneur (aucun outil à installer sur le serveur)
docker compose exec app python -c "import sqlite3; sqlite3.connect('/data/inventaire.db').backup(sqlite3.connect('/data/sauvegarde.db'))"
tar czf inventaire-$(date +%F).tgz data/sauvegarde.db data/uploads/
rm data/sauvegarde.db
```

ou, si `sqlite3` est installé sur le serveur :

```sh
sqlite3 data/inventaire.db ".backup data/sauvegarde.db"
```

Automatisez-le avec une tâche cron quotidienne, et gardez les archives **ailleurs que sur le serveur** (stockage distant, autre machine).

### Restaurer

```sh
docker compose down
mv data data.ancien
mkdir data && tar xzf inventaire-AAAA-MM-JJ.tgz          # recrée data/sauvegarde.db et data/uploads/
mv data/sauvegarde.db data/inventaire.db
sudo chown -R 1000:1000 data
docker compose up -d
```

Si la sauvegarde vient d'une version plus ancienne de l'appli, les migrations sont appliquées au démarrage.

## Mise à jour

```sh
cd inventaire
# faites d'abord une sauvegarde (voir ci-dessus)
git pull
docker compose up -d --build
```

Au démarrage, le service `app` applique les migrations de la base (`flask db upgrade`). Le `scheduler` attend que l'appli soit en bonne santé, et ne lance jamais les migrations lui-même. Consultez le [journal des versions](../CHANGELOG.md) avant de mettre à jour.

## Installation sans Docker

Pour un serveur où Docker n'est pas disponible. Il faut Python 3.12 ou plus récent.

```sh
git clone <url-du-depot> /srv/inventaire && cd /srv/inventaire
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # régler SECRET_KEY, BASE_URL, DATA_DIR=/srv/inventaire/data…
export FLASK_APP=wsgi.py
.venv/bin/flask db upgrade
.venv/bin/flask seed
.venv/bin/flask create-admin
```

Le fichier `.env` est lu automatiquement, depuis le dossier courant, par `wsgi.py` et par la commande `flask`.

**Service systemd**, `/etc/systemd/system/inventaire.service` :

```ini
[Unit]
Description=Pupitre (inventaire)
After=network.target

[Service]
User=inventaire
WorkingDirectory=/srv/inventaire
Environment=FLASK_APP=wsgi.py
ExecStartPre=/srv/inventaire/.venv/bin/flask db upgrade
ExecStart=/srv/inventaire/.venv/bin/gunicorn --bind 127.0.0.1:8000 --workers 2 wsgi:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**Rappels**, toutes les 15 minutes dans la crontab de l'utilisateur `inventaire` :

```cron
*/15 * * * * cd /srv/inventaire && FLASK_APP=wsgi.py .venv/bin/flask run-reminders >/dev/null 2>&1
```

Fuseau horaire : les dates suivent l'heure locale du serveur (`TZ`). Vérifiez qu'elle est correcte (`timedatectl`).

## Journaux et diagnostic

```sh
docker compose ps                 # état des services (healthy / unhealthy)
docker compose logs -f app        # requêtes et erreurs de l'appli
docker compose logs -f scheduler  # résultat de chaque passage des rappels
```

Dans l'appli, **Paramètres → État de l'installation** (`/sante`) fait le point : base accessible et en mode WAL, migrations à jour, dossier `data/uploads` accessible en écriture et espace disque libre (alerte sous 1 Go), dernier passage du planificateur (alerte au-delà d'une heure), emails en attente. La même chose existe en JSON sur `/health`, pour un outil de supervision connecté avec un compte admin : la réponse est `200` si tout va bien ou s'il y a seulement des points à surveiller, `503` en cas de problème. Ces deux pages renvoient `404` aux personnes qui ne gèrent pas les paramètres. Le healthcheck Docker, lui, interroge la page de connexion et n'a pas besoin de compte.

| Symptôme | Piste |
|---|---|
| Le conteneur redémarre en boucle avec « SECRET_KEY n'est pas définie » | Renseigner `SECRET_KEY` dans `.env` |
| `PermissionError` sur `/data` | `sudo chown -R 1000:1000 data` |
| Les QR codes ouvrent `localhost` | `BASE_URL` n'est pas l'adresse publique |
| La caméra ne s'ouvre pas sur téléphone | L'appli n'est pas servie en HTTPS |
| On est déconnecté à chaque page, ou « CSRF token missing » | `BASE_URL` en `https` mais site servi en `http`, ou `TRUST_PROXY` mal réglé derrière le proxy |
| Les adresses IP des journaux sont toutes celles du proxy | `TRUST_PROXY=1` |
| Photo ou import refusé (« 413 ») | Fichier de plus de 16 Mo, ou limite du reverse proxy (`client_max_body_size` pour nginx) |
| Pas d'emails | Paramètres → email de test, puis `docker compose logs app` |
| Plus aucun admin ne peut se connecter | `docker compose exec app flask create-admin` avec un email existant |

## Liste de contrôle de sécurité

- [ ] `SECRET_KEY` longue et aléatoire (`python3 -c "import secrets; print(secrets.token_hex(32))"`), propre à cette installation et jamais publiée (le fichier `.env` n'est pas versionné).
- [ ] Appli servie uniquement en **HTTPS**, avec `BASE_URL` en `https://` (sans quoi les cookies de session ne sont pas marqués `Secure`).
- [ ] Port 8000 non exposé publiquement (c'est le réglage par défaut de `docker-compose.yml`).
- [ ] `TRUST_PROXY=1` derrière le reverse proxy (OpenResty, Nginx Proxy Manager, Caddy, Traefik…), et seulement dans ce cas. Sans lui, l'appli voit toutes les requêtes venir du proxy en `http` : la limite de tentatives de connexion vise l'adresse du proxy, et les redirections sont fausses. Les logs le signalent au démarrage quand `BASE_URL` est en `https://`.
- [ ] Après une mise à jour, aucune violation « Content Security Policy » dans la console du navigateur (F12) sur les pages courantes (voir [`CSP_REPORT_ONLY`](configuration.md#content-security-policy)).
- [ ] Sauvegardes automatiques, stockées hors du serveur, et restauration testée au moins une fois.
- [ ] Comptes des personnes parties désactivés (Personnes → Modifier → décocher « Peut se connecter »).
- [ ] Système et images Docker mis à jour régulièrement.

Pour signaler une faille, voir [SECURITY.md](../SECURITY.md).
