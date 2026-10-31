from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import or_
from wtforms import BooleanField, DateTimeLocalField, RadioField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from ..extensions import db, fold_contains
from ..forms import FrDecimalField, amount_range
from ..models import (Activity, Condition, Item, ItemStatus, Kit, Loan, LoanLine, LoanStatus, LoanType, Role, User)
from ..permissions import can, ensure, holders, require
from ..services import loan_checks as checks
from ..services import loans as svc
from ..services.mailer import deliver_quietly
from ..services.qr import qr_svg
from .items import none_if_zero

bp = Blueprint("loans", __name__)

DT_FORMAT = "%Y-%m-%dT%H:%M"
VIEWS = {
    "actifs": ("En attente et en cours", (LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS)),
    "demande": ("À valider", (LoanStatus.DEMANDE,)),
    "valide": ("Validés", (LoanStatus.VALIDE,)),
    "en_cours": ("En cours", (LoanStatus.EN_COURS,)),
    "retard": ("En retard", (LoanStatus.EN_COURS,)),
    "historique": ("Historique", (LoanStatus.RENDU, LoanStatus.REFUSE, LoanStatus.ANNULE)),
}


def user_label(user):
    extra = " · ".join(x for x in (user.section, user.role_label if user.role != Role.EMPRUNTEUR else None) if x)
    return f"{user.last_name.upper()} {user.first_name}" + (f" ({extra})" if extra else "")


class LoanForm(FlaskForm):
    type = RadioField("Type", choices=[(LoanType.GARDE, "Garde (longue durée, ex. instrument pour l'année)"),
                                       (LoanType.RESERVATION, "Réservation (période précise, ex. concert)")],
                      default=LoanType.GARDE)
    borrower_id = SelectField("Emprunteur", coerce=none_if_zero, validators=[DataRequired("Choisissez l'emprunteur.")])
    guarantor_id = SelectField("Garant", coerce=none_if_zero)
    title = StringField("Motif", validators=[Optional(), Length(max=160)])
    start = DateTimeLocalField("Début", format=DT_FORMAT, validators=[DataRequired("Date de début obligatoire.")])
    end = DateTimeLocalField("Fin", format=DT_FORMAT, validators=[Optional()])
    deposit_amount = FrDecimalField("Caution (€)", places=2, validators=[Optional(), amount_range()])
    comment = TextAreaField("Commentaire", validators=[Optional()])
    auto_validate = BooleanField("Valider directement")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        people = db.session.scalars(db.select(User).where(User.archived_at.is_(None))
                                    .order_by(User.last_name, User.first_name)).all()
        self.borrower_id.choices = [(0, "— choisir —")] + [(u.id, user_label(u)) for u in people]
        if can(current_user, "loan.validate"):
            guarantors = holders("loan.guarantee")
            self.guarantor_id.choices = [(0, "— aucun —")] + [
                (u.id, user_label(u)) for u in people if u.role in guarantors and u.active]
        elif can(current_user, "loan.guarantee"):
            # Un prof qui fait la demande en est le garant.
            self.guarantor_id.choices = [(current_user.id, user_label(current_user))]
        else:
            self.guarantor_id.choices = [(0, "— aucun —")]


def get_loan(loan_id):
    return db.session.get(Loan, loan_id) or abort(404)


def form_lines():
    item_ids = request.form.getlist("item_ids", type=int)
    quantities = {i: request.form.get(f"qty_{i}", 1, type=int) for i in item_ids}
    return svc.build_lines(item_ids, quantities, request.form.getlist("kit_ids", type=int))


def selected_lines():
    """Lignes affichées dans le formulaire (après une erreur ou pré-remplies via ?item_id / ?kit_id)."""
    source = request.form if request.method == "POST" else request.args
    item_ids = source.getlist("item_ids", type=int) + source.getlist("item_id", type=int)
    kit_ids = source.getlist("kit_ids", type=int) + source.getlist("kit_id", type=int)
    rows = []
    for item_id in dict.fromkeys(item_ids):
        if item := db.session.get(Item, item_id):
            rows.append({"item": item, "qty": source.get(f"qty_{item_id}", 1, type=int)})
    for kit_id in dict.fromkeys(kit_ids):
        if kit := db.session.get(Kit, kit_id):
            rows.append({"kit": kit})
    return rows


# ---------------------------------------------------------------------------
# Listes
# ---------------------------------------------------------------------------

@bp.route("/prets")
@require("loan.list_all")
def index():
    view = request.args.get("vue", "actifs")
    if view not in VIEWS:
        view = "actifs"
    query = db.select(Loan).where(Loan.status.in_(VIEWS[view][1]))
    if view == "retard":
        query = query.where(Loan.end.is_not(None), Loan.end < datetime.now())
    loan_type = request.args.get("type")
    if loan_type in LoanType.ALL:
        query = query.where(Loan.type == loan_type)
    if q := (request.args.get("q") or "").strip():
        people = db.select(User.id).where(or_(fold_contains(User.first_name, q), fold_contains(User.last_name, q)))
        query = query.where(or_(Loan.borrower_id.in_(people), Loan.guarantor_id.in_(people),
                                fold_contains(Loan.title, q)))
    order = Loan.start.desc() if view == "historique" else Loan.start
    loans = db.session.scalars(query.order_by(order).limit(500)).all()
    counts = {key: _count(statuses, key == "retard") for key, (_, statuses) in VIEWS.items() if key != "historique"}
    return render_template("loans/index.html", loans=loans, view=view, views=VIEWS, counts=counts,
                           loan_type=loan_type, q=q)


def _count(statuses, overdue=False):
    query = db.select(db.func.count(Loan.id)).where(Loan.status.in_(statuses))
    if overdue:
        query = query.where(Loan.end.is_not(None), Loan.end < datetime.now())
    return db.session.scalar(query)


@bp.route("/mes-prets")
@require("myloans.view")
def mine():
    as_borrower = db.session.scalars(
        db.select(Loan).where(Loan.borrower_id == current_user.id).order_by(Loan.start.desc())).all()
    as_guarantor = []
    if can(current_user, "loan.guarantee"):
        as_guarantor = db.session.scalars(
            db.select(Loan).where(Loan.guarantor_id == current_user.id,
                                  Loan.status.in_((LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS)))
            .order_by(Loan.start)).all()
    return render_template("loans/mine.html", as_borrower=as_borrower, as_guarantor=as_guarantor)


# ---------------------------------------------------------------------------
# Demande
# ---------------------------------------------------------------------------

@bp.route("/prets/nouveau", methods=["GET", "POST"])
@require("loan.request")
def create():
    form = LoanForm()
    if request.method == "GET":
        start = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=1)
        form.start.data = start.replace(minute=0)
        form.borrower_id.data = request.args.get("borrower_id", type=int)
        if can(current_user, "loan.validate"):
            form.auto_validate.data = False
    else:
        if not can(current_user, "loan.validate"):
            form.auto_validate.data = False
        if form.validate():
            try:
                loan = _create_from_form(form)
            except svc.LoanError as exc:
                db.session.rollback()
                flash(str(exc), "error")
            else:
                deliver_quietly()
                return redirect(url_for(".detail", loan_id=loan.id))
    return render_template("loans/form.html", form=form, rows=selected_lines())


def _create_from_form(form):
    borrower = db.session.get(User, form.borrower_id.data)
    guarantor = db.session.get(User, form.guarantor_id.data) if form.guarantor_id.data else None
    if guarantor and not can(guarantor, "loan.guarantee"):
        raise svc.LoanError("Cette personne ne peut pas être garante d'un prêt (voir son rôle).")
    loan, warnings = svc.create_request(
        loan_type=form.type.data, borrower=borrower, guarantor=guarantor, requested_by=current_user,
        start=form.start.data, end=form.end.data, lines=form_lines(), title=(form.title.data or "").strip(),
        comment=form.comment.data, deposit_amount=form.deposit_amount.data)
    for warning in warnings:
        flash(warning, "warning")
    if form.auto_validate.data and can(current_user, "loan.validate"):
        svc.validate(loan, current_user)
        flash("Prêt enregistré et validé.", "success")
    elif can(current_user, "loan.validate"):
        flash("Demande enregistrée : elle reste à valider.", "success")
    else:
        flash("Demande envoyée : un administrateur doit la valider.", "success")
    db.session.commit()
    return loan


@bp.route("/prets/recherche-materiel")
@require("loan.request")
def search_equipment():
    """Fragment HTMX : objets et kits correspondant à la recherche, à ajouter à la demande."""
    q = (request.args.get("q") or "").strip()
    items, kits = [], []
    if q:
        items = db.session.scalars(
            db.select(Item).where(or_(fold_contains(Item.code, q), fold_contains(Item.name, q),
                                      fold_contains(Item.serial_number, q)),
                                  Item.status != ItemStatus.REFORME)
            .order_by(Item.code).limit(15)).all()
        kits = db.session.scalars(
            db.select(Kit).where(fold_contains(Kit.name, q)).order_by(Kit.name).limit(5)).all()
    return render_template("loans/_equipment_results.html", items=items, kits=kits, q=q)


@bp.route("/prets/verifier", methods=["POST"])
@require("loan.request")
def check():
    """Fragment HTMX : disponibilité du matériel choisi sur la période saisie."""
    problems, warnings = [], []
    try:
        start = _parse_dt(request.form.get("start"))
        end = _parse_dt(request.form.get("end"))
        lines = form_lines()
        if lines and start:
            if end is not None and end <= start:
                problems = ["La date de fin doit être après la date de début."]
            else:
                problems, warnings = svc.check_availability(lines, start, end)
    except svc.LoanError as exc:
        problems = [str(exc)]
    return render_template("loans/_check.html", problems=problems, warnings=warnings,
                           checked=bool(request.form.getlist("item_ids") or request.form.getlist("kit_ids")))


def _parse_dt(value):
    try:
        return datetime.strptime(value, DT_FORMAT) if value else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Fiche et actions
# ---------------------------------------------------------------------------

@bp.route("/prets/<int:loan_id>")
@login_required
def detail(loan_id):
    loan = get_loan(loan_id)
    ensure("loan.view", loan)
    problems = []
    if loan.status in (LoanStatus.DEMANDE, LoanStatus.VALIDE) and can(current_user, "loan.list_all"):
        try:
            problems, _ = svc.check_availability(loan.lines, loan.start, loan.end, exclude_loan_id=loan.id)
        except svc.LoanError as exc:
            problems = [str(exc)]
    activities = db.session.scalars(
        db.select(Activity).where(Activity.loan_id == loan.id, Activity.item_id.is_(None))
        .order_by(Activity.created_at, Activity.id)).all()
    return render_template("loans/detail.html", loan=loan, problems=problems, activities=activities,
                           conditions=Condition.ALL, line_items=svc.line_items)


def _action(loan_id, permission, fn, success):
    loan = get_loan(loan_id)
    ensure(permission, loan)
    try:
        fn(loan)
    except svc.LoanError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash(success, "success")
        deliver_quietly()
    return redirect(url_for(".detail", loan_id=loan_id))


@bp.route("/prets/<int:loan_id>/valider", methods=["POST"])
@require("loan.validate")
def validate(loan_id):
    return _action(loan_id, "loan.validate", lambda l: svc.validate(l, current_user), "Prêt validé.")


@bp.route("/prets/<int:loan_id>/refuser", methods=["POST"])
@require("loan.validate")
def refuse(loan_id):
    reason = request.form.get("reason")
    return _action(loan_id, "loan.validate", lambda l: svc.refuse(l, current_user, reason), "Demande refusée.")


@bp.route("/prets/<int:loan_id>/annuler", methods=["POST"])
@login_required
def cancel(loan_id):
    return _action(loan_id, "loan.cancel", lambda l: svc.cancel(l, current_user), "Prêt annulé.")


@bp.route("/prets/<int:loan_id>/remise", methods=["POST"])
@login_required
def checkout(loan_id):
    return _action(loan_id, "loan.checkout", lambda l: svc.checkout(l, current_user), "Remise enregistrée.")


@bp.route("/prets/<int:loan_id>/retour", methods=["POST"])
@login_required
def checkin(loan_id):
    returns = {}
    for line_id in request.form.getlist("line_ids", type=int):
        condition = request.form.get(f"condition_{line_id}") or None
        if condition is not None and condition not in Condition.ALL:
            condition = None
        returns[line_id] = condition
    deposit = bool(request.form.get("deposit_returned"))
    return _action(loan_id, "loan.checkout", lambda l: svc.checkin(l, current_user, returns, deposit),
                   "Retour enregistré.")


@bp.route("/prets/<int:loan_id>/fin", methods=["POST"])
@require("loan.validate")
def change_end(loan_id):
    raw = request.form.get("end")
    new_end = _parse_dt(raw)

    def apply(loan):
        if raw and new_end is None:
            raise svc.LoanError("Date invalide.")
        svc.change_end(loan, current_user, new_end)

    return _action(loan_id, "loan.validate", apply, "Date de fin mise à jour.")


# ---------------------------------------------------------------------------
# Vérification du matériel (scan avant de quitter la salle) et liste imprimable
# ---------------------------------------------------------------------------

CHECK_LABELS = {checks.CHECKED: "pointé ✓", checks.ALREADY: "déjà pointé", checks.NOT_IN_LOAN: "ne fait pas partie de ce prêt"}


def _check_page(loan, template, **context):
    return render_template(template, loan=loan, progress=checks.progress(loan), labels=CHECK_LABELS, **context)


@bp.route("/prets/<int:loan_id>/verification")
@require("loan.verify")
def verify(loan_id):
    loan = get_loan(loan_id)
    if loan.status not in checks.ALLOWED_STATUSES:
        flash("La vérification n'est possible que pour un prêt validé ou en cours.", "warning")
        return redirect(url_for(".detail", loan_id=loan.id))
    return _check_page(loan, "loans/verify.html")


@bp.route("/prets/<int:loan_id>/verification/etat")
@require("loan.verify")
def verify_state(loan_id):
    """Fragment HTMX rafraîchi régulièrement : on voit ce que les autres ont scanné."""
    return _check_page(get_loan(loan_id), "loans/_verify_list.html", oob=False)


@bp.route("/prets/<int:loan_id>/verification/scan", methods=["POST"])
@require("loan.verify")
def verify_scan(loan_id):
    loan = get_loan(loan_id)
    error, results = None, []
    try:
        results = checks.scan(loan, request.form.get("code"), current_user)
        db.session.commit()
    except checks.CheckError as exc:
        db.session.rollback()
        error = str(exc)
    if request.headers.get("HX-Request"):
        return _check_page(loan, "loans/_verify_result.html", results=results, error=error)
    if error:
        flash(error, "error")
    for item, result, count, qty in results:
        flash(f"{item.code} {item.name} : {CHECK_LABELS[result]}" + (f" ({count}/{qty})" if qty > 1 else "") + ".",
              "success" if result == checks.CHECKED else "warning")
    return redirect(url_for(".verify", loan_id=loan.id))


@bp.route("/prets/<int:loan_id>/verification/<int:item_id>", methods=["POST"])
@require("loan.verify")
def verify_mark(loan_id, item_id):
    loan = get_loan(loan_id)
    try:
        checks.mark(loan, item_id, current_user, checked=request.form.get("action") != "retirer")
        db.session.commit()
    except checks.CheckError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    if request.headers.get("HX-Request"):
        return _check_page(loan, "loans/_verify_list.html", oob=False)
    return redirect(url_for(".verify", loan_id=loan.id))


@bp.route("/prets/<int:loan_id>/verification/recommencer", methods=["POST"])
@require("loan.verify")
def verify_reset(loan_id):
    loan = get_loan(loan_id)
    try:
        checks.reset(loan)
        db.session.commit()
    except checks.CheckError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash("Pointage remis à zéro.", "success")
    return redirect(url_for(".verify", loan_id=loan.id))


@bp.route("/prets/<int:loan_id>/liste")
@login_required
def checklist(loan_id):
    """Liste du matériel à imprimer, à cocher au départ et au retour."""
    loan = get_loan(loan_id)
    ensure("loan.view", loan)
    groups = {}  # objets regroupés par kit (None = hors kit), dans l'ordre des lignes
    for row in checks.expected(loan, everything=True):
        groups.setdefault(row["kit"], []).append(row)
    verify_url = current_app.config["BASE_URL"] + url_for(".verify", loan_id=loan.id)
    return render_template("loans/checklist.html", loan=loan, groups=groups,
                           count=sum(len(rows) for rows in groups.values()), qr=qr_svg(verify_url) if can(current_user, "loan.verify") else None,
                           printed_at=datetime.now())


@bp.app_template_global()
def item_loans(item):
    """Prêts (récents d'abord) contenant l'objet, directement ou via son kit."""
    conditions = [LoanLine.item_id == item.id]
    if item.kit_id:
        conditions.append(LoanLine.kit_id == item.kit_id)
    return db.session.scalars(
        db.select(Loan).join(LoanLine).where(or_(*conditions)).distinct().order_by(Loan.start.desc()).limit(20)
    ).all()
