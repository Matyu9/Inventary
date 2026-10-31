"""Vérification du matériel d'un prêt : on scanne ce qu'on a en main pour ne rien oublier.

Typiquement en fin de concert, avant de quitter la salle, ou au départ du local. Le pointage est
partagé (plusieurs personnes peuvent scanner en même temps) et ne change ni le statut du prêt ni
celui des objets : le retour au local s'enregistre toujours depuis la fiche du prêt.
"""
from datetime import datetime

from ..extensions import db
from ..models import ConsumptionMode, Kit, LoanCheck, LoanStatus
from .loans import line_items
from .qr import resolve_scan

ALLOWED_STATUSES = (LoanStatus.VALIDE, LoanStatus.EN_COURS)
CHECKED, ALREADY, NOT_IN_LOAN = "pointe", "deja", "hors_pret"


class CheckError(ValueError):
    pass


def expected(loan, everything=False):
    """Matériel à avoir en main : [{item, qty, kit, consumed, returned}].

    Sans `everything`, on écarte les lignes déjà rendues et les consommables distribués (qui ne reviennent pas).
    """
    rows = {}
    for line in loan.lines:
        returned = line.returned_at is not None
        for item, qty in line_items(line):
            consumed = item.is_quantity and item.consumption_mode == ConsumptionMode.CONSOMME
            if (returned or consumed) and not everything:
                continue
            if item.id in rows:
                rows[item.id]["qty"] += qty
            else:
                rows[item.id] = {"item": item, "qty": qty, "kit": line.kit, "consumed": consumed,
                                 "returned": returned and not consumed}
    return list(rows.values())


def progress(loan):
    db.session.flush()
    checks = {c.item_id: c for c in loan.checks}
    rows = []
    for row in expected(loan):
        check = checks.get(row["item"].id)
        count = min(check.quantity, row["qty"]) if check else 0
        rows.append(dict(row, count=count, done=count >= row["qty"], check=check))
    done = sum(r["done"] for r in rows)
    return {"rows": rows, "done": done, "total": len(rows), "missing": [r for r in rows if not r["done"]]}


def _ensure_open(loan):
    if loan.status not in ALLOWED_STATUSES:
        raise CheckError("La vérification n'est possible que pour un prêt validé ou en cours.")


def _add(loan, item, needed, user, amount):
    check = next((c for c in loan.checks if c.item_id == item.id), None)
    if check is None:
        check = LoanCheck(item=item, quantity=0)
        loan.checks.append(check)
    check.quantity = min(needed, check.quantity + amount)
    check.checked_by = user
    check.checked_at = datetime.now()
    return check


def scan(loan, text, user):
    """Pointe l'objet (ou tous les objets d'un kit). Renvoie [(objet, résultat, compté, attendu)]."""
    _ensure_open(loan)
    target = resolve_scan(text)
    if target is None:
        raise CheckError(f"Code inconnu : « {(text or '').strip()[:60]} ».")
    items = list(target.items) if isinstance(target, Kit) else [target]
    if not items:
        raise CheckError(f"Le kit « {target.name} » est vide.")
    state = {r["item"].id: r for r in progress(loan)["rows"]}
    results = []
    for item in items:
        row = state.get(item.id)
        if row is None:
            results.append((item, NOT_IN_LOAN, 0, 0))
        elif row["done"]:
            results.append((item, ALREADY, row["count"], row["qty"]))
        else:
            # Un QR identifie un objet suivi en quantité, pas chaque exemplaire : un scan = un exemplaire.
            check = _add(loan, item, row["qty"], user, 1)
            results.append((item, CHECKED, check.quantity, row["qty"]))
    return results


def mark(loan, item_id, user, checked=True):
    """Pointage manuel (QR abîmé, objets comptés à la main) : tout l'objet, ou on l'enlève."""
    _ensure_open(loan)
    db.session.flush()  # les pointages en attente doivent avoir leur item_id
    row = next((r for r in expected(loan) if r["item"].id == item_id), None)
    if row is None:
        raise CheckError("Cet objet ne fait pas partie du prêt.")
    if checked:
        _add(loan, row["item"], row["qty"], user, row["qty"])
    else:
        for check in [c for c in loan.checks if c.item_id == item_id]:
            loan.checks.remove(check)


def reset(loan):
    _ensure_open(loan)
    loan.checks.clear()
