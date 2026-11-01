# Liste des fonctionnalités

Inventaire complet de ce que fait l'appli, domaine par domaine. Pour savoir comment s'en servir, écran par écran, voir le [guide d'utilisation](guide-utilisateur.md). Pour ce qui a changé d'une version à l'autre, voir le [journal des versions](../CHANGELOG.md).

## Sommaire

- [Inventaire](#inventaire)
- [Kits et consommables](#kits-et-consommables)
- [Prêts](#prêts)
- [QR codes, scan, signalements](#qr-codes-scan-signalements)
- [Inventaire physique](#inventaire-physique)
- [Tableau de bord, calendrier, notifications](#tableau-de-bord-calendrier-notifications)
- [Rapports, import, export](#rapports-import-export)
- [Comptes et personnes](#comptes-et-personnes)
- [Rôles et permissions](#rôles-et-permissions)
- [Administration et personnalisation](#administration-et-personnalisation)
- [Sécurité et technique](#sécurité-et-technique)

## Inventaire

- **Fiche objet** :
  - désignation, catégorie, marque, modèle, n° de série, description ;
  - état : neuf, bon, usé, à réparer, HS ;
  - statut : disponible, en garde, sorti, en entretien, réformé ;
  - lieu, tags, date et prix d'achat, valeur estimée.
- **Code automatique** (`INV-0042`, préfixe configurable).
- **Champs personnalisés** par catégorie (texte, nombre, date, oui/non, liste), obligatoires ou non, dans l'ordre choisi.
- **Catégories** (instrument, matériel, consommable) et **lieux en arborescence** (« Local > Armoire bois »), avec une fiche par lieu.
- **Tags** sans doublon, sans tenir compte des accents ni des majuscules. Les tags que plus aucun objet ne porte sont supprimés.
- **Photos** avec légende.
- **Documents joints** aux objets et aux kits :
  - factures, devis, notices, garanties, certificats, assurances ;
  - en PDF, image, Word, Excel, OpenDocument ou texte, avec vérification du contenu réel du fichier ;
  - une pastille 📎 dans la liste signale les objets qui en ont.
- **Notes** : chacun supprime les siennes, et avec un droit dédié celles des autres.
- **Entretiens** avec coût et prochaine échéance.
- **Rappels** manuels programmés sur un objet.
- **Historique complet** de chaque fiche : qui a changé quoi, et quand.
- **Recherche sans accents** (« flute » trouve « Flûte »), filtres par catégorie, tag, lieu, statut et état, pagination.
- **Actions groupées** : déplacer, ajouter des tags, imprimer les étiquettes de la sélection.
- **« Enregistrer et créer un autre »** : le formulaire suivant reprend la catégorie et le lieu.
- **Réformer** ou **supprimer** un objet.
- **Corbeille** :
  - un objet ou un kit supprimé y reste 30 jours, invisible partout ailleurs, puis il est effacé automatiquement ;
  - on peut le restaurer tel quel (un kit récupère ses objets) ou le supprimer définitivement ;
  - seul un admin peut vider la corbeille.

## Kits et consommables

- **Kits** (sono, batterie…) prêtés d'un bloc, avec leur QR code et leurs documents.
  - Un objet appartient à un seul kit.
  - La composition est bloquée pendant un prêt.
- **Consommables** suivis en quantité, en deux modes :
  - **consommé** (anches, huile, cordes) : ce qui est distribué ne revient pas ;
  - **retournable** (pupitres, câbles) : prêté en quantité, puis rendu.
- **Mouvements de stock** : achat, retour en stock, distribution, perte ou casse, correction d'inventaire. Le stock ne devient jamais négatif.
- **Seuil de stock bas** : alerte, puis une relance par semaine.

## Prêts

- **Gardes** (un instrument confié pour l'année) et **réservations** (une période précise, par exemple un concert).
- **Circuit** : demande → validation, ou refus avec motif → remise → retour.
  - Le retour peut être partiel, met à jour l'état des objets et indique si la caution est rendue.
- **Prof garant** : il remet et récupère le matériel des prêts dont il est garant.
- **Fiche emprunteur** créée sans quitter la demande.
- **Recherche du matériel** pendant la demande (objets, kits, quantités), avec vérification de la disponibilité au fur et à mesure.
- **Détection des conflits**, y compris quand deux admins valident en même temps.
- **Demandes des emprunteurs** pour eux-mêmes :
  - le garant accepte ou refuse en premier, puis un admin valide ;
  - un onglet « Attente du garant » regroupe ces demandes ;
  - le garant est relancé chaque jour au bout de 3 jours ;
  - les noms des autres emprunteurs sont masqués dans les messages de conflit.
- **Modifier la date de fin** (prolonger, raccourcir) et **annuler** une demande ou un prêt.
- **« Rien oublié ? »** : vérification par scan du matériel d'un prêt, par exemple en fin de concert.
  - Plusieurs personnes peuvent scanner en même temps.
  - Gère les kits et les objets en quantité.
  - Pointage à la main possible, et bouton pour recommencer.
- **Liste du matériel à imprimer** (A4) : cases départ et retour, contenu des kits, lieux de rangement, signatures, QR code vers la vérification.
- **Liste des prêts** avec onglets, filtre par type et recherche.
- **Mes prêts** : ceux dont on est l'emprunteur, et ceux dont on est garant.

## QR codes, scan, signalements

- **QR code unique** par objet et par kit, affichable en grand dans une fenêtre.
- **Planches d'étiquettes A4** en trois formats (65, 21 ou 8 par page), avec décalage pour finir une planche entamée.
  - Étiquettes de kits en masse, avec en option celles des objets qu'ils contiennent.
- **Page Scanner** : caméra, douchette USB ou saisie du code.
- **Page publique d'un QR code**, sans compte :
  - elle ne montre que le nom et le code de l'objet ;
  - deux boutons, **Signaler un problème** et **J'ai trouvé cet objet**, avec nom et contact facultatifs ;
  - un champ anti-robots et une limite d'envois protègent contre les abus.
- **Fiche réduite** pour un emprunteur connecté qui scanne un objet, avec ses éventuels prêts dessus.
- **Menu Signalements** :
  - une pastille compte les signalements à traiter, et un bandeau s'affiche sur la fiche de l'objet ;
  - marquer comme traité avec une note, ou rouvrir ;
  - pour un objet trouvé, l'emprunteur et le garant sont prévenus.

## Inventaire physique

- **Ouverture** avec un périmètre facultatif, par lieux (sous-lieux compris) et/ou par catégories.
- **Scan** par caméra, douchette ou saisie. Chaque scan est classé trouvé ou inattendu, et la progression s'affiche en direct.
- **Clôture** : chaque objet non scanné est noté manquant, ou prêté s'il est sorti.
  - Option pour mettre à jour le lieu des objets trouvés ailleurs que prévu.
- **Rapport figé**, exportable en CSV.

## Tableau de bord, calendrier, notifications

- **Tableau de bord à la carte** : chacun affiche, masque et réordonne ses blocs, avec retour à la disposition par défaut.
  - Blocs : objets par statut, demandes à confirmer comme garant, demandes à valider, prêts en retard, sorties des 7 prochains jours, entretiens à prévoir, stocks bas.
- **Accueil d'un emprunteur** : ses prêts en cours.
- **Calendrier** des prêts et des entretiens, en vues mois, semaine et planning, avec une couleur par statut.
- **Flux iCal personnel** pour Google Agenda, Outlook ou Apple Calendrier, avec un bouton pour générer un nouveau lien.
- **Notifications dans l'appli** (cloche), et **par email** si un serveur SMTP est configuré.
- **Rappels automatiques**, calculés toutes les 15 minutes :
  - rappels manuels arrivés à échéance ;
  - entretiens prévus dans les 7 jours ou dépassés ;
  - matériel à récupérer et retours attendus dans les 24 h ;
  - prêts en retard, une relance par jour ;
  - réservations validées jamais remises ;
  - demandes en attente, côté admin et côté garant ;
  - stocks bas.
- **Emails HTML** aux couleurs de l'association, avec une version texte et un récapitulatif du prêt ou de l'objet.
- **Textes des emails modifiables** : variables, aperçu, retour aux textes par défaut.

## Rapports, import, export

- **Indicateurs** de la période : objets au parc, valeur, prêts en cours, prêts sur la période, part des retours à l'heure.
- **Rapports** :
  - valeur du parc par catégorie, pour l'assurance et l'AG ;
  - objets les plus prêtés ;
  - retours à l'heure ou en retard ;
  - gardes en cours par personne ;
  - coûts d'entretien par catégorie ;
  - objets jamais sortis.
- **Impression / PDF** de la page, et export CSV de chaque tableau.
- **Export de l'inventaire** en Excel ou CSV, complet ou filtré, avec les champs personnalisés.
- **Import Excel ou CSV** :
  - jusqu'à 5 000 lignes ;
  - colonnes associées automatiquement d'après les en-têtes, à vérifier ;
  - création des catégories et des lieux manquants, mise à jour des objets existants ;
  - **essai à blanc** avant l'import réel.

## Comptes et personnes

- **Connexion** par mot de passe, avec « Rester connecté ».
- **Invitations** par email (lien valable 7 jours) et **mot de passe oublié** (lien valable 1 heure).
- **Mot de passe provisoire** en dernier recours.
- **Mon compte** :
  - profil et photo ;
  - changement d'email confirmé par un lien ;
  - mot de passe ;
  - appareils connectés, avec déconnexion à distance.
- **Affichage personnel** : thème clair, sombre ou automatique, taille du texte, page d'accueil après la connexion.
- **Fiches emprunteurs sans accès**, que les profs peuvent créer.
- **Fiche détaillée** d'une personne : coordonnées, statut, dernière connexion, prêts, appareils connectés, journal.
- **Statuts** : actif, jamais connecté, invitation en attente, sans accès, archivé. La liste se filtre par statut et par rôle.
- **Archiver ou désarchiver**, **supprimer** une fiche jamais utilisée, **fusionner** deux doublons (avec suggestions).
- **Actions groupées** : ouvrir ou couper l'accès, archiver, changer le rôle.
- **Photos de profil** recadrées en carré, sans métadonnées EXIF, servies aux seules personnes connectées.

## Rôles et permissions

- **3 rôles de base** (Administrateur, Professeur, Emprunteur) et **40 droits** répartis en 4 groupes.
- **Rôles sur mesure** : créer, dupliquer, renommer, supprimer.
  - Les droits du Professeur et de l'Emprunteur se modifient.
  - L'Administrateur garde toujours tous les droits.
- **Rôles déduits des groupes OIDC** à la connexion.
- **Garde-fous** :
  - seul un admin donne ou retire le rôle d'administrateur et gère les comptes des admins ;
  - un admin ne peut pas retirer ses propres droits d'administrateur.

## Administration et personnalisation

- **Apparence** :
  - nom et sous-titre de l'association ;
  - logo, qui sert aussi d'icône d'onglet ;
  - 7 palettes de couleurs, chacune avec sa variante sombre.
- **Paramètres** :
  - email de test ;
  - lancer les rappels maintenant ;
  - déconnecter tout le monde, avec le nombre de sessions ouvertes.
- **État de l'installation** (`/sante`, et `/health` en JSON) : base, migrations, espace disque, planificateur, emails.
- **Commandes CLI** : `flask create-admin`, `flask seed`, `flask run-reminders`.

## Sécurité et technique

- **Connexion OIDC** facultative (Authentik, Keycloak, Google, Microsoft…).
- **Sessions révocables** : un changement de mot de passe ou une désactivation déconnecte les appareils.
- **Protection CSRF** et **limitation des demandes** (connexion, mot de passe oublié, signalements).
- **Content-Security-Policy** en Report-Only par défaut, avec collecte des violations sur `/csp-report`, et aucun style en ligne dans les pages.
- **Exports CSV** qui neutralisent les formules.
- **Pages d'erreur** en français (400, 403, 404, 405, 413, 429, 500). La page 500 donne une référence à retrouver dans les logs.
- **Bandeau d'erreur** quand une action HTMX est refusée ou impossible.
- **Appli installable** (PWA) :
  - barre d'onglets en bas sur téléphone ;
  - démarrage sur `/accueil` ;
  - fichiers statiques versionnés pour toujours charger la dernière version.
- **Déploiement** : Docker (application et planificateur), SQLite, migrations Alembic, données de démo.
- **Icônes Papicons**.
