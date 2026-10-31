from datetime import datetime, timedelta

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Item, ItemStatus, Loan, LoanStatus, Tracking
from ..permissions import can
from ..services.reminders import due_maintenances

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
def index():
    if not can(current_user, "inventory.view"):
        loans = db.session.scalars(
            db.select(Loan)
            .where(Loan.borrower_id == current_user.id, Loan.status.in_(
                (LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS)))
            .order_by(Loan.start)
        ).all()
        return render_template("dashboard/borrower.html", loans=loans)

    now = datetime.now()
    counts = dict(db.session.execute(
        db.select(Item.status, db.func.count()).group_by(Item.status)
    ).all())
    pending = db.session.scalars(
        db.select(Loan).where(Loan.status == LoanStatus.DEMANDE, Loan.awaiting_guarantor.is_(False))
        .order_by(Loan.start)
    ).all()
    to_confirm = db.session.scalars(
        db.select(Loan).where(Loan.status == LoanStatus.DEMANDE, Loan.awaiting_guarantor.is_(True),
                              Loan.guarantor_id == current_user.id).order_by(Loan.start)
    ).all()
    overdue = db.session.scalars(
        db.select(Loan).where(Loan.status == LoanStatus.EN_COURS, Loan.end < now).order_by(Loan.end)
    ).all()
    upcoming = db.session.scalars(
        db.select(Loan).where(
            Loan.status == LoanStatus.VALIDE, Loan.start.between(now, now + timedelta(days=7))
        ).order_by(Loan.start)
    ).all()
    maintenances = due_maintenances((now + timedelta(days=30)).date())
    low_stock = db.session.scalars(
        db.select(Item).where(
            Item.tracking == Tracking.QUANTITE,
            Item.low_stock_threshold.is_not(None),
            Item.quantity <= Item.low_stock_threshold,
        )
    ).all()
    return render_template(
        "dashboard/index.html",
        counts=counts, statuses=ItemStatus.ALL, pending=pending, to_confirm=to_confirm, overdue=overdue,
        upcoming=upcoming, maintenances=maintenances, low_stock=low_stock,
    )
