"""Calendrier des prêts (FullCalendar) et flux iCal par utilisateur."""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from icalendar import Calendar, Event
from sqlalchemy import or_

from ..extensions import db
from ..models import Loan, LoanStatus, LoanType, Maintenance, User
from ..permissions import can, require

bp = Blueprint("calendar", __name__)

SHOWN = (LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS, LoanStatus.RENDU)
COLORS = {LoanStatus.DEMANDE: "#d08a0b", LoanStatus.VALIDE: "#2f6fd0", LoanStatus.EN_COURS: "#2e8b4e",
          LoanStatus.RENDU: "#8c8c8c", "retard": "#c0392b"}
# Une garde sans fin s'affiche jusqu'à cette distance de la fenêtre demandée.
OPEN_END_DISPLAY = timedelta(days=1)


def _parse(value, default):
    try:
        return datetime.fromisoformat(value[:19]) if value else default
    except ValueError:
        return default


def loans_between(user, start, end, statuses=SHOWN):
    # Un prêt en cours reste affiché tant que le matériel n'est pas rendu, même après sa date de fin.
    query = db.select(Loan).where(Loan.status.in_(statuses), Loan.start < end,
                                  or_(Loan.end.is_(None), Loan.end > start, Loan.status == LoanStatus.EN_COURS))
    if not can(user, "loan.list_all"):
        query = query.where(Loan.borrower_id == user.id)
    return db.session.scalars(query.order_by(Loan.start)).all()


def loan_title(loan):
    things = ", ".join(l.kit.name if l.kit else l.item.name for l in loan.lines[:3])
    if len(loan.lines) > 3:
        things += f" +{len(loan.lines) - 3}"
    prefix = "Garde" if loan.type == LoanType.GARDE else (loan.title or "Réservation")
    return f"{prefix} — {loan.borrower.full_name} : {things}"


@bp.route("/calendrier")
@require("calendar.view")
def index():
    feed = url_for(".ical", token=current_user.ical_token, _external=False)
    return render_template("calendar/index.html", feed_url=current_app.config["BASE_URL"] + feed)


@bp.route("/calendrier/evenements")
@require("calendar.view")
def events():
    start = _parse(request.args.get("start"), datetime.now() - timedelta(days=31))
    end = _parse(request.args.get("end"), datetime.now() + timedelta(days=62))
    if end <= start or end - start > timedelta(days=400):
        abort(400)
    data, now = [], datetime.now()
    for loan in loans_between(current_user, start, end):
        status = "retard" if loan.is_overdue else loan.status
        data.append({
            "id": f"loan-{loan.id}",
            "title": loan_title(loan),
            "start": loan.start.isoformat(),
            "end": (max(loan.end, now) if loan.is_overdue else
                    loan.end or max(end, loan.start) + OPEN_END_DISPLAY).isoformat(),
            "url": url_for("loans.detail", loan_id=loan.id),
            "color": COLORS[status],
            "extendedProps": {"status": loan.status_label, "openEnded": loan.end is None},
        })
    if can(current_user, "inventory.view"):
        for m in db.session.scalars(db.select(Maintenance).join(Maintenance.item).where(
                Maintenance.next_due >= start.date(), Maintenance.next_due < end.date())):
            data.append({"id": f"maint-{m.id}", "title": f"Entretien : {m.item.code} {m.item.name}",
                         "start": m.next_due.isoformat(), "allDay": True, "color": "#7b5ea7",
                         "url": url_for("items.detail", item_id=m.item_id)})
    return jsonify(data)


@bp.route("/cal/<token>.ics")
def ical(token):
    """Flux iCal (abonnement Google / Outlook / Apple). Authentifié par le jeton secret de l'utilisateur."""
    user = db.session.scalar(db.select(User).filter_by(ical_token=token))
    if user is None or not user.active:
        abort(404)
    now = datetime.now()
    cal = Calendar()
    cal.add("prodid", f"-//{current_app.config['ASSO_NAME']}//Inventaire//FR")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"Prêts — {current_app.config['ASSO_NAME']}")
    base = current_app.config["BASE_URL"]
    for loan in loans_between(user, now - timedelta(days=90), now + timedelta(days=365),
                              (LoanStatus.VALIDE, LoanStatus.EN_COURS)):
        event = Event()
        event.add("uid", f"loan-{loan.id}@inventaire")
        event.add("summary", loan_title(loan))
        if loan.end:
            event.add("dtstart", loan.start)
            event.add("dtend", loan.end)
        else:
            # Garde sans fin : un événement d'une journée au début.
            event.add("dtstart", loan.start.date())
            event.add("dtend", loan.start.date() + timedelta(days=1))
        event.add("dtstamp", now)
        event.add("url", f"{base}/prets/{loan.id}")
        event.add("description", f"{loan.status_label} — {base}/prets/{loan.id}")
        cal.add_component(event)
    response = current_app.response_class(cal.to_ical(), mimetype="text/calendar")
    response.headers["Content-Disposition"] = 'inline; filename="prets.ics"'
    return response


@bp.route("/calendrier/nouveau-lien", methods=["POST"])
@require("calendar.view")
def reset_token():
    current_user.ical_token = secrets.token_urlsafe(32)
    db.session.commit()
    flash("Nouveau lien d'abonnement généré : l'ancien ne fonctionne plus.", "success")
    return redirect(url_for(".index"))
