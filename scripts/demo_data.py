"""Données de démonstration fictives : comptes, inventaire, kits, consommables, prêts, entretiens, inventaire physique.

Usage, sur une base vide (après `flask db upgrade`) :

    DATA_DIR=./demo .venv/bin/python scripts/demo_data.py

Tous les comptes de démo ont le mot de passe « demo-harmonie ». À ne jamais lancer sur la base réelle :
le script refuse si la base contient déjà des objets.
"""
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from flask import current_app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extensions import db  # noqa: E402
from app.models import (Category, CategoryType, ConsumptionMode, InventoryAudit, Item, Kit,  # noqa: E402
                        Location, LoanLine, LoanType, Maintenance, Note, Role, Tag, Tracking, User)
from app.services import audits, loans  # noqa: E402

PASSWORD = "demo-harmonie"


def _user(first, last, role, *, active=True, section=None, status=None):
    user = User(first_name=first, last_name=last, role=role, active=active, section=section, member_status=status,
                email=f"{first.lower()}.{last.lower()}@exemple.org".replace("è", "e").replace("é", "e"))
    if active:
        user.set_password(PASSWORD)
    db.session.add(user)
    return user


def populate():
    """Remplit une base vide. À appeler dans un contexte de requête (les notifications construisent des liens)."""
    if db.session.scalar(db.select(Item.id).limit(1)):
        raise SystemExit("La base contient déjà des objets : données de démo non ajoutées.")
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    today = date.today()

    # --- Comptes --------------------------------------------------------------
    claire = _user("Claire", "Dubois", Role.ADMIN)
    marc = _user("Marc", "Lefèvre", Role.PROF, section="Clarinettes")
    sophie = _user("Sophie", "Bernard", Role.PROF, section="Percussions")
    lucas = _user("Lucas", "Martin", Role.EMPRUNTEUR, section="Clarinettes", status="élève")
    ines = _user("Inès", "Moreau", Role.EMPRUNTEUR, section="Flûtes", status="musicien")
    hugo = _user("Hugo", "Petit", Role.EMPRUNTEUR, active=False, section="Saxophones", status="élève")
    lea = _user("Léa", "Garnier", Role.EMPRUNTEUR, active=False, section="Trompettes", status="élève")

    # --- Référentiel ----------------------------------------------------------
    cats = {name: Category(name=name, type=ctype, color=color) for name, ctype, color in (
        ("Bois", CategoryType.INSTRUMENT, "#2f7d4f"), ("Cuivres", CategoryType.INSTRUMENT, "#b7791f"),
        ("Percussions", CategoryType.INSTRUMENT, "#9b2c2c"), ("Sono", CategoryType.MATERIEL, "#2b6cb0"),
        ("Pupitres", CategoryType.MATERIEL, "#4a5568"), ("Consommables", CategoryType.CONSOMMABLE, "#805ad5"))}
    local = Location(name="Local de répétition")
    bois_arm = Location(name="Armoire bois", parent=local)
    cuivres_arm = Location(name="Armoire cuivres", parent=local)
    reserve = Location(name="Réserve percussions", parent=local)
    ecole = Location(name="École de musique")
    salle2 = Location(name="Salle 2", parent=ecole)
    etude, concert = Tag(name="étude"), Tag(name="concert")
    db.session.add_all([*cats.values(), local, ecole, etude, concert])

    def item(name, cat, location, **kw):
        tags = kw.pop("tags", [])
        obj = Item(code=Item.next_code(current_app.config["ITEM_CODE_PREFIX"]), name=name, category=cats[cat], location=location, **kw)
        obj.tags = tags
        db.session.add(obj)
        db.session.flush()
        return obj

    clarinette = item("Clarinette si♭", "Bois", bois_arm, brand="Buffet Crampon", model="E11", serial_number="B114522",
                      condition="bon", purchase_date=date(2019, 9, 2), purchase_price=Decimal("1150"),
                      estimated_value=Decimal("850"), tags=[etude])
    item("Clarinette si♭", "Bois", bois_arm, brand="Yamaha", model="YCL-255", serial_number="Y30871",
         condition="usé", estimated_value=Decimal("400"), tags=[etude])
    flute = item("Flûte traversière", "Bois", bois_arm, brand="Pearl", model="PF-505", serial_number="P77810",
                 condition="bon", estimated_value=Decimal("600"), tags=[etude])
    sax = item("Saxophone alto", "Bois", bois_arm, brand="Selmer", model="Axos", serial_number="SA90233",
               condition="neuf", purchase_date=date(2024, 3, 15), purchase_price=Decimal("2390"),
               estimated_value=Decimal("2200"))
    item("Hautbois", "Bois", bois_arm, brand="Rigoutat", condition="à réparer", estimated_value=Decimal("1800"))
    trompette = item("Trompette si♭", "Cuivres", cuivres_arm, brand="Bach", model="TR300", serial_number="T55102",
                     condition="bon", estimated_value=Decimal("550"), tags=[etude])
    item("Trombone ténor", "Cuivres", cuivres_arm, brand="Yamaha", model="YSL-354", condition="bon",
         estimated_value=Decimal("700"))
    tuba = item("Tuba si♭", "Cuivres", cuivres_arm, brand="Jupiter", condition="usé", estimated_value=Decimal("2600"))
    item("Euphonium", "Cuivres", cuivres_arm, brand="Besson", condition="bon", estimated_value=Decimal("1900"))
    timbales = item("Timbales (paire)", "Percussions", reserve, brand="Adams", condition="bon",
                    estimated_value=Decimal("6500"), tags=[concert])
    item("Xylophone", "Percussions", reserve, brand="Bergerault", condition="bon", estimated_value=Decimal("3200"),
         tags=[concert])
    item("Caisse claire", "Percussions", salle2, brand="Pearl", condition="bon", estimated_value=Decimal("350"),
         tags=[etude])

    sono = Kit(name="Sono concert", description="Deux enceintes, table de mixage, micros et câbles.", location=local)
    db.session.add(sono)
    for name, value in (("Enceinte amplifiée gauche", 450), ("Enceinte amplifiée droite", 450),
                        ("Table de mixage 8 voies", 320), ("Micro chant", 110)):
        item(name, "Sono", local, brand="Yamaha" if "Table" in name else "RCF", condition="bon",
             estimated_value=Decimal(value), kit=sono, tags=[concert])

    pupitres = item("Pupitre pliant", "Pupitres", local, tracking=Tracking.QUANTITE, quantity=40, unit="pupitres",
                    consumption_mode=ConsumptionMode.RETOURNABLE, estimated_value=Decimal("15"), tags=[concert])
    item("Anches clarinette force 2,5", "Consommables", bois_arm, tracking=Tracking.QUANTITE, quantity=4,
         unit="boîtes", low_stock_threshold=5, consumption_mode=ConsumptionMode.CONSOMME)
    anches_sax = item("Anches saxophone alto force 3", "Consommables", bois_arm, tracking=Tracking.QUANTITE,
                      quantity=12, unit="boîtes", low_stock_threshold=4, consumption_mode=ConsumptionMode.CONSOMME)
    item("Huile pistons", "Consommables", cuivres_arm, tracking=Tracking.QUANTITE, quantity=9, unit="flacons",
         low_stock_threshold=3, consumption_mode=ConsumptionMode.CONSOMME)

    # --- Notes et entretiens --------------------------------------------------
    db.session.add_all([
        Note(item=clarinette, author=marc, text="Tampons du bas changés en septembre, bon pour un élève débutant."),
        Note(item=tuba, author=claire, text="Bosse sur le pavillon, sans effet sur le son."),
        Maintenance(item=clarinette, date=today - timedelta(days=50), kind="révision", provider="Atelier des Vents",
                    cost=Decimal("85"), description="Révision complète, tampons.", next_due=today + timedelta(days=4),
                    recorded_by=marc),
        Maintenance(item=tuba, date=today - timedelta(days=200), kind="nettoyage", provider="Atelier des Vents",
                    cost=Decimal("60"), next_due=today - timedelta(days=3), recorded_by=claire),
        Maintenance(item=sax, date=today - timedelta(days=20), kind="réglage", cost=Decimal("40"), recorded_by=marc),
    ])
    db.session.flush()

    # --- Prêts ------------------------------------------------------------------
    def loan(loan_type, borrower, guarantor, requested_by, start, end, lines, **kw):
        created, _warnings = loans.create_request(loan_type=loan_type, borrower=borrower, guarantor=guarantor,
                                                  requested_by=requested_by, start=start, end=end, lines=lines, **kw)
        db.session.flush()
        return created

    # Garde de l'année, remise.
    garde = loan(LoanType.GARDE, lucas, marc, marc, now - timedelta(days=40), None, [LoanLine(item=clarinette, quantity=1)],
                 title="Garde 2026-2027", deposit_amount=Decimal("150"))
    loans.validate(garde, claire)
    loans.checkout(garde, marc)
    # Réservation en retard.
    retard = loan(LoanType.RESERVATION, ines, marc, marc, now - timedelta(days=12), now - timedelta(days=2),
                  [LoanLine(item=flute, quantity=1)], title="Stage de la Toussaint")
    loans.validate(retard, claire)
    loans.checkout(retard, claire)
    # Réservation validée pour le concert, pas encore remise.
    concert_loan = loan(LoanType.RESERVATION, sophie, sophie, sophie, now + timedelta(days=3),
                        now + timedelta(days=4), [LoanLine(kit=sono, quantity=1), LoanLine(item=pupitres, quantity=25),
                                                  LoanLine(item=timbales, quantity=1)], title="Concert de printemps")
    loans.validate(concert_loan, claire)
    # Demandes en attente de validation.
    loan(LoanType.GARDE, hugo, marc, marc, now + timedelta(days=1), None, [LoanLine(item=sax, quantity=1)],
         title="Garde saxophone", comment="Hugo commence le saxophone à la rentrée.")
    loan(LoanType.RESERVATION, lea, sophie, sophie, now + timedelta(days=6), now + timedelta(days=8),
         [LoanLine(item=trompette, quantity=1)], title="Examen de fin de cycle")
    # Distribution de consommables (rendue d'office).
    distribution = loan(LoanType.RESERVATION, lucas, marc, marc, now - timedelta(days=5), now - timedelta(days=4),
                        [LoanLine(item=anches_sax, quantity=2)], title="Anches pour la classe")
    loans.validate(distribution, claire)
    loans.checkout(distribution, marc)

    # --- Inventaire physique en cours ---------------------------------------
    audit = InventoryAudit(name="Inventaire de rentrée — armoire bois", opened_by=claire)
    audit.locations = [bois_arm]
    db.session.add(audit)
    db.session.flush()
    prefix = current_app.config["ITEM_CODE_PREFIX"]
    for code in (f"{prefix}-0002", f"{prefix}-0004", f"{prefix}-0005"):
        audits.scan(audit, code, claire, bois_arm)

    db.session.commit()
    return {"comptes": 7, "objets": db.session.scalar(db.select(db.func.count(Item.id)))}


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    from app import create_app

    app = create_app()
    with app.test_request_context():
        print(populate())
        print(f"Comptes : claire.dubois@exemple.org (admin), marc.lefevre@exemple.org (prof), "
              f"lucas.martin@exemple.org (emprunteur) — mot de passe « {PASSWORD} ».")
