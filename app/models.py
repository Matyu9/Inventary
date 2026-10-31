"""Modèle de données de l'inventaire."""
import secrets
import uuid
from datetime import date, datetime

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint, event, func
from sqlalchemy.orm import Session, declared_attr, with_loader_criteria
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager


def _token():
    return uuid.uuid4().hex


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now(),
                           onupdate=datetime.now)


class TrashableMixin:
    """Objet ou kit mis à la corbeille (`deleted_at` renseigné) : masqué de toutes les requêtes, cf. _hide_trash."""
    deleted_at = db.Column(db.DateTime, index=True)

    @declared_attr
    def deleted_by_id(cls):
        return db.Column(db.ForeignKey("user.id", ondelete="SET NULL"))

    @declared_attr
    def deleted_by(cls):
        return db.relationship("User", foreign_keys=[cls.deleted_by_id])


def get_live(model, obj_id):
    """db.session.get() hors corbeille : l'identity map peut encore contenir un élément tout juste supprimé."""
    obj = db.session.get(model, obj_id) if obj_id is not None else None
    return obj if obj is not None and obj.deleted_at is None else None


@event.listens_for(Session, "do_orm_execute")
def _hide_trash(state):
    """Ajoute « pas dans la corbeille » à chaque SELECT (relations comprises).

    Seule la corbeille lit les éléments supprimés, avec `execution_options(include_deleted=True)`. Les rechargements
    d'attributs (is_column_load) ne sont pas filtrés : un objet tout juste mis à la corbeille reste lisible.
    """
    if state.is_select and not state.is_column_load and not state.execution_options.get("include_deleted"):
        state.statement = state.statement.options(
            with_loader_criteria(TrashableMixin, lambda cls: cls.deleted_at.is_(None), include_aliases=True))


# ---------------------------------------------------------------------------
# Utilisateurs
# ---------------------------------------------------------------------------

class Role(db.Model):
    """Rôle attribuable aux personnes, avec ses droits (cochés dans l'écran « Rôles »).

    Les trois rôles de base existent toujours (non supprimables) ; l'administrateur a tous les droits
    quelle que soit sa liste, pour qu'on ne puisse pas se retirer l'accès à l'appli.
    """
    ADMIN = "admin"
    PROF = "prof"
    EMPRUNTEUR = "emprunteur"
    BUILTIN = (ADMIN, PROF, EMPRUNTEUR)
    DEFAULT_NAMES = {ADMIN: "Administrateur", PROF: "Professeur", EMPRUNTEUR: "Emprunteur"}

    key = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.String(255))
    permissions = db.Column(db.JSON, nullable=False, default=list)
    # Groupes OIDC (séparés par des virgules) dont les membres reçoivent ce rôle à la connexion.
    oidc_groups = db.Column(db.String(255))

    @property
    def builtin(self):
        return self.key in self.BUILTIN

    @property
    def oidc_group_list(self):
        return [g.strip() for g in (self.oidc_groups or "").split(",") if g.strip()]

    def __repr__(self):
        return f"<Role {self.key}>"


class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(255), unique=True)
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(20), nullable=False, default=Role.EMPRUNTEUR)
    # Un compte inactif ne peut pas se connecter (fiche emprunteur sans accès).
    active = db.Column(db.Boolean, nullable=False, default=False)
    phone = db.Column(db.String(30))
    section = db.Column(db.String(80))  # pupitre ou classe
    member_status = db.Column(db.String(20))  # musicien / élève
    ical_token = db.Column(db.String(64), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(32))
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    # Identifiant du compte chez le fournisseur OIDC (claim « sub »), fixé à la première connexion.
    oidc_sub = db.Column(db.String(255), unique=True, index=True)
    # Rôle attribué d'après les groupes OIDC (et non par un admin) : il suit les groupes.
    role_from_oidc = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    last_login_at = db.Column(db.DateTime)
    # Fiche archivée : plus d'accès, masquée des listes et des choix, historique conservé.
    archived_at = db.Column(db.DateTime)
    avatar = db.Column(db.String(255))  # photo de profil, relative à UPLOAD_DIR (avatars/….jpg)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def initials(self):
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    @property
    def is_active(self):  # utilisé par Flask-Login
        return self.active

    @property
    def is_admin(self):
        return self.role == Role.ADMIN

    @property
    def role_label(self):
        role = db.session.get(Role, self.role)
        return role.name if role else Role.DEFAULT_NAMES.get(self.role, self.role)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)

    # Jeton de la session en cours (voir LoginSession), fixé à la connexion ou au chargement.
    session_token = None

    def get_id(self):  # utilisé par Flask-Login : stocké dans le cookie de session et « rester connecté »
        return f"{self.id}:{self.session_token}"

    def __repr__(self):
        return f"<User {self.full_name} ({self.role})>"


class LoginSession(db.Model):
    """Connexion ouverte sur un appareil. La supprimer invalide ses cookies, même copiés ailleurs."""
    id = db.Column(db.String(64), primary_key=True)  # jeton secret
    user_id = db.Column(db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    last_seen_at = db.Column(db.DateTime)
    user_agent = db.Column(db.String(255))
    ip = db.Column(db.String(45))

    user = db.relationship("User")


class AccountToken(db.Model):
    """Lien à usage unique envoyé par email : invitation, mot de passe oublié, changement d'email.

    Seule l'empreinte SHA-256 du jeton est stockée : une fuite de la base ne donne aucun lien valable.
    """
    INVITE = "invite"
    RESET = "reset"
    EMAIL = "email"

    id = db.Column(db.String(64), primary_key=True)  # empreinte du jeton
    user_id = db.Column(db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    purpose = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(255))  # nouvelle adresse, pour un changement d'email
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    expires_at = db.Column(db.DateTime, nullable=False)

    user = db.relationship("User")


@login_manager.user_loader
def load_user(user_id):
    user_id, _, token = user_id.partition(":")
    login = db.session.get(LoginSession, token) if token else None
    if login is None or str(login.user_id) != user_id:
        return None
    login.user.session_token = token
    from .services.sessions import touch
    touch(login)
    return login.user


# ---------------------------------------------------------------------------
# Référentiel
# ---------------------------------------------------------------------------

class CategoryType:
    INSTRUMENT = "instrument"
    MATERIEL = "materiel"
    CONSOMMABLE = "consommable"
    ALL = (INSTRUMENT, MATERIEL, CONSOMMABLE)
    LABELS = {INSTRUMENT: "Instrument", MATERIEL: "Matériel", CONSOMMABLE: "Consommable"}


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    type = db.Column(db.String(20), nullable=False, default=CategoryType.INSTRUMENT)
    color = db.Column(db.String(7))

    items = db.relationship("Item", back_populates="category")


item_tags = db.Table(
    "item_tags",
    db.Column("item_id", db.ForeignKey("item.id", ondelete="CASCADE"), primary_key=True),
    db.Column("tag_id", db.ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)


class Location(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    parent_id = db.Column(db.ForeignKey("location.id", ondelete="SET NULL"))

    parent = db.relationship("Location", remote_side=[id], back_populates="children")
    children = db.relationship("Location", back_populates="parent", order_by="Location.name")
    items = db.relationship("Item", back_populates="location")

    @property
    def path(self):
        """Chemin complet, ex. « Local > Armoire 2 > Étagère haute »."""
        parts, node, seen = [], self, set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            parts.append(node.name)
            node = node.parent
        return " > ".join(reversed(parts))


custom_field_categories = db.Table(
    "custom_field_categories",
    db.Column("custom_field_id", db.ForeignKey("custom_field.id", ondelete="CASCADE"), primary_key=True),
    db.Column("category_id", db.ForeignKey("category.id", ondelete="CASCADE"), primary_key=True),
)


class FieldType:
    TEXTE = "texte"
    NOMBRE = "nombre"
    DATE = "date"
    BOOLEEN = "booleen"
    LISTE = "liste"
    ALL = (TEXTE, NOMBRE, DATE, BOOLEEN, LISTE)


class CustomField(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(80), nullable=False)
    type = db.Column(db.String(20), nullable=False, default=FieldType.TEXTE)
    options = db.Column(db.JSON)  # valeurs possibles pour le type « liste »
    required = db.Column(db.Boolean, nullable=False, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)

    # Vide = le champ s'applique à toutes les catégories.
    categories = db.relationship("Category", secondary=custom_field_categories)


class CustomFieldValue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"), nullable=False)
    field_id = db.Column(db.ForeignKey("custom_field.id", ondelete="CASCADE"), nullable=False)
    value = db.Column(db.Text)

    field = db.relationship("CustomField")

    __table_args__ = (UniqueConstraint("item_id", "field_id"),)


# ---------------------------------------------------------------------------
# Objets
# ---------------------------------------------------------------------------

class Condition:
    ALL = ("neuf", "bon", "usé", "à réparer", "HS")


class ItemStatus:
    DISPONIBLE = "disponible"
    EN_GARDE = "en garde"
    SORTI = "sorti"
    EN_ENTRETIEN = "en entretien"
    REFORME = "réformé"
    ALL = (DISPONIBLE, EN_GARDE, SORTI, EN_ENTRETIEN, REFORME)


class Tracking:
    INDIVIDUEL = "individuel"
    QUANTITE = "quantite"


class ConsumptionMode:
    CONSOMME = "consomme"  # distribué, ne revient pas (anches, huile…)
    RETOURNABLE = "retournable"  # prêté en quantité et rendu (pupitres, câbles…)


class Kit(TrashableMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    location_id = db.Column(db.ForeignKey("location.id", ondelete="SET NULL"))
    qr_token = db.Column(db.String(32), unique=True, nullable=False, default=_token)
    # Objets détachés à la mise à la corbeille : remis dans le kit s'il est restauré.
    trashed_item_ids = db.Column(db.JSON)

    location = db.relationship("Location")
    items = db.relationship("Item", back_populates="kit", order_by="Item.code")


class Item(TrashableMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    category_id = db.Column(db.ForeignKey("category.id"), nullable=False)
    brand = db.Column(db.String(80))
    model = db.Column(db.String(80))
    serial_number = db.Column(db.String(80))
    description = db.Column(db.Text)
    condition = db.Column(db.String(20), nullable=False, default="bon")
    status = db.Column(db.String(20), nullable=False, default=ItemStatus.DISPONIBLE)
    location_id = db.Column(db.ForeignKey("location.id", ondelete="SET NULL"))
    kit_id = db.Column(db.ForeignKey("kit.id", ondelete="SET NULL"))
    purchase_date = db.Column(db.Date)
    purchase_price = db.Column(db.Numeric(10, 2))
    estimated_value = db.Column(db.Numeric(10, 2))
    qr_token = db.Column(db.String(32), unique=True, nullable=False, default=_token)

    tracking = db.Column(db.String(20), nullable=False, default=Tracking.INDIVIDUEL)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit = db.Column(db.String(30))
    low_stock_threshold = db.Column(db.Integer)
    consumption_mode = db.Column(db.String(20))

    category = db.relationship("Category", back_populates="items")
    location = db.relationship("Location", back_populates="items")
    kit = db.relationship("Kit", back_populates="items")
    tags = db.relationship("Tag", secondary=item_tags, order_by="Tag.name")
    photos = db.relationship("Photo", back_populates="item", cascade="all, delete-orphan", order_by="Photo.id")
    notes = db.relationship("Note", back_populates="item", cascade="all, delete-orphan",
                            order_by="Note.created_at.desc()")
    maintenances = db.relationship("Maintenance", back_populates="item", cascade="all, delete-orphan",
                                   order_by="Maintenance.date.desc()")
    custom_values = db.relationship("CustomFieldValue", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(status.in_(ItemStatus.ALL), name="ck_item_status"),
        CheckConstraint("quantity >= 0", name="ck_item_quantity"),
    )

    @property
    def is_quantity(self):
        return self.tracking == Tracking.QUANTITE

    @property
    def stock_label(self):
        """« 3 boîtes »-like : quantité suivie de l'unité, sans espace superflu."""
        return f"{self.quantity} {self.unit}" if self.unit else str(self.quantity)

    @property
    def is_low_stock(self):
        return self.is_quantity and self.low_stock_threshold is not None and self.quantity <= self.low_stock_threshold

    @staticmethod
    def next_code(prefix):
        # Les codes des objets en corbeille restent réservés (ils peuvent être restaurés).
        codes = db.session.scalars(db.select(Item.code).where(Item.code.startswith(f"{prefix}-", autoescape=True))
                                   .execution_options(include_deleted=True))
        numbers = [int(c.rsplit("-", 1)[1]) for c in codes if c.rsplit("-", 1)[1].isdigit()]
        return f"{prefix}-{max(numbers, default=0) + 1:04d}"


class StockMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"), nullable=False)
    delta = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(20), nullable=False)  # achat, distribution, perte, correction, retour
    comment = db.Column(db.String(255))
    user_id = db.Column(db.ForeignKey("user.id"))
    loan_id = db.Column(db.ForeignKey("loan.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())

    item = db.relationship("Item")
    user = db.relationship("User")


class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    thumbnail = db.Column(db.String(255), nullable=False)
    caption = db.Column(db.String(255))
    uploaded_by_id = db.Column(db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())

    item = db.relationship("Item", back_populates="photos")


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"), nullable=False)
    author_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())

    item = db.relationship("Item", back_populates="notes")
    author = db.relationship("User")


class Maintenance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    kind = db.Column(db.String(30), nullable=False)  # révision, réparation, nettoyage…
    provider = db.Column(db.String(120))
    cost = db.Column(db.Numeric(10, 2))
    description = db.Column(db.Text)
    next_due = db.Column(db.Date)
    recorded_by_id = db.Column(db.ForeignKey("user.id"))

    item = db.relationship("Item", back_populates="maintenances")
    recorded_by = db.relationship("User")


reminder_recipients = db.Table(
    "reminder_recipients",
    db.Column("reminder_id", db.ForeignKey("reminder.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
)


class Reminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    message = db.Column(db.String(255), nullable=False)
    sent = db.Column(db.Boolean, nullable=False, default=False)
    created_by_id = db.Column(db.ForeignKey("user.id"))

    item = db.relationship("Item")
    recipients = db.relationship("User", secondary=reminder_recipients)


# ---------------------------------------------------------------------------
# Prêts (garde longue durée et réservations datées)
# ---------------------------------------------------------------------------

class LoanType:
    GARDE = "garde"
    RESERVATION = "reservation"
    ALL = (GARDE, RESERVATION)
    LABELS = {GARDE: "Garde", RESERVATION: "Réservation"}


class LoanStatus:
    DEMANDE = "demande"
    VALIDE = "valide"
    EN_COURS = "en_cours"
    RENDU = "rendu"
    REFUSE = "refuse"
    ANNULE = "annule"
    ALL = (DEMANDE, VALIDE, EN_COURS, RENDU, REFUSE, ANNULE)
    # Statuts qui bloquent le matériel sur leur période.
    BLOCKING = (VALIDE, EN_COURS)
    LABELS = {DEMANDE: "Demandé", VALIDE: "Validé", EN_COURS: "En cours", RENDU: "Rendu",
              REFUSE: "Refusé", ANNULE: "Annulé"}


class Loan(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=LoanStatus.DEMANDE)
    title = db.Column(db.String(160))
    borrower_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    guarantor_id = db.Column(db.ForeignKey("user.id"))
    requested_by_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    validated_by_id = db.Column(db.ForeignKey("user.id"))
    validated_at = db.Column(db.DateTime)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime)  # facultatif pour une garde
    checked_out_at = db.Column(db.DateTime)
    returned_at = db.Column(db.DateTime)
    deposit_amount = db.Column(db.Numeric(10, 2))
    deposit_returned = db.Column(db.Boolean, nullable=False, default=False)
    comment = db.Column(db.Text)
    refusal_reason = db.Column(db.String(255))

    borrower = db.relationship("User", foreign_keys=[borrower_id])
    guarantor = db.relationship("User", foreign_keys=[guarantor_id])
    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    validated_by = db.relationship("User", foreign_keys=[validated_by_id])
    lines = db.relationship("LoanLine", back_populates="loan", cascade="all, delete-orphan")
    checks = db.relationship("LoanCheck", back_populates="loan", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(type.in_(LoanType.ALL), name="ck_loan_type"),
        CheckConstraint(status.in_(LoanStatus.ALL), name="ck_loan_status"),
        CheckConstraint("type = 'garde' OR \"end\" IS NOT NULL", name="ck_reservation_end"),
        CheckConstraint("\"end\" IS NULL OR \"end\" > start", name="ck_loan_period"),
    )

    @property
    def is_overdue(self):
        return self.status == LoanStatus.EN_COURS and self.end is not None and self.end < datetime.now()

    @property
    def status_label(self):
        return "En retard" if self.is_overdue else LoanStatus.LABELS[self.status]

    @property
    def type_label(self):
        return LoanType.LABELS[self.type]


class LoanLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.ForeignKey("loan.id", ondelete="CASCADE"), nullable=False)
    item_id = db.Column(db.ForeignKey("item.id"))
    kit_id = db.Column(db.ForeignKey("kit.id"))
    quantity = db.Column(db.Integer, nullable=False, default=1)
    condition_out = db.Column(db.String(20))
    condition_in = db.Column(db.String(20))
    returned_at = db.Column(db.DateTime)

    loan = db.relationship("Loan", back_populates="lines")
    item = db.relationship("Item")
    kit = db.relationship("Kit")

    __table_args__ = (
        CheckConstraint("(item_id IS NULL) != (kit_id IS NULL)", name="ck_line_item_xor_kit"),
        CheckConstraint("quantity > 0", name="ck_line_quantity"),
    )


class LoanCheck(db.Model):
    """Objet d'un prêt pointé lors d'une vérification (ex. avant de quitter la salle de concert)."""
    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.ForeignKey("loan.id", ondelete="CASCADE"), nullable=False)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)  # objets suivis en quantité : nombre compté
    checked_by_id = db.Column(db.ForeignKey("user.id", ondelete="SET NULL"))
    checked_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())

    loan = db.relationship("Loan", back_populates="checks")
    item = db.relationship("Item")
    checked_by = db.relationship("User")

    __table_args__ = (
        UniqueConstraint("loan_id", "item_id"),
        CheckConstraint("quantity > 0", name="ck_loan_check_quantity"),
    )


# ---------------------------------------------------------------------------
# Inventaire physique (audits)
# ---------------------------------------------------------------------------

audit_locations = db.Table(
    "audit_locations",
    db.Column("audit_id", db.ForeignKey("inventory_audit.id", ondelete="CASCADE"), primary_key=True),
    db.Column("location_id", db.ForeignKey("location.id", ondelete="CASCADE"), primary_key=True),
)
audit_categories = db.Table(
    "audit_categories",
    db.Column("audit_id", db.ForeignKey("inventory_audit.id", ondelete="CASCADE"), primary_key=True),
    db.Column("category_id", db.ForeignKey("category.id", ondelete="CASCADE"), primary_key=True),
)


class InventoryAudit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="ouvert")  # ouvert / cloture
    opened_by_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    opened_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())
    closed_at = db.Column(db.DateTime)

    opened_by = db.relationship("User")
    locations = db.relationship("Location", secondary=audit_locations)
    categories = db.relationship("Category", secondary=audit_categories)
    scans = db.relationship("AuditScan", back_populates="audit", cascade="all, delete-orphan")


class AuditScan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    audit_id = db.Column(db.ForeignKey("inventory_audit.id", ondelete="CASCADE"), nullable=False)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"), nullable=False)
    result = db.Column(db.String(20), nullable=False)  # trouve / inattendu
    location_id = db.Column(db.ForeignKey("location.id", ondelete="SET NULL"))
    scanned_by_id = db.Column(db.ForeignKey("user.id"))
    scanned_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())

    audit = db.relationship("InventoryAudit", back_populates="scans")
    item = db.relationship("Item")
    location = db.relationship("Location")
    scanned_by = db.relationship("User")

    __table_args__ = (UniqueConstraint("audit_id", "item_id"),)


# ---------------------------------------------------------------------------
# Journal d'activité et notifications
# ---------------------------------------------------------------------------

class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"))
    action = db.Column(db.String(50), nullable=False)  # ex. item.create, loan.validate
    message = db.Column(db.String(500), nullable=False)
    item_id = db.Column(db.ForeignKey("item.id", ondelete="CASCADE"))
    kit_id = db.Column(db.ForeignKey("kit.id", ondelete="CASCADE"))
    loan_id = db.Column(db.ForeignKey("loan.id", ondelete="CASCADE"))
    changes = db.Column(db.JSON)  # {champ: [ancien, nouveau]}
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now(), index=True)
    # Personne concernée par l'action (compte créé, rôle changé…), à distinguer de son auteur `user_id`.
    target_user_id = db.Column(db.ForeignKey("user.id", ondelete="SET NULL"), index=True)

    user = db.relationship("User", foreign_keys=[user_id])
    target_user = db.relationship("User", foreign_keys=[target_user_id])


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    message = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(255))
    # Type d'événement (ex. "loan.validated") et objets liés : servent à composer l'email.
    kind = db.Column(db.String(30))
    loan_id = db.Column(db.ForeignKey("loan.id", ondelete="SET NULL"))
    item_id = db.Column(db.ForeignKey("item.id", ondelete="SET NULL"))
    read = db.Column(db.Boolean, nullable=False, default=False)
    # Clé de déduplication pour les rappels automatiques (ex. "overdue:12:2026-10-30").
    dedup_key = db.Column(db.String(120), unique=True)
    emailed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, server_default=func.now())

    user = db.relationship("User")
    loan = db.relationship("Loan")
    item = db.relationship("Item")


class EmailTemplate(db.Model):
    """Textes personnalisés d'un type d'email (sinon, textes par défaut de services/mailer.py)."""
    kind = db.Column(db.String(30), primary_key=True)
    subject = db.Column(db.String(200), nullable=False)
    intro = db.Column(db.Text, nullable=False)
    button = db.Column(db.String(60), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
