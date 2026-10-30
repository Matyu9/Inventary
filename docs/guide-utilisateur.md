# Guide d'utilisation

Ce guide décrit l'inventaire écran par écran. Il s'adresse aux trois rôles : administrateurs, profs et emprunteurs. Chaque section indique qui peut faire quoi.

## Sommaire

- [Se connecter](#se-connecter)
- [Les trois rôles](#les-trois-rôles)
- [Le tableau de bord](#le-tableau-de-bord)
- [L'inventaire](#linventaire)
  - [Fiche objet](#fiche-objet)
  - [Créer et modifier un objet](#créer-et-modifier-un-objet)
  - [Statuts et états](#statuts-et-états)
  - [Photos, notes, entretiens, rappels](#photos-notes-entretiens-rappels)
  - [Réformer ou supprimer](#réformer-ou-supprimer)
  - [Actions groupées](#actions-groupées)
- [Lieux, catégories, tags et champs personnalisés](#lieux-catégories-tags-et-champs-personnalisés)
- [Kits](#kits)
- [Consommables et stock](#consommables-et-stock)
- [Les prêts](#les-prêts)
  - [Gardes et réservations](#gardes-et-réservations)
  - [Le cycle d'un prêt](#le-cycle-dun-prêt)
  - [Demander un prêt](#demander-un-prêt)
  - [Valider ou refuser](#valider-ou-refuser)
  - [Remettre le matériel](#remettre-le-matériel)
  - [Récupérer le matériel](#récupérer-le-matériel)
  - [Prolonger, raccourcir, annuler](#prolonger-raccourcir-annuler)
- [QR codes, étiquettes et scanner](#qr-codes-étiquettes-et-scanner)
- [Inventaire physique](#inventaire-physique)
- [Calendrier et abonnement iCal](#calendrier-et-abonnement-ical)
- [Notifications et rappels automatiques](#notifications-et-rappels-automatiques)
- [Rapports](#rapports)
- [Import et export](#import-et-export)
- [Personnes et comptes](#personnes-et-comptes)
- [Paramètres](#paramètres)
- [Installer l'appli sur un téléphone](#installer-lappli-sur-un-téléphone)
- [Qui peut faire quoi](#qui-peut-faire-quoi)
- [Questions fréquentes](#questions-fréquentes)

## Se connecter

![Page de connexion](images/connexion.png)

- **Avec email et mot de passe.** Le compte est créé par un administrateur, qui vous communique un **mot de passe provisoire**. À la première connexion, l'appli demande d'en choisir un nouveau, d'au moins 8 caractères. Vous pourrez le changer plus tard via le menu de votre avatar, en haut à droite, puis **Mot de passe**.
- **Avec le compte de l'association** : si le bouton « Se connecter avec … » est affiché, la connexion passe par le fournisseur d'identité de l'association (Authentik, Google…). Voir [la doc OIDC](oidc-authentik.md#comptes-et-rôles) pour le rattachement des comptes.
- Après 8 échecs en 15 minutes, la connexion est bloquée quelques minutes pour cet email ou cette adresse.
- **Rester connecté** garde la session ouverte sur cet appareil. À éviter sur un ordinateur partagé.

Mot de passe oublié : demandez à un administrateur de générer un nouveau mot de passe provisoire.

## Les trois rôles

- **Administrateur** : gère tout, et il est le seul à **valider** les prêts, à supprimer, à gérer les comptes et les paramètres.
- **Professeur** : gère l'inventaire au quotidien (objets, notes, entretiens, stock). Il **demande** les prêts pour ses élèves et s'en porte **garant**, puis remet et récupère le matériel des prêts dont il est garant.
- **Emprunteur** : voit uniquement **ses** prêts, son calendrier et ses notifications. Un emprunteur peut aussi être une simple **fiche** sans accès à l'appli : un élève à qui on prête un instrument, mais qui ne se connecte pas.

Le tableau complet est en fin de guide : [Qui peut faire quoi](#qui-peut-faire-quoi).

## Le tableau de bord

**Admins et profs** voient :

- des raccourcis : **Demande de prêt**, **Scanner**, **Nouvel objet** ;
- le nombre d'objets par statut (disponible, en garde, sorti, en entretien, réformé) ;
- les **demandes à valider** (admins), les **prêts en retard**, les **sorties des 7 prochains jours** ;
- les **entretiens à prévoir** et les **stocks bas**.

**Les emprunteurs** voient leurs prêts en cours.

![Accueil d'un emprunteur sur téléphone](images/mobile-mes-prets.png)

La recherche en haut de page cherche dans tout l'inventaire : code, désignation, marque, numéro de série… sans tenir compte des accents ni des majuscules.

## L'inventaire

**Menu Inventaire** (admins et profs).

![Liste de l'inventaire](images/inventaire.png)

La liste se filtre par **catégorie**, **lieu** (sous-lieux compris), **statut**, **état** et **tag**, et se trie en cliquant sur les en-têtes. La recherche porte sur le code, la désignation, la marque, le modèle, le numéro de série et les champs personnalisés.

### Fiche objet

![Fiche d'un objet](images/fiche-objet.png)

La fiche regroupe :

- les **informations** : catégorie, marque, modèle, numéro de série, état, lieu, date et prix d'achat, valeur estimée, champs personnalisés ;
- le **QR code**, avec les liens pour imprimer l'étiquette ou télécharger le QR en SVG ;
- les **photos** ;
- les **prêts** qui concernent l'objet ;
- les **entretiens**, les **rappels** et les **notes** ;
- l'**historique** : création, modifications champ par champ, prêts, remises, retours, déplacements, inventaires.

Le bouton **Prêter / réserver** ouvre une demande de prêt avec l'objet déjà ajouté.

### Créer et modifier un objet

**Nouvel objet** (admins et profs). Les champs principaux :

- **Code** : attribué automatiquement (`INV-0001`, `INV-0002`…). Le préfixe se règle dans la configuration.
- **Désignation** et **catégorie** : obligatoires. La catégorie détermine les champs personnalisés proposés.
- **Suivi** :
  - **individuel**, le cas par défaut : un objet = une fiche (une clarinette, une enceinte) ;
  - **en quantité** : une fiche pour un lot (40 pupitres, des boîtes d'anches). Voir [Consommables et stock](#consommables-et-stock).
- **Tags** : mots-clés libres séparés par des virgules (« étude, concert »). Ils sont créés à la volée.
- **Valeur estimée** : utilisée par le rapport de valeur du parc (assurance, AG). À défaut, c'est le prix d'achat qui compte.

Chaque modification est enregistrée dans l'historique, avec l'ancienne et la nouvelle valeur.

### Statuts et états

Le **statut** indique où en est l'objet :

| Statut | Signification | Qui le change |
|---|---|---|
| Disponible | Au local, prêtable | Automatique au retour, ou à la main |
| En garde | Confié pour une garde | Automatique à la remise |
| Sorti | Parti pour une réservation | Automatique à la remise |
| En entretien | Chez le réparateur, non prêtable | À la main, ou en enregistrant un entretien |
| Réformé | Retiré du parc (vendu, perdu, hors service) | Bouton « Réformer » (admin) |

L'**état** décrit l'objet physique : neuf, bon, usé, à réparer, HS. Il peut être mis à jour au retour d'un prêt.

Un objet **en entretien** ou **réformé** ne peut pas être prêté.

### Photos, notes, entretiens, rappels

- **Photos** : choisissez une image, éventuellement depuis l'appareil photo du téléphone, avec une légende facultative. Les images sont redressées et réduites automatiquement.
- **Notes** : remarques libres, datées et signées. Un prof peut supprimer ses propres notes, un admin toutes.
- **Entretiens** : date, type (révision, réparation, nettoyage…), prestataire, coût, description et **date du prochain entretien**. On peut aussi mettre l'objet « en entretien » ou le rendre disponible au même moment. La date du prochain entretien alimente les rappels et le tableau de bord. Les coûts alimentent le rapport « Coûts d'entretien ».
- **Rappels** : une date et un message (fin de garantie, contrôle, assurance…), avec les destinataires de votre choix. Sans destinataire, ce sont les admins qui le reçoivent.

### Réformer ou supprimer

Réservé aux **admins**.

- **Réformer** retire l'objet du parc sans perdre son historique. C'est l'action normale pour un instrument vendu, perdu ou hors d'usage. Un objet actuellement prêté ne peut pas être réformé. **Réintégrer** le remet en service.
- **Supprimer** efface la fiche. C'est possible seulement si l'objet n'a **jamais été prêté** : sinon, réformez-le.

### Actions groupées

Cochez des objets dans la liste pour :

- les **déplacer** vers un lieu ;
- leur **ajouter des tags** ;
- **imprimer leurs étiquettes**.

## Lieux, catégories, tags et champs personnalisés

- **Lieux** (créés et modifiés par les admins et les profs, supprimés par les admins) : arborescence libre, par exemple « Local de répétition > Armoire bois ». Filtrer sur un lieu inclut ses sous-lieux. Un lieu ne peut être supprimé que s'il est vide (ni objet ni sous-lieu).
- **Catégories** (créées et modifiées par les admins et les profs, supprimées par les admins) : nom, couleur et type. Le type est **instrument**, **matériel** ou **consommable**. Un objet d'une catégorie « consommable » est suivi en quantité.
- **Tags** : libres, créés en tapant leur nom sur une fiche.
- **Champs personnalisés** (admins, menu **Champs perso**) : des champs en plus des champs standard, limités ou non à certaines catégories. Exemples : « Diapason » pour les bois, « Taille » pour les tenues. Les types possibles sont texte, nombre, date, oui / non et liste de choix. Un champ peut être obligatoire. Il apparaît sur la fiche, dans le formulaire et dans les exports.

## Kits

**Menu Kits** (admins et profs). Un kit est un ensemble d'objets prêtés **d'un bloc**, comme une sono ou une batterie.

![Fiche d'un kit](images/kit.png)

- Créez le kit, puis ajoutez-y des objets existants. Un objet appartient à un seul kit à la fois.
- Un objet d'un kit ne peut pas être prêté seul : on prête le kit complet.
- La composition ne peut pas changer pendant un prêt en cours.
- Un kit a son propre QR code. Le scanner pendant un inventaire physique compte tous ses objets.
- Les consommables ne peuvent pas entrer dans un kit.
- Seuls les admins suppriment un kit, et seulement s'il n'a jamais été prêté : sinon, retirez-en les objets si besoin.

## Consommables et stock

**Menu Consommables** (admins et profs).

![Consommables](images/consommables.png)

Les objets **suivis en quantité** se répartissent en deux modes :

- **Consommé** (anches, huile, cordes) : ce qui est distribué ne revient pas. Le stock baisse à la remise.
- **Retournable** (pupitres, câbles) : prêté en quantité, puis rendu. On peut prêter 25 pupitres sur 40 ; les 15 autres restent disponibles sur la même période.

**Mouvements de stock** (bouton sur la fiche ou dans la liste) :

| Motif | Effet |
|---|---|
| Achat / réassort | Ajoute la quantité |
| Retour en stock | Ajoute la quantité |
| Distribution | Retire la quantité |
| Perte / casse | Retire la quantité |
| Correction d'inventaire | Remplace par la quantité réellement comptée |

Le stock ne peut jamais devenir négatif. Avec un **seuil de stock bas**, les admins reçoivent une alerte quand on l'atteint, puis une relance par semaine. L'objet apparaît aussi dans « Stocks bas » sur le tableau de bord.

## Les prêts

**Menu Prêts** (admins et profs), **Mes prêts** (tout le monde).

![Liste des prêts](images/prets.png)

### Gardes et réservations

- **Garde** : longue durée, sans date de fin obligatoire. Exemple : un instrument confié à un élève pour l'année.
- **Réservation** : une période précise, avec début et fin obligatoires. Exemple : la sono pour le concert de printemps.

### Le cycle d'un prêt

```
            ┌──────────┐ validation ┌────────┐  remise  ┌──────────┐  retour complet  ┌───────┐
 demande ──▶│ Demandé  │───────────▶│ Validé │─────────▶│ En cours │─────────────────▶│ Rendu │
            └──────────┘   (admin)  └────────┘          └──────────┘                  └───────┘
               │    │                   │                  │   ▲
               │    └─ refus ──▶ Refusé │                  └───┘ retour partiel
               └──── annulation ────────┴──▶ Annulé
```

- Une **demande ne bloque rien** : deux demandes peuvent viser le même instrument. La personne qui demande en est avertie, et l'admin tranche à la validation.
- Un prêt **validé** ou **en cours** bloque le matériel sur sa période. Une deuxième validation sur la même période est refusée, avec le nom du prêt en conflit.
- Un prêt en cours dont la date de fin est dépassée continue de bloquer le matériel jusqu'au retour effectif. Il apparaît « En retard ».

### Demander un prêt

**Nouvelle demande** (admins et profs).

![Nouvelle demande de prêt](images/nouveau-pret.png)

1. **Type** : garde ou réservation.
2. **Emprunteur** : la personne qui part avec le matériel. Si elle n'existe pas encore, **+ Nouvelle fiche emprunteur** la crée sans quitter la demande.
3. **Garant** : un prof qui répond du matériel. Un prof est automatiquement garant de ses propres demandes ; un admin choisit le garant, ou aucun.
4. **Motif** (facultatif), **début**, **fin** (obligatoire pour une réservation).
5. **Matériel** : cherchez par code, nom ou numéro de série, puis ajoutez des objets, des kits, ou une quantité pour les objets suivis en quantité. La disponibilité est vérifiée au fur et à mesure.
6. **Caution et commentaire** (facultatif) : montant de la caution, informations utiles à l'admin.

Les admins sont notifiés de la nouvelle demande.

### Valider ou refuser

Réservé aux **admins**, depuis le tableau de bord ou la fiche du prêt.

- **Valider** revérifie la disponibilité au moment du clic. L'emprunteur, le garant et le demandeur sont notifiés.
- **Refuser** demande un motif facultatif, transmis au garant et au demandeur.

Les demandes en attente depuis plus de 2 jours font l'objet d'une relance quotidienne aux admins.

### Remettre le matériel

**Remettre le matériel**, sur un prêt validé : par un admin, ou par le prof garant du prêt.

![Détail d'un prêt validé](images/pret-concert.png)

- Les objets passent « en garde » ou « sortis ». Leur état au départ est noté.
- Les consommables distribués sont retirés du stock et n'ont pas à être rendus.

### Récupérer le matériel

**Retour**, sur un prêt en cours : par un admin, ou par le prof garant.

- Cochez ce qui est rendu. Le **retour peut être partiel** : le reste demeure en cours.
- Pour chaque objet rendu, indiquez son état s'il a changé (usé, à réparer…). La fiche est mise à jour.
- Cochez **caution rendue** le cas échéant.
- Quand tout est rendu, le prêt passe « Rendu » et les objets redeviennent disponibles.

### Prolonger, raccourcir, annuler

- **Modifier la date de fin** (admins) : sur une demande, un prêt validé ou en cours. Une prolongation n'est acceptée que si le matériel reste libre.
- **Annuler** : sur une demande ou un prêt validé, pas encore remis. C'est possible pour un admin, pour la personne qui a fait la demande et pour le garant. Les autres personnes concernées sont notifiées.

**Mes prêts** liste les prêts dont vous êtes l'emprunteur et, pour les profs, ceux dont vous êtes garant.

## QR codes, étiquettes et scanner

Chaque objet et chaque kit a un **QR code unique**. Il contient l'adresse publique de l'appli suivie d'un identifiant secret, par exemple `https://inventaire.exemple.fr/q/3f9a…`.

- **Scanner avec l'appareil photo du téléphone** ouvre directement la fiche, après connexion si besoin. Un emprunteur qui scanne voit une fiche réduite et ses éventuels prêts sur cet objet.
- **Page Scanner** (admins et profs) : scan avec la caméra, ou saisie d'un code (`INV-0042`), ou lecture par une douchette USB.
- **Étiquettes** : depuis une fiche, un kit ou une sélection d'objets dans la liste. Trois formats de planches A4 adhésives (type Avery) sont proposés :
  - petit, 38 × 21 mm, 65 par page ;
  - moyen, 63,5 × 38 mm, 21 par page ;
  - grand, 99 × 68 mm, 8 par page.

  Un **décalage** permet de réutiliser une planche entamée. Il y a 500 étiquettes au maximum par impression.

> **Avant d'imprimer**, vérifiez que l'adresse publique (`BASE_URL`) est l'adresse définitive : elle est inscrite dans chaque QR code. L'appli prévient si elle vaut encore `localhost`.

La caméra du navigateur exige une connexion **HTTPS**, sauf sur `localhost`.

## Inventaire physique

**Menu Inventaires**. Les admins et les profs scannent ; seuls les admins ouvrent, clôturent et suppriment un inventaire.

![Inventaire physique en cours](images/inventaire-physique.png)

1. **Ouvrir un inventaire** : un nom, et un périmètre facultatif, par lieux (sous-lieux compris) et / ou par catégories. Sans périmètre, tout le parc est attendu, sauf les objets réformés.
2. **Scanner** ce qui est physiquement présent : caméra, douchette ou saisie du code. On peut préciser le lieu où l'on scanne. Chaque scan est classé :
   - **trouvé** : l'objet était attendu ;
   - **inattendu** : l'objet est hors du périmètre.
3. La page montre en direct ce qui **reste à trouver** et ce qui est **prêté**, donc absent normalement.
4. **Clôturer** : chaque objet attendu non scanné est enregistré **manquant**, ou **prêté** s'il est sorti. Les manquants sont notés dans leur historique. L'option **Mettre à jour le lieu** déplace les objets trouvés ailleurs que prévu vers le lieu où ils ont été scannés.

Le rapport clôturé est figé et exportable en CSV.

## Calendrier et abonnement iCal

**Menu Calendrier** (tout le monde).

![Calendrier des prêts](images/calendrier.png)

- Vues **mois**, **semaine** et **planning**. Les couleurs distinguent les prêts demandés, validés, en cours, en retard et rendus, ainsi que les entretiens.
- Un emprunteur ne voit que ses propres prêts.
- **Abonnement iCal** : le lien personnel affiché sur la page s'ajoute dans Google Agenda, Outlook ou Apple Calendrier. Il contient les prêts validés et en cours, de 3 mois en arrière à 1 an en avant.
  - Ce lien donne accès à vos prêts sans mot de passe : ne le partagez pas.
  - **Nouveau lien** invalide l'ancien.

## Notifications et rappels automatiques

La cloche en haut à droite indique les notifications non lues. Si un serveur SMTP est configuré, les notifications partent aussi par email.

Notifications liées aux actions :

- nouvelle demande de prêt (admins) ;
- prêt validé, refusé, annulé, ou date de fin modifiée (personnes concernées) ;
- stock passé sous le seuil (admins).

Rappels automatiques, calculés toutes les 15 minutes :

| Rappel | Destinataires | Fréquence |
|---|---|---|
| Rappel manuel arrivé à échéance | Destinataires choisis, sinon admins | Une fois |
| Entretien prévu dans les 7 jours ou dépassé | Admins | Une fois par échéance |
| Matériel à récupérer dans les 24 h | Emprunteur et garant | Une fois |
| Retour attendu dans les 24 h | Emprunteur et garant | Une fois |
| Prêt en retard | Emprunteur, garant et admins | Une fois par jour |
| Réservation validée jamais remise, 2 jours après le début | Admins | Une fois |
| Demande en attente depuis plus de 2 jours | Admins | Une fois par jour |
| Stock bas | Admins | Une fois par semaine |

## Rapports

**Menu Rapports** (admins et profs). Choisissez la période, puis imprimez la page (**Imprimer / PDF**) ou exportez chaque tableau en **CSV**.

![Rapports](images/rapports.png)

- **Indicateurs** : objets au parc, valeur estimée, prêts en cours, prêts sur la période, part des retours à l'heure.
- **Valeur du parc par catégorie** : valeur estimée, ou prix d'achat à défaut, hors objets réformés. Utile pour l'assurance et l'AG.
- **Objets les plus prêtés** sur la période.
- **Retours** : prêts rendus à l'heure ou en retard, avec une marge de 24 h.
- **Gardes en cours par personne**.
- **Coûts d'entretien par catégorie**.
- **Objets jamais sortis** sur la période.

## Import et export

**Menu Import / export**.

- **Export** (admins et profs) : tout l'inventaire, ou le résultat d'un filtre, en **Excel** ou en **CSV**. Les champs personnalisés sont inclus. Le CSV utilise le point-virgule comme séparateur et s'ouvre directement dans Excel.
- **Import** (admins) : un fichier `.xlsx` ou `.csv`, une ligne par objet, avec une ligne d'en-têtes, 5 000 lignes au maximum.
  1. L'appli **devine l'association des colonnes** d'après leurs en-têtes : « Désignation », « Instrument », « N° de série », « Emplacement »… Vérifiez-la et corrigez-la si besoin.
  2. Les options :
     - catégorie par défaut ;
     - création automatique des catégories et des lieux manquants (un lieu peut s'écrire « Local > Armoire bois ») ;
     - mise à jour des objets existants (repérés par leur code).
  3. **Faire un essai** : l'import est simulé, puis annulé. Vous voyez combien d'objets seraient créés ou mis à jour, et les erreurs ligne par ligne.
  4. **Importer** pour de bon.

Les lignes en erreur sont ignorées, les autres importées. Les dates (`15/03/2024`, `2024-03-15`) et les montants (`1 150,00 €`) sont reconnus sous leurs formes courantes.

## Personnes et comptes

**Menu Personnes**.

![Liste des personnes](images/utilisateurs.png)

- **Les profs** peuvent créer des **fiches emprunteur**, sans accès à l'appli, pour pouvoir leur prêter du matériel.
- **Les admins** créent et modifient tous les comptes :
  - **Rôle** : administrateur, professeur ou emprunteur.
  - **Peut se connecter** : ouvre l'accès, ce qui demande un email. L'appli génère alors un **mot de passe provisoire**, affiché **une seule fois**. Transmettez-le à la personne, qui devra le changer à sa première connexion.
  - **Nouveau mot de passe provisoire** : en cas d'oubli.
  - Décocher **Peut se connecter** coupe l'accès immédiatement, y compris pour une personne déjà connectée. L'historique et les prêts sont conservés.
- Un admin ne peut pas retirer ses propres droits d'administrateur, pour éviter de se retrouver sans aucun admin.

Avec la connexion OIDC et des groupes configurés, les rôles peuvent aussi venir du fournisseur d'identité : voir [Rôles d'après les groupes](oidc-authentik.md#rôles-daprès-les-groupes).

## Paramètres

**Menu Paramètres** (admins). La page affiche la configuration en cours : nom de l'association, adresse publique, préfixe des codes, état des emails. Elle propose deux actions :

- **M'envoyer un email de test**, pour vérifier la configuration SMTP ;
- **Lancer les rappels maintenant**, sans attendre le prochain passage automatique.

Ces réglages se modifient dans le fichier de configuration du serveur : voir [Configuration](configuration.md).

## Installer l'appli sur un téléphone

L'inventaire s'installe comme une application, avec une icône sur l'écran d'accueil et un affichage plein écran :

- **iPhone (Safari)** : bouton Partager, puis **Sur l'écran d'accueil** ;
- **Android (Chrome)** : menu ⋮, puis **Installer l'application** ou **Ajouter à l'écran d'accueil**.

Sur téléphone, une barre d'onglets en bas d'écran donne accès à l'accueil, aux prêts, au scanner et au matériel.

## Qui peut faire quoi

| Action | Admin | Prof | Emprunteur |
|---|:---:|:---:|:---:|
| Voir l'inventaire, les kits, les lieux | ✅ | ✅ | — |
| Créer et modifier objets, kits, lieux, catégories, photos, tags | ✅ | ✅ | — |
| Réformer ou supprimer un objet ; supprimer un kit, un lieu, une catégorie | ✅ | — | — |
| Ajouter une note | ✅ | ✅ | — |
| Supprimer une note | ✅ | ses notes | — |
| Enregistrer un entretien, un rappel | ✅ | ✅ | — |
| Supprimer un entretien | ✅ | ceux qu'il a saisis | — |
| Mouvements de stock | ✅ | ✅ | — |
| Champs personnalisés | ✅ | — | — |
| Créer une fiche emprunteur (sans accès) | ✅ | ✅ | — |
| Gérer les comptes, rôles et accès | ✅ | — | — |
| Demander un prêt | ✅ | ✅ (garant) | — |
| Voir tous les prêts | ✅ | ✅ | ses prêts |
| Valider ou refuser un prêt, modifier sa date de fin | ✅ | — | — |
| Remettre et récupérer le matériel | ✅ | s'il est garant | — |
| Annuler un prêt (avant la remise) | ✅ | s'il l'a demandé ou en est garant | — |
| Scanner, imprimer des étiquettes | ✅ | ✅ | — |
| Scanner pendant un inventaire physique | ✅ | ✅ | — |
| Ouvrir, clôturer, supprimer un inventaire physique | ✅ | — | — |
| Rapports et export | ✅ | ✅ | — |
| Import | ✅ | — | — |
| Paramètres | ✅ | — | — |
| Mes prêts, calendrier, notifications | ✅ | ✅ | ✅ |

## Questions fréquentes

**Deux profs ont demandé le même instrument pour la même date. Que se passe-t-il ?**
Les deux demandes sont enregistrées, avec un avertissement. L'admin valide l'une ; l'autre ne pourra plus être validée sur cette période.

**Un élève rend son instrument abîmé.**
Au retour, indiquez le nouvel état (« à réparer »). Ensuite, enregistrez un entretien en mettant l'objet « en entretien » pour qu'il ne soit plus prêté.

**On a perdu un objet.**
Un admin le **réforme** : il sort du parc et des rapports de valeur, mais son historique reste consultable.

**L'élève ne se connectera jamais à l'appli.**
Une **fiche emprunteur** sans accès suffit pour lui prêter du matériel.

**Les QR codes imprimés mènent à une mauvaise adresse.**
L'adresse publique a changé après l'impression. Remettez l'ancienne adresse en redirection sur le reverse proxy, ou réimprimez les étiquettes.

**Je ne reçois pas les emails.**
Un admin peut tester l'envoi dans **Paramètres**. Sans serveur SMTP configuré, les notifications restent dans l'appli.
