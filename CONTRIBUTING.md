# Contribuer

Merci de votre intérêt pour Pupitre ! Les signalements de bugs, les idées et les propositions de code sont les bienvenus.

## Signaler un bug

Ouvrez un ticket (*issue*) avec le modèle « Bug ». Indiquez :

- ce que vous faisiez, et avec quel rôle (admin, prof, emprunteur) ;
- ce qui s'est passé, et ce que vous attendiez ;
- le navigateur et l'appareil (ordinateur, téléphone) ;
- si possible, le message d'erreur des journaux (`docker compose logs app`).

**N'y mettez aucune donnée personnelle** (noms d'élèves, emails, numéros de téléphone) ni aucun secret (`.env`, mots de passe).

Pour une **faille de sécurité**, n'ouvrez pas de ticket public : suivez [SECURITY.md](SECURITY.md).

## Proposer une idée

Ouvrez un ticket avec le modèle « Idée ». Décrivez le besoin concret (« pendant les concerts, on doit… ») plutôt que la solution : c'est plus facile d'en discuter.

## Proposer du code

1. Pour une modification importante, ouvrez d'abord un ticket afin d'en discuter.
2. Installez l'environnement : [docs/developpement.md](docs/developpement.md).
3. Créez une branche, faites vos modifications, **avec des tests**.
4. Vérifiez que toute la suite passe : `.venv/bin/pytest`.
5. Mettez à jour la documentation concernée et le [journal des versions](CHANGELOG.md), dans la section « Non publié ».
6. Ouvrez une *pull request* en remplissant le modèle.

Les conventions du projet (interface en français, droits centralisés, règles métier dans `services/`, migrations compatibles SQLite…) sont décrites dans [docs/developpement.md › Conventions](docs/developpement.md#conventions).

## Licence

Le projet n'a pas encore de licence. En proposant une contribution, vous acceptez qu'elle soit intégrée au projet et diffusée sous la licence qui sera choisie.
