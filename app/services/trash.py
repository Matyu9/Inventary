"""Corbeille : objets et kits supprimés, restaurables pendant 30 jours puis effacés pour de bon."""
from datetime import datetime, timedelta

from ..extensions import db
from ..models import AuditScan, Item, ItemStatus, Kit, LoanLine
from .activity import log
from .documents import delete_document_files
from .items import prune_tags
from .photos import delete_photo_files

RETENTION_DAYS = 30


class TrashError(ValueError):
    pass


def _trashed(model):
    return (db.select(model).where(model.deleted_at.is_not(None)).order_by(model.deleted_at.desc())
            .execution_options(include_deleted=True))


def trashed_items():
    return db.session.scalars(_trashed(Item)).all()


def trashed_kits():
    return db.session.scalars(_trashed(Kit)).all()


def count():
    return sum(db.session.scalar(db.select(db.func.count(m.id)).where(m.deleted_at.is_not(None))
                                 .execution_options(include_deleted=True)) for m in (Item, Kit))


def get_trashed(model, obj_id):
    return db.session.scalar(db.select(model).where(model.id == obj_id, model.deleted_at.is_not(None))
                             .execution_options(include_deleted=True))


def purge_date(obj):
    return obj.deleted_at.date() + timedelta(days=RETENTION_DAYS)


def days_left(obj, now=None):
    return max((purge_date(obj) - (now or datetime.now()).date()).days, 0)


# ---------------------------------------------------------------------------
# Mise à la corbeille et restauration
# ---------------------------------------------------------------------------

def trash_item(item, user):
    if db.session.scalar(db.select(LoanLine.id).where(LoanLine.item_id == item.id).limit(1)):
        raise TrashError("Cet objet a un historique de prêts : réformez-le plutôt que de le supprimer.")
    if item.status in (ItemStatus.EN_GARDE, ItemStatus.SORTI):
        raise TrashError("Impossible de supprimer un objet actuellement prêté.")
    # Les pointages d'inventaire physique désignent un objet présent : ils ne survivent pas à la suppression.
    db.session.execute(db.delete(AuditScan).where(AuditScan.item_id == item.id))
    item.deleted_at, item.deleted_by = datetime.now(), user
    log("item.trash", f"Mis à la corbeille : {item.code} {item.name}", item=item, user=user)


def restore_item(item, user):
    item.deleted_at, item.deleted_by = None, None
    log("item.untrash", "Restauré depuis la corbeille", item=item, user=user)


def trash_kit(kit, user):
    if db.session.scalar(db.select(LoanLine.id).where(LoanLine.kit_id == kit.id).limit(1)):
        raise TrashError("Ce kit a un historique de prêts : il ne peut pas être supprimé. "
                         "Retirez-en les objets si besoin.")
    kit.trashed_item_ids = [item.id for item in kit.items]
    for item in list(kit.items):
        item.kit = None
        log("kit.remove", f"Retiré du kit « {kit.name} » (kit mis à la corbeille)", item=item, user=user)
    kit.deleted_at, kit.deleted_by = datetime.now(), user
    log("kit.trash", f"Kit mis à la corbeille : {kit.name}", kit=kit, user=user)


def restore_kit(kit, user):
    """Restaure le kit et y remet ses anciens objets, sauf ceux entrés entre-temps dans un autre kit."""
    kit.deleted_at, kit.deleted_by = None, None
    ids = kit.trashed_item_ids or []
    back = db.session.scalars(db.select(Item).where(Item.id.in_(ids), Item.kit_id.is_(None))).all() if ids else []
    for item in back:
        item.kit = kit
        log("kit.add", f"Remis dans le kit « {kit.name} » (kit restauré)", item=item, user=user)
    kit.trashed_item_ids = None
    log("kit.untrash", f"Kit restauré depuis la corbeille ({len(back)} objet{'s' if len(back) > 1 else ''} remis)",
        kit=kit, user=user)
    return len(back), len(ids) - len(back)


# ---------------------------------------------------------------------------
# Suppression définitive
# ---------------------------------------------------------------------------

def purge_item(item, user=None):
    for photo in item.photos:
        delete_photo_files(photo)
    for document in item.documents:
        delete_document_files(document)
    label = f"{item.code} {item.name}"
    db.session.delete(item)
    db.session.flush()
    prune_tags()
    log("item.purge", f"Supprimé définitivement : {label}", user=user)


def purge_kit(kit, user=None):
    for document in kit.documents:
        delete_document_files(document)
    name = kit.name
    db.session.delete(kit)
    log("kit.purge", f"Kit supprimé définitivement : {name}", user=user)


def purge(objs, user=None):
    for obj in objs:
        (purge_kit if isinstance(obj, Kit) else purge_item)(obj, user)
    return len(objs)


def purge_expired(now=None):
    """Efface ce qui est dans la corbeille depuis plus de 30 jours (lancé par les rappels automatiques)."""
    limit = (now or datetime.now()) - timedelta(days=RETENTION_DAYS)
    expired = [obj for model in (Item, Kit) for obj in db.session.scalars(
        db.select(model).where(model.deleted_at.is_not(None), model.deleted_at < limit)
        .execution_options(include_deleted=True))]
    return purge(expired)
