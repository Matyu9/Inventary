# Connexion avec Adhérents

Adhérents est le serveur de connexion de l'association. Ce guide branche l'inventaire dessus, rattache les comptes existants, puis passe la connexion entièrement par Adhérents.

## Sommaire

1. [Déclarer l'inventaire dans Adhérents](#1-déclarer-linventaire-dans-adhérents)
2. [Configurer l'inventaire](#2-configurer-linventaire)
3. [Rattacher les comptes](#3-rattacher-les-comptes)
4. [Basculer](#4-basculer)
5. [Rôles d'après les groupes](#rôles-daprès-les-groupes)
6. [Déconnexion](#déconnexion)
6. [Dépannage](#dépannage)

## 1. Déclarer l'inventaire dans Adhérents

Dans Adhérents, **Applications → Nouvelle application** :

| Champ | Valeur |
|---|---|
| Nom | `Inventaire` |
| Adresses de retour | `<BASE_URL>/connexion/oidc/retour` |
| Adresses après déconnexion | `<BASE_URL>/connexion` |
| Adresse des avis de déconnexion | `<BASE_URL>/connexion/oidc/deconnexion` |
| Groupes autorisés | ceux qui entrent dans l'inventaire (musiciens, profs, régie…) |
| Groupes transmis | ceux qui donnent un rôle dans l'inventaire (voir `OIDC_ADMIN_GROUPS`, `OIDC_PROF_GROUPS` et les groupes des rôles) |
| Appli de l'association | cochée : pas d'écran de consentement |

Notez l'identifiant et le secret : le secret n'est affiché qu'une fois.

Adhérents doit pouvoir joindre l'adresse des avis de déconnexion. Avec Docker, `localhost` désigne le conteneur, pas la machine.

## 2. Configurer l'inventaire

```sh
OIDC_DISCOVERY_URL=https://compte.<domaine>/.well-known/openid-configuration
OIDC_CLIENT_ID=<identifiant>
OIDC_CLIENT_SECRET=<secret>
OIDC_PROVIDER_NAME=Adhérents
OIDC_SCOPES=openid email profile groups
```

À ce stade, le formulaire email / mot de passe reste affiché (`OIDC_ONLY=0`) : les deux connexions cohabitent.

## 3. Rattacher les comptes

Sans cette étape, l'inventaire retrouve la fiche d'une personne par son email à sa première connexion. Or la fiche d'un enfant porte souvent l'adresse de son parent : le parent récupérerait la fiche de l'enfant, et ses prêts.

1. Donnez à l'inventaire la liste des comptes, au choix :
   - **sans fichier** (conseillé) : dans Adhérents, cochez « Peut récupérer la liste des comptes » sur l'application, puis renseignez `OIDC_ACCOUNTS_URL=https://compte.<domaine>/oidc/comptes` dans l'inventaire ;
   - **avec un fichier** : dans Adhérents, **Applications → Inventaire → Exporter les comptes (JSON)**, ou `flask oidc-export-accounts --application "Inventaire" > comptes.json`.
2. Dans l'inventaire, **Personnes → Rattacher les comptes OIDC** : cliquez « Récupérer les comptes depuis Adhérents », ou envoyez le fichier. Rien n'est modifié à ce stade.
3. Vérifiez le tableau. Seules les lignes où l'adresse vérifiée **et** le nom correspondent sont cochées d'office. Les lignes « à vérifier » (adresse partagée, nom identique mais autre adresse, adresse non vérifiée) ne se cochent que si c'est bien la même personne.
4. Confirmez. Chaque rattachement est noté au journal.
5. Si vous êtes passé par un fichier, supprimez-le : il contient des noms et des adresses.

Puis mettez **`OIDC_LINK_BY_EMAIL=0`**. Une personne non rattachée dont l'adresse est déjà prise par une fiche est alors refusée, avec un message qui l'invite à contacter un responsable, au lieu de prendre cette fiche. Une personne dont l'adresse est inconnue reçoit une nouvelle fiche, comme avant.

On peut refaire l'export et le rattachement à tout moment, par exemple pour une personne refusée : les lignes déjà rattachées sont simplement signalées.

Seul un administrateur accède à cet écran : rattacher une identité à une fiche d'administrateur donne ses droits.

## 4. Basculer

Une fois les connexions par Adhérents vérifiées :

```sh
OIDC_ONLY=1
```

Le formulaire de mot de passe disparaît. **Gardez au moins un administrateur avec un mot de passe** : si Adhérents est en panne, il entre par l'accès de secours, `<BASE_URL>/connexion?local=1`. Cette page n'accepte que les administrateurs, avec la même limitation des tentatives que la connexion normale.

## Rôles d'après les groupes

Adhérents décide de qui entre, et l'inventaire donne le rôle d'après les groupes reçus. Exemple : faire d'Alice une administratrice de l'inventaire.

1. Dans Adhérents, **Groupes → Nouveau groupe** : « Admins de l'Inventaire ». Notez sa **clé** (`inventaire-admins`) : c'est elle qui est transmise, et elle ne change jamais, même si le groupe est renommé.
2. Dans Adhérents, sur la fiche d'Alice (**Comptes**), ajoutez-la à ce groupe. Pour tout un rôle d'un coup (tous les gestionnaires, par exemple), cochez plutôt le groupe sur ce rôle, dans **Rôles**.
3. Dans Adhérents, **Applications → Inventaire → Modifier** : cochez ce groupe dans **Groupes transmis**. Pas besoin de le cocher dans « Groupes autorisés » si Alice est déjà dans un groupe autorisé.
4. Dans l'inventaire, **Rôles → Administrateur** : mettez `inventaire-admins` dans **Groupes OIDC**. On peut aussi utiliser `OIDC_ADMIN_GROUPS=inventaire-admins` dans le `.env`, mais cela demande un redémarrage.
5. Le scope `groups` doit figurer dans `OIDC_SCOPES`, sinon l'inventaire ne reçoit aucun groupe.

Alice devient administratrice à sa prochaine connexion. Si elle est connectée au moment du changement, Adhérents prévient l'inventaire, qui la déconnecte : elle se reconnecte sans rien ressaisir. La retirer du groupe la fait redescendre de la même façon.

Les règles quand une personne a plusieurs groupes, et le cas d'un rôle choisi à la main, sont décrites dans [Rôles d'après les groupes](oidc-authentik.md#rôles-daprès-les-groupes) : elles sont les mêmes ici. Une différence : avec Adhérents, un changement de groupes prend effet tout de suite, et non à la prochaine connexion.

## Déconnexion

| Où l'on se déconnecte | Ce qui se passe |
|---|---|
| Dans l'inventaire | La session de l'inventaire se ferme, puis le navigateur passe par Adhérents, qui ferme la sienne et prévient les autres applis. Retour sur la page de connexion de l'inventaire. |
| Dans Adhérents, ou une autre appli | Adhérents envoie un avis signé à l'inventaire, qui ferme la session correspondante. |
| Compte coupé, groupe retiré, groupes transmis changés | Même avis : la personne est déconnectée de l'inventaire. Si seuls ses groupes ont changé, elle se reconnecte sans rien ressaisir et reçoit son nouveau rôle. |

L'inventaire vérifie chaque avis : signature avec les clés publiées par Adhérents, émetteur, audience, date (5 minutes de marge), événement de déconnexion, absence de `nonce`. Un avis invalide reçoit une réponse `400` et ne ferme rien. Une session ouverte par mot de passe n'est jamais fermée par un avis.

`OIDC_PROVIDER_LOGOUT=0` limite la déconnexion à l'inventaire, sans passer par Adhérents.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Adhérents affiche « Déconnexion impossible » | L'adresse `<BASE_URL>/connexion` n'est pas déclarée dans « Adresses après déconnexion ». |
| La déconnexion dans Adhérents ne ferme pas l'inventaire | Adresse des avis absente ou injoignable : l'écran de l'application, dans Adhérents, affiche les avis en attente. Les journaux de l'inventaire indiquent un avis refusé et son motif. |
| « Votre compte n'est pas encore rattaché à votre fiche » | `OIDC_LINK_BY_EMAIL=0` et une fiche porte déjà cette adresse : refaire l'export et rattacher la personne. |
| « Ce compte est déjà lié à une autre identité » | La fiche trouvée par email est rattachée à quelqu'un d'autre. |
| « Adhérents refuse l'identifiant ou le secret » | `OIDC_CLIENT_ID` ou `OIDC_CLIENT_SECRET` ne correspondent pas à l'application déclarée dans Adhérents. |
| « Cochez Peut récupérer la liste des comptes » | La case est décochée sur l'application, dans Adhérents. |
