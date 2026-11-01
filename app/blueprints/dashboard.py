from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Item, ItemStatus, Loan, LoanStatus, Tracking
from ..permissions import can, require
from ..services import dashboard
from ..services.reminders import due_maintenances

bp = Blueprint("dashboard", __name__)


# Données de chaque bloc : seuls les blocs affichés sont calculés.
def _stats(now):
    return dict(db.session.execute(db.select(Item.status, db.func.count()).group_by(Item.status)).all())


def _to_confirm(now):
    return db.session.scalars(
        db.select(Loan).where(Loan.status == LoanStatus.DEMANDE, Loan.awaiting_guarantor.is_(True),
                              Loan.guarantor_id == current_user.id).order_by(Loan.start)
    ).all()


def _pending(now):
    return db.session.scalars(
        db.select(Loan).where(Loan.status == LoanStatus.DEMANDE, Loan.awaiting_guarantor.is_(False))
        .order_by(Loan.start)
    ).all()


def _overdue(now):
    return db.session.scalars(
        db.select(Loan).where(Loan.status == LoanStatus.EN_COURS, Loan.end < now).order_by(Loan.end)
    ).all()


def _upcoming(now):
    return db.session.scalars(
        db.select(Loan).where(
            Loan.status == LoanStatus.VALIDE, Loan.start.between(now, now + timedelta(days=7))
        ).order_by(Loan.start)
    ).all()


def _maintenances(now):
    return due_maintenances((now + timedelta(days=30)).date())


def _low_stock(now):
    return db.session.scalars(
        db.select(Item).where(
            Item.tracking == Tracking.QUANTITE,
            Item.low_stock_threshold.is_not(None),
            Item.quantity <= Item.low_stock_threshold,
        )
    ).all()


LOADERS = {"stats": _stats, "to_confirm": _to_confirm, "pending": _pending, "overdue": _overdue,
           "upcoming": _upcoming, "maintenances": _maintenances, "low_stock": _low_stock}


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
    widgets = dashboard.visible(current_user)
    data = {key: LOADERS[key](now) for key in widgets}
    widgets = [key for key in widgets if key != "to_confirm" or data[key]]  # affiché seulement s'il y en a
    return render_template("dashboard/index.html", widgets=widgets, data=data, statuses=ItemStatus.ALL,
                           wide=dashboard.full_width(widgets))


@bp.route("/tableau-de-bord", methods=["GET", "POST"])
@require("inventory.view")
def customize():
    if request.method == "POST":
        order = request.form.getlist("order")
        shown = request.form.getlist("shown")
        key, _, direction = request.form.get("move", "").partition(":")
        if direction in ("up", "down"):
            order = dashboard.move(order, key, -1 if direction == "up" else 1)
        dashboard.save(current_user, order, shown)
        db.session.commit()
        if direction in ("up", "down"):
            return redirect(url_for(".customize") + f"#w-{key}")
        flash("Tableau de bord enregistré.", "success")
        return redirect(url_for(".index"))
    return render_template("dashboard/customize.html", layout=dashboard.layout(current_user),
                           widgets=dashboard.WIDGETS)


@bp.route("/tableau-de-bord/reinitialiser", methods=["POST"])
@require("inventory.view")
def reset():
    dashboard.reset(current_user)
    db.session.commit()
    flash("Tableau de bord remis par défaut.", "success")
    return redirect(url_for(".index"))
