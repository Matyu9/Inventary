# Journal des versions

Les changements notables du projet sont consignés ici. Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## Non publié

### Ajouté

- **Déconnecter tout le monde** (Paramètres, admin) : ferme toutes les sessions sauf la sienne, y compris celles ouvertes avec « Rester connecté ». La page affiche aussi le nombre de sessions ouvertes.
- **Pages d'erreur** soignées (400, 403, 404, 405, 413, 429, 500), avec un message en français et un lien de retour. Une page expirée (CSRF) l'explique clairement. La page 500 donne une référence à retrouver dans les logs et n'affiche aucun détail technique.
- Une action HTMX refusée ou impossible affiche un bandeau au lieu d'échouer sans rien montrer.

### Sécurité

- **Sessions révocables** : la déconnexion invalide le cookie de l'appareil, même copié ailleurs. Un changement de mot de passe déconnecte les autres appareils. La réinitialisation par un admin et la désactivation déconnectent tous les appareils. Chacun devra se reconnecter une fois après la mise à jour.
- L'export CSV d'un inventaire physique neutralise les formules (nom d'objet commençant par `=`, `+`, `-` ou `@`), comme les autres exports.

### Corrigé

- Un identifiant, un numéro de page ou une date hors limites renvoie une page 404 au lieu d'une erreur 500. Les rapports acceptent n'importe quelle période.
- Les mouvements de stock sont plafonnés à 1 000 000.
- Les tags que plus aucun objet ne porte sont supprimés et disparaissent du filtre.

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
