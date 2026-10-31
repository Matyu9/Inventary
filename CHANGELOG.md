# Journal des versions

Les changements notables du projet sont consignés ici. Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## Non publié

### Ajouté

- **Documents joints** (fiches objet et kit) : factures, devis, notices, garanties, certificats, assurances, en PDF, image, Word, Excel, OpenDocument ou texte. Le contenu réel du fichier est vérifié. Deux nouveaux droits, « Voir les documents joints » et « Ajouter et supprimer des documents joints », donnés par défaut aux profs. Une pastille 📎 signale les objets qui en ont dans la liste.
- **Corbeille** (bouton en haut de l'inventaire et des kits, admin) : un objet ou un kit supprimé y reste 30 jours, invisible partout ailleurs, puis il est effacé automatiquement. On peut le restaurer tel quel (un kit récupère ses objets) ou le supprimer définitivement. Seul un administrateur peut vider la corbeille.
- **État de l'installation** (Paramètres, admin) : page `/sante` et sa version JSON `/health` qui vérifient la base, les migrations, l'espace disque, le passage des rappels automatiques et les emails. Réponse `503` en cas de problème, `404` pour qui ne gère pas les paramètres.
- **Déconnecter tout le monde** (Paramètres, admin) : ferme toutes les sessions sauf la sienne, y compris celles ouvertes avec « Rester connecté ». La page affiche aussi le nombre de sessions ouvertes.
- **Pages d'erreur** soignées (400, 403, 404, 405, 413, 429, 500), avec un message en français et un lien de retour. Une page expirée (CSRF) l'explique clairement. La page 500 donne une référence à retrouver dans les logs et n'affiche aucun détail technique.
- Une action HTMX refusée ou impossible affiche un bandeau au lieu d'échouer sans rien montrer.
- **Emails repensés** : version HTML aux couleurs de l'appli (avec une version texte), salutation, objet clair et bouton vers la page concernée. Les emails de prêt récapitulent le matériel, les dates, l'emprunteur et le garant ; ceux d'un objet, son emplacement et son stock.
- **Textes des emails modifiables** (Paramètres → Emails, admin) : objet, introduction et bouton de chaque type d'email, avec des variables (`{emprunteur}`, `{debut}`…) et un aperçu. Un bouton rétablit les textes par défaut.
- **QR code agrandi** : un clic sur le QR d'une fiche objet ou kit l'affiche en grand dans une fenêtre.
- **Étiquettes de kits en masse** (page Kits) : étiquettes de la sélection ou de tous les kits, avec en option celles des objets qu'ils contiennent.
- **Vérification du matériel d'un prêt** (« Rien oublié ? ») : sur un prêt validé ou en cours, on scanne tout ce qu'on a en main (en fin de concert, avant de quitter la salle). La page montre les manquants et les pointés, se met à jour quand plusieurs personnes scannent en même temps, gère les kits et les objets en quantité, et permet de pointer à la main.
- **Liste du matériel à imprimer** pour chaque prêt : feuille A4 avec cases « Départ » et « Retour », contenu des kits, lieux de rangement, signatures et QR code vers la vérification.
- **Rôles sur mesure** (menu Rôles, admin) : créer des rôles (« Régisseur », « Secrétariat »…) en cochant leurs droits, dupliquer un rôle existant, renommer les rôles de base et modifier les droits du Professeur et de l'Emprunteur. L'Administrateur garde toujours tous les droits. Chaque rôle peut indiquer des groupes OIDC dont les membres le reçoivent à la connexion.
- **Gestion des comptes** :
  - **Invitations** : ouvrir l'accès envoie un lien par email pour choisir son mot de passe (valable 7 jours, à usage unique). Sans email configuré, le lien s'affiche pour être transmis. Le mot de passe provisoire reste disponible depuis la fiche.
  - **Mot de passe oublié** depuis la page de connexion : lien de réinitialisation par email (1 heure), sans révéler si le compte existe, avec limitation des demandes.
  - **Mon compte** : profil, changement d'email confirmé par un lien envoyé à la nouvelle adresse (l'ancienne est prévenue), mot de passe, et liste des appareils connectés avec déconnexion à distance.
  - **Fiche détaillée** d'une personne : coordonnées, statut et dernière connexion, prêts, appareils connectés, journal.
  - **Photos de profil** : chacun ajoute la sienne dans Mon compte, les gestionnaires de comptes depuis la fiche. Recadrées en carré (256 px, sans métadonnées EXIF), elles remplacent les initiales dans le menu, la liste des personnes et les fiches, et ne sont servies qu'aux personnes connectées.
  - **Cycle de vie** : archiver une personne qui part (historique conservé), supprimer une fiche jamais utilisée, fusionner deux doublons (avec suggestions), actions groupées (ouvrir ou couper l'accès, archiver, changer de rôle) et filtre par statut.
- **Icônes Papicons** (association Papillon, licence MIT) dans toute l'appli.
- **« Enregistrer et créer un autre »** à la création d'un objet : le formulaire suivant reprend la catégorie et le lieu, pour enchaîner les saisies.

### Sécurité

- L'adresse des photos (`/objets/fichiers/…`) ne sert plus que les photos d'objets, et plus le reste du dossier des fichiers envoyés.

- Un gestionnaire de comptes qui n'est pas administrateur ne peut plus générer de mot de passe provisoire pour un compte administrateur (ce qui permettait de le prendre).
- Seul un administrateur peut donner ou retirer le rôle d'administrateur, modifier le compte d'un admin ou gérer les rôles.
- **Sessions révocables** : la déconnexion invalide le cookie de l'appareil, même copié ailleurs. Un changement de mot de passe déconnecte les autres appareils. La réinitialisation par un admin et la désactivation déconnectent tous les appareils. Chacun devra se reconnecter une fois après la mise à jour.
- L'export CSV d'un inventaire physique neutralise les formules (nom d'objet commençant par `=`, `+`, `-` ou `@`), comme les autres exports.

### Corrigé

- Un identifiant, un numéro de page ou une date hors limites renvoie une page 404 au lieu d'une erreur 500. Les rapports acceptent n'importe quelle période.
- Les mouvements de stock sont plafonnés à 1 000 000.
- Les tags que plus aucun objet ne porte sont supprimés et disparaissent du filtre.
- Les notifications n'écrivent plus « validé(e) », « jour(s) » ni « restant(s) ».

## 0.1.0 — 2026-10-30

Première version.

### Ajouté

- **Inventaire** :
  - fiches objets avec photos, notes, champs personnalisés par catégorie, tags et lieux en arborescence ;
  - historique complet de chaque fiche ;
  - recherche sans accents ;
  - actions groupées.
- **Prêts** :
  - gardes et réservations ;
  - circuit demande, puis validation, puis remise, puis retour (partiel possible) ;
  - prof garant ;
  - caution ;
  - détection des conflits, y compris entre validations simultanées.
- **Kits** prêtés d'un bloc, et **consommables** suivis en stock avec seuil d'alerte.
- **QR codes**, planches d'étiquettes A4 et page de scan (caméra, douchette, saisie).
- **Inventaire physique** par scan, avec rapport trouvé, manquant, inattendu ou prêté.
- **Calendrier** et flux iCal personnel.
- **Rappels automatiques** et notifications, dans l'appli et par email (SMTP facultatif).
- **Rapports** : valeur du parc, utilisation, retours, gardes, coûts d'entretien, objets inactifs.
- **Import et export** CSV et Excel, avec association des colonnes et essai à blanc.
- **Rôles** admin, prof et emprunteur ; fiches emprunteur sans accès ; mots de passe provisoires.
- **Connexion OIDC** facultative (Authentik, Keycloak…), avec rôles déduits des groupes.
- Interface adaptée au téléphone et installable (PWA).
- Déploiement Docker : application et planificateur de rappels.
- Documentation complète, données de démo et captures d'écran.
