# Connexion OIDC avec Authentik

Ce guide explique comment brancher la connexion de l'inventaire sur Authentik, d'abord en local pour tester, puis en production. Il s'applique aussi, à quelques noms d'écrans près, aux autres fournisseurs OpenID Connect (Keycloak, Pocket ID, Google, Microsoft Entra…).

## Sommaire

1. [Comment ça marche](#comment-ça-marche)
2. [Configurer Authentik](#1-configurer-authentik)
3. [Configurer l'inventaire](#2-configurer-linventaire)
4. [Tester en local](#3-tester-en-local)
5. [Passer en production](#4-passer-en-production)
6. [Comptes et rôles](#comptes-et-rôles)
7. [Référence des variables](#référence-des-variables)
8. [Dépannage](#dépannage)

## Comment ça marche

```
Navigateur ──1──▶ Inventaire  /connexion/oidc
Navigateur ◀─2─── redirection vers Authentik (state, nonce, PKCE)
Navigateur ──3──▶ Authentik   l'utilisateur se connecte
Navigateur ◀─4─── redirection vers <BASE_URL>/connexion/oidc/retour?code=…
Inventaire ──5──▶ Authentik   échange du code contre les jetons, lecture des clés et de /userinfo
```

- Authentik **n'appelle jamais** l'inventaire : les étapes 2 et 4 sont des redirections du navigateur. On peut donc tester avec un inventaire sur `localhost` et un Authentik sur Internet.
- Seule l'étape 5 est un appel de serveur à serveur : l'inventaire doit pouvoir joindre Authentik.
- L'inventaire vérifie la signature de l'`id_token`, son émetteur, son audience, son expiration et le `nonce`. Le `state` et le PKCE empêchent de rejouer ou de détourner une connexion.

## 1. Configurer Authentik

### Créer le provider et l'application

Dans l'interface d'administration d'Authentik : **Applications → Applications → Create with provider** (ou créez d'abord le provider dans **Applications → Providers**, puis l'application).

| Réglage | Valeur |
|---|---|
| Nom de l'application | `Inventaire` |
| Slug | `inventaire` (repris dans l'URL de découverte) |
| Type de provider | **OAuth2/OpenID Provider** |
| Authorization flow | le flow d'autorisation par défaut (explicite ou implicite, au choix) |
| Client type | **Confidential** |
| Redirect URIs | mode **Strict**, une ligne par environnement (voir ci-dessous) |
| Signing key | **obligatoire** : choisissez un certificat, par exemple `authentik Self-signed Certificate` |
| Scopes | `openid`, `email`, `profile` (les mappings par défaut) |

Les URL de rappel à déclarer, exactement (protocole, hôte, port, chemin) :

```
http://localhost:8000/connexion/oidc/retour
https://inventaire.harmonie-neuville.fr/connexion/oidc/retour
```

> **Signing key** : sans clé, Authentik signe les jetons en HS256 avec le secret du client et ne publie aucune clé publique. L'inventaire ne vérifie les jetons qu'avec les clés publiées : toutes les connexions échoueraient.

Notez ensuite, dans la fiche du provider :

- le **Client ID** et le **Client Secret** ;
- l'**OpenID Configuration URL**, de la forme `https://<authentik>/application/o/inventaire/.well-known/openid-configuration`.

### Restreindre l'accès (facultatif)

Par défaut, tout utilisateur Authentik peut se connecter à l'inventaire. Il y obtient au minimum un compte Emprunteur, qui ne voit que ses propres prêts. Pour limiter l'accès, ajoutez un **binding** de groupe ou d'utilisateur sur l'application (**Applications → Inventaire → Policy / Group / User Bindings**).

### Vérifier le contenu du jeton

Dans la fiche du provider, l'onglet **Preview** montre les claims envoyés pour un utilisateur donné. Vérifiez trois choses :

1. **`email`** est présent ;
2. **`email_verified`** vaut `true` (voir ci-dessous) ;
3. **`groups`** contient les noms des groupes de l'utilisateur (le mapping `profile` par défaut d'Authentik l'envoie).

### Le cas de `email_verified`

L'inventaire ne lie un compte existant par son email que si le fournisseur affirme l'avoir vérifié. Sinon, n'importe qui pouvant choisir son email dans Authentik pourrait prendre le compte d'un administrateur de l'inventaire.

Selon la version et la configuration d'Authentik, le mapping `email` par défaut peut envoyer `email_verified: false`. Dans ce cas, deux solutions :

**A. Un mapping email personnalisé** (recommandé si les utilisateurs ne peuvent pas modifier eux-mêmes leur email dans Authentik). Dans **Customization → Property Mappings → Create → Scope Mapping** :

- Name : `Inventaire : email vérifié`
- Scope name : `email`
- Expression :

  ```python
  return {
      "email": request.user.email,
      "email_verified": True,
  }
  ```

Puis, dans le provider (**Advanced protocol settings → Scopes**), remplacez le mapping `email` par défaut par celui-ci.

**B. Désactiver la vérification côté inventaire** avec `OIDC_REQUIRE_VERIFIED_EMAIL=0`, sous la même condition : les emails d'Authentik doivent être fiables.

Sans l'une ou l'autre, rien n'est cassé : les nouveaux venus obtiennent un compte Emprunteur sans email, mais une personne qui a déjà un compte dans l'inventaire voit sa connexion refusée (« le fournisseur d'identité ne l'a pas vérifié »).

> Pour savoir si vos utilisateurs peuvent changer leur email : **System → Brands** ou **System → Settings** selon la version, option du type « Allow users to change email ».

### Groupes et rôles

Créez dans Authentik, ou réutilisez, un groupe pour les administrateurs de l'inventaire et un pour les profs, par exemple `inventaire-admins` et `profs`. Ce sont leurs **noms** qui vont dans la configuration de l'inventaire.

## 2. Configurer l'inventaire

Dans le fichier `.env` à la racine du projet (voir `.env.example`) :

```ini
BASE_URL=http://localhost:8000
OIDC_DISCOVERY_URL=https://<authentik>/application/o/inventaire/.well-known/openid-configuration
OIDC_CLIENT_ID=<client id>
OIDC_CLIENT_SECRET=<client secret>
OIDC_PROVIDER_NAME=Authentik
OIDC_ADMIN_GROUPS=inventaire-admins
OIDC_PROF_GROUPS=profs
```

L'OIDC est activé dès que `OIDC_DISCOVERY_URL` et `OIDC_CLIENT_ID` sont renseignés. Le bouton « Se connecter avec Authentik » apparaît alors sur la page de connexion, au-dessus du formulaire email / mot de passe.

Les variables sont lues **au démarrage** : redémarrez l'appli après chaque modification.

## 3. Tester en local

1. Renseignez le `.env` avec `BASE_URL=http://localhost:8000`, et déclarez `http://localhost:8000/connexion/oidc/retour` dans Authentik.
2. Démarrez le serveur de développement (il lit le `.env` tout seul) :

   ```bash
   export FLASK_APP=wsgi.py
   .venv/bin/flask db upgrade          # applique les migrations, dont celle de l'OIDC
   .venv/bin/flask run --debug --port 8000
   ```

   `flask run` n'écoute que sur `127.0.0.1` : l'inventaire n'est pas exposé sur le réseau, et ce n'est pas nécessaire pour tester.

3. Ouvrez **`http://localhost:8000`**, pas `http://127.0.0.1:8000`. L'adresse doit être identique à `BASE_URL` et à l'URL de rappel. Sinon, le cookie de session posé au départ n'est pas renvoyé au retour et la connexion échoue.
4. Utilisez une **fenêtre de navigation privée** pour chaque compte testé : vous évitez de mélanger votre session d'administrateur de l'inventaire et celle d'Authentik.

Si Authentik tourne lui aussi sur votre machine (Docker), l'inventaire doit le joindre **à la même URL** que votre navigateur. L'émetteur (`iss`) du jeton doit correspondre à celui de la découverte.

### Scénarios à vérifier

| Scénario | Résultat attendu |
|---|---|
| Utilisateur inconnu de l'inventaire, sans groupe | compte Emprunteur créé, page « Mes prêts » |
| Utilisateur membre de `profs` | compte Prof |
| Utilisateur membre de `inventaire-admins` | compte Admin |
| Retirer cet utilisateur du groupe, puis se reconnecter | redevient Emprunteur |
| Compte déjà présent dans l'inventaire (même email) | compte existant lié, rôle conservé ou mis à jour selon les groupes |
| Compte désactivé dans l'inventaire | connexion refusée |
| Cliquer sur « Annuler » ou refuser chez Authentik | retour sur la page de connexion avec un message |

Le journal d'activité garde une trace des comptes créés, des liaisons et des changements de rôle.

## 4. Passer en production

1. Dans Authentik, ajoutez l'URL de rappel de production (`https://…/connexion/oidc/retour`).
2. Dans le `.env` du serveur : `BASE_URL=https://…` et, derrière un reverse proxy, `TRUST_PROXY=1`. Reprenez les variables `OIDC_*`.
3. `docker compose up -d --build` : les migrations sont appliquées au démarrage du conteneur.
4. Testez avec un compte non administrateur.
5. Une fois l'OIDC validé, vous pouvez masquer le formulaire email / mot de passe avec `OIDC_ONLY=1`.

> **Gardez un accès de secours.** Avec `OIDC_ONLY=1`, une panne d'Authentik empêche toute connexion. En cas de besoin, repassez `OIDC_ONLY=0` et redémarrez : les comptes qui ont un mot de passe pourront de nouveau l'utiliser. Gardez au moins un administrateur avec un mot de passe local.

## Comptes et rôles

### Rattachement d'une connexion à un compte

À chaque connexion, dans cet ordre :

1. **Identifiant Authentik (`sub`)** : si un compte de l'inventaire y est déjà lié, c'est lui.
2. **Email vérifié** : si un compte de l'inventaire a cet email, il est lié à l'identité Authentik pour les fois suivantes. Un compte déjà lié à une autre identité est refusé.
3. **Sinon** : un compte **Emprunteur** est créé, avec le prénom et le nom transmis par Authentik.

Cas particuliers :

- Un compte **désactivé par un admin** reste refusé, même via Authentik.
- Une **fiche emprunteur sans accès** (créée par un prof, jamais connectée) devient un compte actif à la première connexion de la personne.
- Un compte qui avait un **mot de passe provisoire** perd ce mot de passe à sa première connexion via Authentik : l'admin le connaissait et il n'a plus d'usage.
- Un compte qui a **son propre mot de passe** le garde : la personne peut se connecter des deux façons.

### Rôles d'après les groupes

Les groupes se déclarent à deux endroits : `OIDC_ADMIN_GROUPS` et `OIDC_PROF_GROUPS` dans le `.env`, et le champ **Groupes OIDC** de chaque rôle (menu **Rôles** de l'inventaire, y compris pour les rôles sur mesure). Dès qu'un groupe est déclaré, le rôle est recalculé à chaque connexion :

| Groupes de la personne | Rôle |
|---|---|
| un groupe de l'Admin (`.env` ou écran Rôles) | Admin (l'emporte toujours) |
| des groupes de plusieurs autres rôles | celui qui a **le plus de droits** |
| un seul groupe déclaré | le rôle correspondant |
| aucun groupe déclaré | Emprunteur, **sauf** si son rôle actuel a été choisi à la main par un admin de l'inventaire |

Autrement dit :

- un rôle **venu des groupes** suit les groupes : retirer quelqu'un du groupe le fait redescendre à Emprunteur à sa prochaine connexion ;
- un rôle **choisi par un admin** dans l'inventaire n'est jamais rétrogradé par Authentik. Il est seulement remplacé si la personne entre dans un groupe configuré ;
- quand un admin modifie le rôle d'un utilisateur dans l'inventaire, ce rôle n'est plus géré par les groupes.

Sans groupes déclarés, les rôles se gèrent uniquement dans l'inventaire (**Personnes → Modifier**).

> Le rôle est mis à jour **à la connexion**. Pour retirer un droit immédiatement, désactivez ou modifiez aussi le compte dans l'inventaire.

### Déconnexion

« Déconnexion » ferme la session de l'inventaire, pas celle d'Authentik. Sur un ordinateur partagé, déconnectez-vous aussi d'Authentik.

## Référence des variables

| Variable | Défaut | Rôle |
|---|---|---|
| `OIDC_DISCOVERY_URL` | (vide) | URL `…/.well-known/openid-configuration`. Avec `OIDC_CLIENT_ID`, active l'OIDC. |
| `OIDC_CLIENT_ID` | (vide) | Identifiant du client chez le fournisseur. |
| `OIDC_CLIENT_SECRET` | (vide) | Secret du client (client confidentiel). |
| `OIDC_PROVIDER_NAME` | `SSO` | Texte du bouton : « Se connecter avec … ». |
| `OIDC_SCOPES` | `openid email profile` | Scopes demandés. |
| `OIDC_GROUPS_CLAIM` | `groups` | Claim qui contient les groupes. |
| `OIDC_ADMIN_GROUPS` | (vide) | Groupes donnant le rôle Admin, séparés par des virgules. |
| `OIDC_PROF_GROUPS` | (vide) | Groupes donnant le rôle Prof, séparés par des virgules. |
| `OIDC_REQUIRE_VERIFIED_EMAIL` | `1` | `1` : lier un compte existant par email seulement si `email_verified` est vrai. |
| `OIDC_ONLY` | `0` | `1` : masque le formulaire email / mot de passe (ignoré si l'OIDC n'est pas configuré). |
| `BASE_URL` | `http://localhost:8000` | Sert à construire l'URL de rappel : doit correspondre à l'adresse utilisée dans le navigateur. |

## Dépannage

Le message affiché à l'utilisateur reste volontairement vague. Le détail se trouve dans les journaux de l'appli : le terminal de `flask run`, ou `docker compose logs app`. Cherchez « Connexion OIDC refusée » ou « Erreur pendant la connexion OIDC ».

| Symptôme | Cause probable | Solution |
|---|---|---|
| Pas de bouton « Se connecter avec… » | `OIDC_DISCOVERY_URL` ou `OIDC_CLIENT_ID` vide, ou appli pas redémarrée | Compléter le `.env`, redémarrer |
| « Le fournisseur d'identité est injoignable » au clic | URL de découverte fausse, ou Authentik non joignable depuis la machine de l'inventaire | Ouvrir l'URL de découverte avec `curl` depuis cette machine |
| Authentik affiche une erreur `redirect_uri` | L'URL de rappel déclarée ne correspond pas exactement à `<BASE_URL>/connexion/oidc/retour` | Corriger l'une ou l'autre (attention à `http`/`https`, au port, à `localhost` / `127.0.0.1`) |
| Retour d'Authentik puis « a échoué », journal `mismatching_state` ou `Missing "state"` | Le cookie de session a été perdu : adresse différente de `BASE_URL`, ou cookie sécurisé sur du HTTP | Utiliser exactement l'adresse de `BASE_URL` ; en HTTPS, vérifier `TRUST_PROXY=1` derrière un proxy |
| « a échoué », journal qui parle de clé, de signature ou de `jwks` | Provider sans **Signing key** (jetons HS256) | Choisir une clé de signature dans le provider |
| « a échoué », journal `invalid_client` | Mauvais Client ID ou Client Secret | Les recopier depuis le provider |
| « a échoué », journal qui parle de `iss` | L'émetteur du jeton diffère de celui de la découverte (Authentik joint par deux URL différentes) | Utiliser la même URL publique d'Authentik partout |
| « Un compte existe déjà avec cet email, mais le fournisseur ne l'a pas vérifié » | `email_verified` absent ou faux | Voir [Le cas de `email_verified`](#le-cas-de-email_verified) |
| « Ce compte est déjà lié à une autre identité » | Le compte de l'inventaire a été lié à un autre utilisateur Authentik | Vérifier qu'il s'agit bien de la même personne ; sinon corriger l'email de l'un des deux comptes |
| « Ce compte est désactivé » | Compte désactivé dans l'inventaire | **Utilisateurs → Modifier → Peut se connecter** |
| Le rôle ne change pas | Groupe mal orthographié, claim `groups` absent, ou rôle choisi à la main par un admin | Vérifier l'onglet **Preview** d'Authentik et les noms dans `OIDC_*_GROUPS` |
| La page plante après la connexion (`no such column: user.oidc_sub`) | Migration non appliquée | `flask db upgrade` (automatique avec Docker) |
