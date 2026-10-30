# Sécurité

## Signaler une faille

**N'ouvrez pas de ticket public** pour une faille de sécurité : elle serait visible de tous avant d'être corrigée.

Utilisez le signalement privé de GitHub : onglet **Security** du dépôt, puis **Report a vulnerability**. Décrivez :

- la faille et son impact (qui peut faire quoi qu'il ne devrait pas pouvoir faire) ;
- les étapes pour la reproduire ;
- la version ou le commit concerné.

Vous recevrez une réponse dès que possible. Le projet est maintenu bénévolement : merci de laisser un délai raisonnable avant toute publication.

## Versions prises en charge

Seule la dernière version de la branche principale reçoit des correctifs. Mettez à jour régulièrement : [docs/installation.md › Mise à jour](docs/installation.md#mise-à-jour).

## Bonnes pratiques d'hébergement

La sécurité d'une installation dépend aussi de son hébergement : HTTPS, `SECRET_KEY` secrète, sauvegardes, comptes désactivés au départ des personnes… Voir la [liste de contrôle de sécurité](docs/installation.md#liste-de-contrôle-de-sécurité). Les mesures intégrées à l'appli sont décrites dans [docs/architecture.md › Sécurité](docs/architecture.md#sécurité).
