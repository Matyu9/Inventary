import click

from .extensions import db
from .models import Category, CategoryType, Location, Role, User

DEFAULT_CATEGORIES = [
    ("Bois", CategoryType.INSTRUMENT),
    ("Cuivres", CategoryType.INSTRUMENT),
    ("Percussions", CategoryType.INSTRUMENT),
    ("Cordes", CategoryType.INSTRUMENT),
    ("Sono", CategoryType.MATERIEL),
    ("Pupitres", CategoryType.MATERIEL),
    ("Housses et étuis", CategoryType.MATERIEL),
    ("Tenues", CategoryType.MATERIEL),
    ("Consommables", CategoryType.CONSOMMABLE),
]


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt="Email")
    @click.option("--first-name", prompt="Prénom")
    @click.option("--last-name", prompt="Nom")
    @click.password_option(prompt="Mot de passe")
    def create_admin(email, first_name, last_name, password):
        """Crée (ou promeut) un compte administrateur."""
        email = email.strip().lower()
        user = db.session.scalar(db.select(User).filter_by(email=email))
        if user is None:
            user = User(email=email, first_name=first_name, last_name=last_name)
            db.session.add(user)
        user.role = Role.ADMIN
        user.active = True
        user.set_password(password)
        db.session.commit()
        click.echo(f"Administrateur prêt : {user.full_name} <{email}>")

    @app.cli.command("seed")
    def seed():
        """Crée les catégories et le lieu par défaut s'ils n'existent pas."""
        created = 0
        for name, ctype in DEFAULT_CATEGORIES:
            if not db.session.scalar(db.select(Category).filter_by(name=name)):
                db.session.add(Category(name=name, type=ctype))
                created += 1
        if not db.session.scalar(db.select(Location).limit(1)):
            db.session.add(Location(name="Local de répétition"))
            created += 1
        db.session.commit()
        click.echo(f"{created} élément(s) créé(s).")

    @app.cli.command("run-reminders")
    def run_reminders():
        """Génère les rappels et notifications (lancé périodiquement par le service scheduler)."""
        from .services.reminders import run

        click.echo(run())
