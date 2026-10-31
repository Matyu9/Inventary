"""Traitement des signalements reçus par QR code (droit report.handle)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Item, Kit, PublicReport, get_live
from ..permissions import can, require
from ..services import public_reports as svc

bp = Blueprint("public_reports", __name__, url_prefix="/signalements")


@bp.app_template_global()
def pending_reports_count():
    """Pastille du menu : signalements à traiter (0 sans le droit)."""
    return svc.pending_count() if can(current_user, "report.handle") else 0


@bp.app_template_global()
def pending_reports_for(target):
    return svc.pending_for(target) if can(current_user, "report.handle") else []


def _get(report_id):
    report = db.session.get(PublicReport, report_id) or abort(404)
    target = get_live(Item, report.item_id) if report.item_id else get_live(Kit, report.kit_id)
    return report if target is not None else abort(404)  # objet ou kit à la corbeille


@bp.route("")
@require("report.handle")
def index():
    handled = request.args.get("vue") == "traites"
    return render_template("public_reports/index.html", reports=svc.listing(handled), handled=handled,
                           pending=svc.pending_count())


@bp.route("/<int:report_id>/traiter", methods=["POST"])
@require("report.handle")
def handle(report_id):
    report = _get(report_id)
    if report.handled_at is None:
        svc.handle(report, current_user, request.form.get("note"))
        db.session.commit()
        flash("Signalement marqué comme traité.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:report_id>/rouvrir", methods=["POST"])
@require("report.handle")
def reopen(report_id):
    report = _get(report_id)
    if report.handled_at is not None:
        svc.reopen(report, current_user)
        db.session.commit()
        flash("Signalement rouvert.", "success")
    return redirect(url_for(".index"))
