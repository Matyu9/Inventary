# Journal des versions

Les changements notables du projet sont consignés ici. Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## Non publié

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
