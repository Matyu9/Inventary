"""Signalements envoyés depuis la page d'un QR code : problème constaté ou objet trouvé."""
from datetime import datetime

from sqlalchemy import or_

from ..extensions import db
from ..models import Item, Kit, Loan, LoanLine, LoanStatus, PublicReport, User
from ..permissions import holders
from .activity import log
from .notify import notify

LINK = "/signalements"


def handlers():
    return db.session.scalars(db.select(User).where(User.role.in_(holders("report.handle")),
                                                    User.active.is_(True))).all()


def current_loans(target):
    """Prêts remis ou validés qui contiennent l'objet (directement ou par son kit) ou le kit."""
    query = db.select(Loan).join(LoanLine).where(Loan.status.in_((LoanStatus.VALIDE, LoanStatus.EN_COURS)))
    if isinstance(target, Kit):
        query = query.where(LoanLine.kit_id == target.id)
    else:
        conditions = [(LoanLine.item_id == target.id) & LoanLine.returned_at.is_(None)]
        if target.kit_id:
            conditions.append(LoanLine.kit_id == target.kit_id)
        query = query.where(or_(*conditions))
    return db.session.scalars(query.distinct()).all()


def create(target, kind, message, name=None, contact=None, user=None):
    """Enregistre le signalement et prévient qui le traite (et, pour un objet trouvé, l'emprunteur et le garant)."""
    report = PublicReport(kind=kind, message=message.strip()[:2000], name=(name or "").strip()[:120] or None,
                          contact=(contact or "").strip()[:160] or None, reported_by=user,
                          **({"kit": target} if isinstance(target, Kit) else {"item": target}))
    db.session.add(report)
    db.session.flush()
    recipients = list(handlers())
    if kind == PublicReport.TROUVE:
        for loan in current_loans(target):
            recipients += [loan.borrower, loan.guarantor]
    who = report.name or (user.full_name if user else "une personne sans compte")
    text = (f"{report.kind_label} sur {report.target_label} par {who} : {report.message}")
    notify(recipients, text, LINK, kind="public.found" if kind == PublicReport.TROUVE else "public.problem",
           item=report.item, report=report)
    log("report.create", f"{report.kind_label} ({who}) : {report.message[:200]}",
        user=user, **({"kit": target} if isinstance(target, Kit) else {"item": target}))
    return report


def _visible(query):
    """Signalements dont l'objet ou le kit n'est pas à la corbeille (le filtre s'applique aux jointures)."""
    return (query.outerjoin(Item, PublicReport.item_id == Item.id).outerjoin(Kit, PublicReport.kit_id == Kit.id)
            .where(or_(Item.id.is_not(None), Kit.id.is_not(None))))


def pending_count():
    return db.session.scalar(_visible(db.select(db.func.count(PublicReport.id))
                                      .where(PublicReport.handled_at.is_(None))))


def listing(handled, limit=200):
    query = _visible(db.select(PublicReport).where(
        PublicReport.handled_at.is_not(None) if handled else PublicReport.handled_at.is_(None)))
    order = PublicReport.handled_at.desc() if handled else PublicReport.created_at.desc()
    return db.session.scalars(query.order_by(order, PublicReport.id.desc()).limit(limit)).all()


def pending_for(target):
    column = PublicReport.kit_id if isinstance(target, Kit) else PublicReport.item_id
    return db.session.scalars(db.select(PublicReport).where(column == target.id, PublicReport.handled_at.is_(None))
                              .order_by(PublicReport.created_at.desc())).all()


def handle(report, user, note=None):
    report.handled_at, report.handled_by = datetime.now(), user
    report.handled_note = (note or "").strip()[:500] or None
    log("report.handle", f"Signalement traité : {report.kind_label.lower()}"
        + (f" ({report.handled_note})" if report.handled_note else ""), user=user,
        **({"kit": report.kit} if report.kit_id else {"item": report.item}))


def reopen(report, user):
    report.handled_at = report.handled_by = report.handled_note = None
    log("report.reopen", "Signalement rouvert", user=user,
        **({"kit": report.kit} if report.kit_id else {"item": report.item}))
