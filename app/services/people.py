"""Cycle de vie des fiches : archivage, suppression d'une fiche inutilisée, fusion de doublons."""
from datetime import datetime

from ..extensions import db
from ..models import Loan, LoanStatus, User
from . import accounts, photos, sessions

# Prêts qui empêchent d'archiver : la personne en est emprunteuse ou garante.
OPEN_LOANS = (LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS)
# Tables qui disparaissent avec la personne et ne se transfèrent pas lors d'une fusion.
PERSONAL = {"login_session", "account_token"}


class PeopleError(Exception):
    """Action refusée : le message s'affiche tel quel."""


def user_columns():
    """[(table, colonne, ondelete)] : toutes les clés étrangères vers user.id, trouvées dans le modèle."""
    found = []
    for table in db.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name == "user" and fk.column.name == "id":
                found.append((table, fk.parent, (fk.ondelete or "").upper()))
    return found


def references(user):
    """{table: nombre de lignes} qui empêchent de supprimer la fiche (prêts, notes, journal…)."""
    counts = {}
    for table, column, ondelete in user_columns():
        if ondelete in ("CASCADE", "SET NULL"):
            continue
        n = db.session.scalar(db.select(db.func.count()).select_from(table).where(column == user.id))
        if n:
            counts[table.name] = counts.get(table.name, 0) + n
    return counts


def open_loans(user):
    return db.session.scalar(db.select(db.func.count(Loan.id)).where(
        Loan.status.in_(OPEN_LOANS), (Loan.borrower_id == user.id) | (Loan.guarantor_id == user.id)))


def archive(user):
    """Archive la fiche : plus d'accès, masquée des listes, historique conservé. Sans commit."""
    if n := open_loans(user):
        raise PeopleError(f"{user.full_name} a {n} prêt(s) en cours ou à venir (emprunteur ou garant) : "
                          "clôturez-les ou changez de garant avant d'archiver.")
    user.archived_at = datetime.now()
    user.active = False
    sessions.revoke(user)
    accounts.revoke(user)


def unarchive(user):
    """Ressort la fiche des archives, sans accès : il faudra le rouvrir. Sans commit."""
    user.archived_at = None


def set_avatar(user, file_storage):
    """Remplace la photo de profil (lève photos.InvalidImage si le fichier n'est pas une image). Sans commit."""
    new = photos.save_avatar(file_storage)
    photos.delete_avatar_file(user.avatar)
    user.avatar = new


def remove_avatar(user):
    photos.delete_avatar_file(user.avatar)
    user.avatar = None


def delete(user):
    """Supprime une fiche que rien ne référence (sinon : archiver). Sans commit."""
    if references(user):
        raise PeopleError("Cette fiche a un historique (prêts, notes, journal…) : archivez-la plutôt.")
    remove_avatar(user)
    db.session.delete(user)


def merge(source, target):
    """Fusionne le doublon `source` dans `target` : tout l'historique passe à `target`, puis `source` disparaît.

    `target` garde son rôle et son accès ; il récupère les coordonnées qui lui manquent. Sans commit.
    """
    if source.id == target.id:
        raise PeopleError("Choisissez deux fiches différentes.")
    if source.oidc_sub and target.oidc_sub:
        raise PeopleError("Les deux fiches sont liées à une identité OIDC différente : fusion impossible.")
    moved = {}
    for table, column, _ondelete in user_columns():
        if table.name in PERSONAL:
            db.session.execute(table.delete().where(column == source.id))
            continue
        if table.name == "reminder_recipients":  # clé primaire (rappel, personne) : pas de doublon
            already = db.select(table.c.reminder_id).where(column == target.id)
            db.session.execute(table.delete().where(column == source.id, table.c.reminder_id.in_(already)))
        n = db.session.execute(table.update().where(column == source.id).values({column.name: target.id})).rowcount
        if n:
            moved[table.name] = moved.get(table.name, 0) + n
    # Coordonnées manquantes (l'email et l'identité OIDC sont uniques : on les libère d'abord).
    taken = {"email": source.email, "oidc_sub": source.oidc_sub}
    source.email = source.oidc_sub = None
    db.session.flush()
    for field in ("email", "oidc_sub"):
        if not getattr(target, field) and taken[field]:
            setattr(target, field, taken[field])
    for field in ("phone", "section", "member_status"):
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))
    if target.avatar is None:
        target.avatar, source.avatar = source.avatar, None
    else:
        remove_avatar(source)
    db.session.flush()
    db.session.expire_all()  # les relations chargées (prêts, notes…) pointent encore vers `source`
    db.session.delete(db.session.get(User, source.id))
    return moved
