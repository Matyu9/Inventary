import csv
import io

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import SelectMultipleField, StringField
from wtforms.validators import DataRequired, Length

from ..extensions import db
from ..models import Category, InventoryAudit, Location
from ..permissions import require
from ..services import audits as svc
from ..services.activity import log
from .items import location_choices

bp = Blueprint("audits", __name__, url_prefix="/inventaires")


class AuditForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=160)])
    locations = SelectMultipleField("Lieux (et leurs sous-lieux) — aucun = tous", coerce=int)
    categories = SelectMultipleField("Catégories — aucune = toutes", coerce=int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.locations.choices = location_choices()
        self.categories.choices = [(c.id, c.name) for c in db.session.scalars(db.select(Category).order_by(Category.name))]


def get_audit(audit_id):
    return db.session.get(InventoryAudit, audit_id) or abort(404)


@bp.route("")
@require("audit.scan")
def index():
    audits = db.session.scalars(db.select(InventoryAudit).order_by(InventoryAudit.opened_at.desc(),
                                                                   InventoryAudit.id.desc())).all()
    return render_template("audits/index.html", audits=audits)


@bp.route("/nouveau", methods=["GET", "POST"])
@require("audit.manage")
def create():
    form = AuditForm()
    if form.validate_on_submit():
        audit = InventoryAudit(name=form.name.data.strip(), opened_by=current_user)
        audit.locations = db.session.scalars(db.select(Location).where(Location.id.in_(form.locations.data))).all()
        audit.categories = db.session.scalars(db.select(Category).where(Category.id.in_(form.categories.data))).all()
        db.session.add(audit)
        log("audit.open", f"Inventaire ouvert : {audit.name}")
        db.session.commit()
        flash("Inventaire ouvert : vous pouvez commencer à scanner.", "success")
        return redirect(url_for(".detail", audit_id=audit.id))
    return render_template("audits/form.html", form=form)


@bp.route("/<int:audit_id>")
@require("audit.scan")
def detail(audit_id):
    audit = get_audit(audit_id)
    if audit.status != "ouvert":
        return render_template("audits/report.html", audit=audit, groups=svc.report(audit), labels=svc.LABELS)
    return render_template("audits/detail.html", audit=audit, progress=svc.progress(audit),
                           locations=location_choices(), labels=svc.LABELS)


@bp.route("/<int:audit_id>/scans", methods=["POST"])
@require("audit.scan")
def add_scan(audit_id):
    audit = get_audit(audit_id)
    location = db.session.get(Location, request.form.get("location_id", type=int) or 0)
    error, results = None, []
    try:
        results = svc.scan(audit, request.form.get("code"), current_user, location)
        db.session.commit()
    except svc.AuditError as exc:
        db.session.rollback()
        error = str(exc)
    if request.headers.get("HX-Request"):
        return render_template("audits/_scan_result.html", audit=audit, results=results, error=error,
                               progress=svc.progress(audit), labels=svc.LABELS)
    if error:
        flash(error, "error")
    for item, result, already in results:
        flash(f"{item.code} {item.name} : {'déjà scanné' if already else svc.LABELS[result].lower()}.",
              "warning" if result == svc.UNEXPECTED or already else "success")
    return redirect(url_for(".detail", audit_id=audit.id))


@bp.route("/<int:audit_id>/scans/<int:scan_id>/supprimer", methods=["POST"])
@require("audit.scan")
def delete_scan(audit_id, scan_id):
    audit = get_audit(audit_id)
    entry = next((s for s in audit.scans if s.id == scan_id), None) or abort(404)
    if audit.status != "ouvert":
        abort(400)
    db.session.delete(entry)
    db.session.commit()
    return redirect(url_for(".detail", audit_id=audit.id))


@bp.route("/<int:audit_id>/cloturer", methods=["POST"])
@require("audit.manage")
def close(audit_id):
    audit = get_audit(audit_id)
    try:
        moved = svc.close(audit, current_user, apply_locations=bool(request.form.get("apply_locations")))
    except svc.AuditError as exc:
        flash(str(exc), "error")
    else:
        log("audit.close", f"Inventaire clôturé : {audit.name}")
        db.session.commit()
        flash("Inventaire clôturé." + (f" {moved} lieu(x) corrigé(s)." if moved else ""), "success")
    return redirect(url_for(".detail", audit_id=audit.id))


@bp.route("/<int:audit_id>/supprimer", methods=["POST"])
@require("audit.manage")
def delete(audit_id):
    audit = get_audit(audit_id)
    log("audit.delete", f"Inventaire supprimé : {audit.name}")
    db.session.delete(audit)
    db.session.commit()
    flash("Inventaire supprimé.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:audit_id>/export.csv")
@require("audit.scan")
def export(audit_id):
    audit = get_audit(audit_id)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Code", "Désignation", "Catégorie", "Lieu enregistré", "Lieu constaté", "Résultat",
                     "Scanné par", "Date"])
    for result, scans in svc.report(audit).items():
        for s in scans:
            writer.writerow([s.item.code, s.item.name, s.item.category.name,
                             s.item.location.path if s.item.location else "", s.location.path if s.location else "",
                             svc.LABELS[result], s.scanned_by.full_name if s.scanned_by else "",
                             s.scanned_at.strftime("%d/%m/%Y %H:%M") if s.scanned_at else ""])
    # BOM pour qu'Excel lise correctement les accents.
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="inventaire-{audit.id}.csv"'})
