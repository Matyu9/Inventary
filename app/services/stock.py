"""Mouvements de stock des objets suivis en quantité."""
from ..extensions import db
from ..models import StockMovement
from .activity import log
from .notify import admins, notify

# motif → sens du mouvement
REASONS = {
    "achat": ("Achat / réassort", +1),
    "retour": ("Retour en stock", +1),
    "distribution": ("Distribution", -1),
    "perte": ("Perte / casse", -1),
    "inventaire": ("Correction d'inventaire (quantité réelle)", 0),
}


MAX_QUANTITY = 1_000_000  # même plafond que la quantité initiale du formulaire objet


class StockError(ValueError):
    pass


def move(item, reason, amount, user, comment=None):
    """Enregistre un mouvement. Pour « inventaire », `amount` est la quantité constatée."""
    if not item.is_quantity:
        raise StockError("Cet objet n'est pas suivi en quantité.")
    if reason not in REASONS:
        raise StockError("Motif inconnu.")
    if amount is None or amount < 0 or amount > MAX_QUANTITY or (reason != "inventaire" and amount == 0):
        raise StockError("Quantité invalide.")
    sign = REASONS[reason][1]
    delta = amount - item.quantity if sign == 0 else sign * amount
    if item.quantity + delta < 0:
        raise StockError(f"Stock insuffisant : il reste {item.stock_label}.")
    if item.quantity + delta > MAX_QUANTITY:
        raise StockError(f"Le stock ne peut pas dépasser {MAX_QUANTITY}.")
    if delta == 0:
        raise StockError("La quantité ne change pas.")
    was_low = item.is_low_stock
    item.quantity += delta
    db.session.add(StockMovement(item=item, delta=delta, reason=reason, user=user,
                                 comment=(comment or "").strip()[:255] or None))
    log("stock.move", f"{REASONS[reason][0]} : {delta:+d} {item.unit or ''}".rstrip(), item=item, user=user,
        changes={"quantity": [item.quantity - delta, item.quantity]})
    if item.is_low_stock and not was_low:
        notify(admins(), f"Stock bas : {item.name} ({item.stock_label} restant(s)).", f"/objets/{item.id}")
    return delta
