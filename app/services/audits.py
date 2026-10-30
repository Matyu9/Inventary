"""Inventaire physique : périmètre attendu, scans, clôture.

Pendant l'audit, on scanne ce qui est physiquement présent. À la clôture, chaque objet attendu
non scanné est enregistré « manquant » (ou « prêté » s'il est sorti), ce qui fige le rapport.
"""
from datetime import datetime

from ..extensions import db
from ..models import AuditScan, InventoryAudit, Item, ItemStatus, Kit
from .activity import log
from .items import location_descendants
from .qr import resolve_scan

FOUND, UNEXPECTED, MISSING, LENT = "trouve", "inattendu", "manquant", "prete"
LABELS = {FOUND: "Trouvé", UNEXPECTED: "Inattendu", MISSING: "Manquant", LENT: "Prêté (absent normal)"}
OUT_STATUSES = (ItemStatus.EN_GARDE, ItemStatus.SORTI)


class AuditError(ValueError):
    pass


def scope_location_ids(audit):
    ids = set()
    for location in audit.locations:
        ids |= location_descendants(location.id)
    return ids


def expected_query(audit):
    """Objets attendus : dans le périmètre (lieux et/ou catégories), hors réformés."""
    query = db.select(Item).where(Item.status != ItemStatus.REFORME)
    if audit.locations:
        query = query.where(Item.location_id.in_(scope_location_ids(audit)))
    if audit.categories:
        query = query.where(Item.category_id.in_([c.id for c in audit.categories]))
    return query.order_by(Item.code)


def in_scope(audit, item):
    if item.status == ItemStatus.REFORME:
        return False
    if audit.locations and item.location_id not in scope_location_ids(audit):
        return False
    if audit.categories and item.category_id not in {c.id for c in audit.categories}:
        return False
    return True


def scan(audit, text, user, location=None):
    """Enregistre un scan. Renvoie [(objet, résultat, déjà_scanné)] (plusieurs objets pour un kit)."""
    if audit.status != "ouvert":
        raise AuditError("Cet inventaire est clôturé.")
    db.session.flush()  # les scans en attente doivent avoir leur item_id
    target = resolve_scan(text)
    if target is None:
        raise AuditError(f"Code inconnu : « {(text or '').strip()[:60]} ».")
    items = list(target.items) if isinstance(target, Kit) else [target]
    if not items:
        raise AuditError(f"Le kit « {target.name} » est vide.")
    done = {s.item_id: s for s in audit.scans}
    results = []
    for item in items:
        if item.id in done:
            results.append((item, done[item.id].result, True))
            continue
        result = FOUND if in_scope(audit, item) else UNEXPECTED
        entry = AuditScan(audit=audit, item=item, result=result, scanned_by=user, location=location)
        db.session.add(entry)
        done[item.id] = entry
        results.append((item, result, False))
    return results


def progress(audit):
    db.session.flush()
    expected = db.session.scalars(expected_query(audit)).all()
    scanned = {s.item_id: s for s in audit.scans}
    found = [s for s in audit.scans if s.result == FOUND]
    remaining = [i for i in expected if i.id not in scanned]
    return {
        "expected": len(expected),
        "found": len(found),
        "unexpected": [s for s in audit.scans if s.result == UNEXPECTED],
        "remaining": [i for i in remaining if i.status not in OUT_STATUSES],
        "lent": [i for i in remaining if i.status in OUT_STATUSES],
    }


def close(audit, user, apply_locations=False):
    if audit.status != "ouvert":
        raise AuditError("Cet inventaire est déjà clôturé.")
    db.session.flush()
    scanned = {s.item_id for s in audit.scans}
    for item in db.session.scalars(expected_query(audit)):
        if item.id in scanned:
            continue
        result = LENT if item.status in OUT_STATUSES else MISSING
        db.session.add(AuditScan(audit=audit, item=item, result=result))
        if result == MISSING:
            log("audit.missing", f"Introuvable lors de l'inventaire « {audit.name} »", item=item, user=user)
    moved = 0
    if apply_locations:
        for s in audit.scans:
            if s.result in (FOUND, UNEXPECTED) and s.location_id and s.item.location_id != s.location_id:
                old = s.item.location.path if s.item.location else None
                s.item.location_id = s.location_id
                log("item.move", f"Lieu corrigé par l'inventaire « {audit.name} »", item=s.item, user=user,
                    changes={"location": [old, s.location.path]})
                moved += 1
    audit.status = "cloture"
    audit.closed_at = datetime.now()
    return moved


def report(audit):
    groups = {key: [] for key in LABELS}
    for s in sorted(audit.scans, key=lambda s: s.item.code):
        groups[s.result].append(s)
    return groups
