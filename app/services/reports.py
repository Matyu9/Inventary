"""Calculs des rapports (assurance, AG, suivi des prêts)."""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_

from ..extensions import db
from ..models import (Category, Item, ItemStatus, Loan, LoanLine, LoanStatus, LoanType, Maintenance, Tracking)


def item_value(item):
    value = item.estimated_value if item.estimated_value is not None else item.purchase_price
    return Decimal(value or 0)


def value_by_category():
    """[{category, count, value, unvalued}] trié par valeur décroissante, hors objets réformés."""
    rows = {}
    for item in db.session.scalars(db.select(Item).where(Item.status != ItemStatus.REFORME)):
        row = rows.setdefault(item.category_id, {"category": item.category, "count": 0, "value": Decimal(0),
                                                 "unvalued": 0})
        row["count"] += item.quantity if item.is_quantity else 1
        row["value"] += item_value(item)
        if item.estimated_value is None and item.purchase_price is None:
            row["unvalued"] += 1
    return sorted(rows.values(), key=lambda r: (-r["value"], r["category"].name))


def _out_after(moment):
    """Le matériel était encore sorti après `moment` : prêt en cours (même en retard), ou rendu après."""
    returned = db.func.coalesce(Loan.returned_at, Loan.end)
    return or_(Loan.status == LoanStatus.EN_COURS, returned.is_(None), returned > moment)


def _overlapping_loans(start, end):
    return db.session.scalars(db.select(Loan).where(
        Loan.status.in_((LoanStatus.EN_COURS, LoanStatus.RENDU)), Loan.start < end, _out_after(start))).all()


def usage(start, end, limit=20):
    """Objets les plus prêtés sur la période (prêts effectivement sortis)."""
    counts = defaultdict(int)
    items = {}
    for loan in _overlapping_loans(start, end):
        seen = set()
        for line in loan.lines:
            covered = line.kit.items if line.kit else [line.item]
            for item in covered:
                if item.id not in seen:
                    seen.add(item.id)
                    counts[item.id] += 1
                    items[item.id] = item
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], items[kv[0]].code))
    return [{"item": items[i], "loans": n} for i, n in ranked[:limit]]


def idle_items(since):
    """Objets individuels (hors réformés) jamais sortis depuis `since`."""
    used = set()
    for loan in db.session.scalars(db.select(Loan).where(
            Loan.status.in_((LoanStatus.EN_COURS, LoanStatus.RENDU)), _out_after(since))):
        for line in loan.lines:
            for item in (line.kit.items if line.kit else [line.item]):
                used.add(item.id)
    candidates = db.session.scalars(db.select(Item).where(
        Item.status != ItemStatus.REFORME, Item.tracking == Tracking.INDIVIDUEL,
        Item.created_at < since).order_by(Item.code)).all()
    return [i for i in candidates if i.id not in used]


def returns_compliance(start, end, grace=timedelta(hours=24)):
    """Prêts datés rendus sur la période : à l'heure (avec une marge) ou en retard."""
    loans = db.session.scalars(db.select(Loan).where(
        Loan.status == LoanStatus.RENDU, Loan.end.is_not(None), Loan.end >= start, Loan.end < end)).all()
    late = [l for l in loans if l.returned_at and l.returned_at > l.end + grace]
    return {"total": len(loans), "late": late, "on_time": len(loans) - len(late),
            "rate": round(100 * (len(loans) - len(late)) / len(loans)) if loans else None}


def custody():
    """Gardes en cours, regroupées par emprunteur."""
    loans = db.session.scalars(db.select(Loan).where(
        Loan.type == LoanType.GARDE, Loan.status == LoanStatus.EN_COURS).order_by(Loan.start)).all()
    groups = defaultdict(list)
    for loan in loans:
        groups[loan.borrower].append(loan)
    return sorted(groups.items(), key=lambda kv: (kv[0].last_name.lower(), kv[0].first_name.lower()))


def maintenance_costs(start, end):
    rows = defaultdict(lambda: {"count": 0, "cost": Decimal(0)})
    for m in db.session.scalars(db.select(Maintenance).join(Maintenance.item).where(
            Maintenance.date >= start.date(), Maintenance.date < end.date())):
        row = rows[m.item.category.name]
        row["count"] += 1
        row["cost"] += Decimal(m.cost or 0)
    return sorted(({"category": k, **v} for k, v in rows.items()), key=lambda r: (-r["cost"], r["category"]))


def kpis(start, end):
    values = value_by_category()
    return {
        "items": db.session.scalar(db.select(db.func.count(Item.id)).where(Item.status != ItemStatus.REFORME)),
        "value": sum((r["value"] for r in values), Decimal(0)),
        "active_loans": db.session.scalar(db.select(db.func.count(Loan.id)).where(Loan.status == LoanStatus.EN_COURS)),
        "loans_period": len(_overlapping_loans(start, end)),
    }


def bar_width(value, maximum):
    """Largeur en % d'une barre ; au moins 1 % pour qu'une valeur non nulle reste visible."""
    if not maximum or not value:
        return 0
    return max(1, round(100 * float(value) / float(maximum)))
