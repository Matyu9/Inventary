"""Règles métier des prêts : disponibilité, conflits, transitions de statut.

Cycle de vie : demandé → validé → en cours → rendu, ou refusé / annulé.
Seuls les prêts validés ou en cours bloquent le matériel ; une demande ne réserve rien.
"""
from collections import defaultdict
from datetime import datetime

from flask import url_for

from ..extensions import db
from ..models import (ConsumptionMode, Item, ItemStatus, Kit, Loan, LoanLine, LoanStatus, LoanType, StockMovement)
from .activity import log
from .notify import admins, notify

UNAVAILABLE_STATUSES = (ItemStatus.REFORME, ItemStatus.EN_ENTRETIEN)


class LoanError(ValueError):
    """Erreur métier, message destiné à l'utilisateur."""


# ---------------------------------------------------------------------------
# Disponibilité
# ---------------------------------------------------------------------------

def line_items(line):
    """[(objet, quantité)] couverts par une ligne de prêt (un kit couvre tous ses objets)."""
    if line.kit is not None or line.kit_id:
        kit = line.kit or db.session.get(Kit, line.kit_id)
        return [(item, 1) for item in kit.items]
    item = line.item or db.session.get(Item, line.item_id)
    return [(item, line.quantity)]


def _overlaps(a_start, a_end, b_start, b_end):
    """Intervalles semi-ouverts ; une fin absente signifie « sans limite »."""
    return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)


def _effective_end(loan, now):
    # Un prêt en cours dont la date de retour est dépassée bloque jusqu'au retour effectif.
    if loan.status == LoanStatus.EN_COURS and loan.end is not None and loan.end < now:
        return None
    return loan.end


def _usage(start, end, statuses, exclude_loan_id=None):
    """Quantités engagées par objet sur la période, et prêts concernés."""
    now = datetime.now()
    query = db.select(Loan).where(Loan.status.in_(statuses))
    if exclude_loan_id:
        query = query.where(Loan.id != exclude_loan_id)
    if end is not None:
        query = query.where(Loan.start < end)
    used, holders = defaultdict(int), defaultdict(list)
    for loan in db.session.scalars(query):
        if not _overlaps(start, end, loan.start, _effective_end(loan, now)):
            continue
        for line in loan.lines:
            if line.returned_at is not None:
                continue
            for item, qty in line_items(line):
                if item.is_quantity and item.consumption_mode == ConsumptionMode.CONSOMME:
                    continue  # un consommable distribué ne revient pas : il ne « bloque » pas de période
                used[item.id] += qty
                if loan not in holders[item.id]:
                    holders[item.id].append(loan)
    return used, holders


def _describe(loan):
    period = f"du {loan.start:%d/%m/%Y}" + (f" au {loan.end:%d/%m/%Y}" if loan.end else " (sans date de fin)")
    return f"{loan.type_label.lower()} n°{loan.id} {period} pour {loan.borrower.full_name}"


def requested_needs(lines):
    """Agrège les lignes demandées : {item_id: [objet, quantité]}. Lève LoanError si incohérent."""
    needs = {}
    if not lines:
        raise LoanError("Ajoutez au moins un objet ou un kit.")
    for line in lines:
        if line.item is not None or line.item_id:
            item = line.item or db.session.get(Item, line.item_id)
            if item.kit_id:
                raise LoanError(f"{item.code} fait partie du kit « {item.kit.name} » : prêtez le kit complet.")
            if not item.is_quantity and line.quantity != 1:
                raise LoanError(f"{item.code} est un objet individuel : quantité 1 uniquement.")
        for item, qty in line_items(line):
            if item.id in needs:
                if not item.is_quantity:
                    raise LoanError(f"{item.code} figure plusieurs fois dans la demande.")
                needs[item.id][1] += qty
            else:
                needs[item.id] = [item, qty]
    return needs


def check_availability(lines, start, end, exclude_loan_id=None):
    """Renvoie (problèmes bloquants, avertissements). Les avertissements signalent des demandes concurrentes."""
    needs = requested_needs(lines)
    problems, warnings = [], []
    used, holders = _usage(start, end, LoanStatus.BLOCKING, exclude_loan_id)
    pending_used, pending = _usage(start, end, (LoanStatus.DEMANDE,), exclude_loan_id)

    for item_id, (item, qty) in needs.items():
        label = f"{item.code} — {item.name}"
        if item.status in UNAVAILABLE_STATUSES:
            problems.append(f"{label} est indisponible ({item.status}).")
            continue
        if item.is_quantity and item.consumption_mode == ConsumptionMode.CONSOMME:
            if qty > item.quantity:
                problems.append(f"{label} : stock insuffisant ({item.stock_label} disponible(s)).")
            continue
        capacity = item.quantity if item.is_quantity else 1
        if used[item_id] + qty > capacity:
            if item.is_quantity:
                problems.append(f"{label} : {capacity - used[item_id]} disponible(s) sur la période, {qty} demandé(s).")
            else:
                problems.append(f"{label} est déjà pris : " + " ; ".join(_describe(l) for l in holders[item_id]) + ".")
        elif pending_used[item_id] + used[item_id] + qty > capacity:
            warnings.append(f"{label} est aussi demandé dans : " + " ; ".join(_describe(l) for l in pending[item_id]) + ".")
    return problems, warnings


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def _link(loan):
    return url_for("loans.detail", loan_id=loan.id)


def _validate_period(loan_type, start, end):
    if start is None:
        raise LoanError("La date de début est obligatoire.")
    if loan_type == LoanType.RESERVATION and end is None:
        raise LoanError("Une réservation doit avoir une date de fin.")
    if end is not None and end <= start:
        raise LoanError("La date de fin doit être après la date de début.")


def create_request(*, loan_type, borrower, guarantor, requested_by, start, end, lines, title=None,
                   comment=None, deposit_amount=None):
    """Crée une demande. Refuse si le matériel est déjà pris ; renvoie (prêt, avertissements)."""
    if loan_type not in LoanType.ALL:
        raise LoanError("Type de prêt inconnu.")
    _validate_period(loan_type, start, end)
    problems, warnings = check_availability(lines, start, end)
    if problems:
        raise LoanError(" ".join(problems))
    loan = Loan(type=loan_type, borrower=borrower, guarantor=guarantor, requested_by=requested_by, start=start,
                end=end, title=title or None, comment=comment or None, deposit_amount=deposit_amount)
    loan.lines = list(lines)
    db.session.add(loan)
    db.session.flush()
    log("loan.request", f"Demande de {loan.type_label.lower()} pour {borrower.full_name}"
        + (f", garant : {guarantor.full_name}" if guarantor else ""), loan=loan, user=requested_by)
    for item, _ in (pair for line in loan.lines for pair in line_items(line)):
        log("loan.request", f"Demandé dans le prêt n°{loan.id} ({borrower.full_name})", item=item, loan=loan,
            user=requested_by)
    notify([a for a in admins() if a.id != requested_by.id],
           f"Nouvelle demande de {loan.type_label.lower()} n°{loan.id} par {requested_by.full_name} "
           f"pour {borrower.full_name}.", _link(loan))
    return loan, warnings


def _require(loan, *statuses):
    if loan.status not in statuses:
        raise LoanError(f"Action impossible : ce prêt est « {loan.status_label.lower()} ».")


def validate(loan, admin):
    _require(loan, LoanStatus.DEMANDE)
    problems, _ = check_availability(loan.lines, loan.start, loan.end, exclude_loan_id=loan.id)
    if problems:
        raise LoanError(" ".join(problems))
    loan.status = LoanStatus.VALIDE
    loan.validated_by = admin
    loan.validated_at = datetime.now()
    log("loan.validate", "Prêt validé", loan=loan, user=admin)
    notify([loan.borrower, loan.guarantor, loan.requested_by], f"Votre {loan.type_label.lower()} n°{loan.id} "
           f"a été validé(e) par {admin.full_name}.", _link(loan))


def refuse(loan, admin, reason=None):
    _require(loan, LoanStatus.DEMANDE)
    loan.status = LoanStatus.REFUSE
    loan.validated_by = admin
    loan.validated_at = datetime.now()
    loan.refusal_reason = (reason or "").strip()[:255] or None
    log("loan.refuse", "Prêt refusé" + (f" : {loan.refusal_reason}" if loan.refusal_reason else ""), loan=loan,
        user=admin)
    notify([loan.guarantor, loan.requested_by], f"La demande n°{loan.id} pour {loan.borrower.full_name} a été "
           f"refusée" + (f" : {loan.refusal_reason}" if loan.refusal_reason else "."), _link(loan))


def cancel(loan, user):
    _require(loan, LoanStatus.DEMANDE, LoanStatus.VALIDE)
    loan.status = LoanStatus.ANNULE
    log("loan.cancel", "Prêt annulé", loan=loan, user=user)
    notify([u for u in (loan.borrower, loan.guarantor, loan.requested_by) if u is not None and u.id != user.id],
           f"Le prêt n°{loan.id} a été annulé par {user.full_name}.", _link(loan))


def checkout(loan, user):
    """Remise du matériel à l'emprunteur."""
    _require(loan, LoanStatus.VALIDE)
    problems, _ = check_availability(loan.lines, loan.start, loan.end, exclude_loan_id=loan.id)
    if problems:
        raise LoanError(" ".join(problems))
    now = datetime.now()
    out_status = ItemStatus.EN_GARDE if loan.type == LoanType.GARDE else ItemStatus.SORTI
    for line in loan.lines:
        for item, qty in line_items(line):
            if not item.is_quantity:
                item.status = out_status
                log("loan.checkout", f"Remis à {loan.borrower.full_name} (prêt n°{loan.id})", item=item, loan=loan,
                    user=user)
            elif item.consumption_mode == ConsumptionMode.CONSOMME:
                item.quantity -= qty
                db.session.add(StockMovement(item=item, delta=-qty, reason="distribution", user=user, loan_id=loan.id,
                                             comment=f"Prêt n°{loan.id} — {loan.borrower.full_name}"))
        if line.item is not None and not line.item.is_quantity:
            line.condition_out = line.item.condition
        if line.item is not None and line.item.is_quantity and line.item.consumption_mode == ConsumptionMode.CONSOMME:
            line.returned_at = now  # distribué : rien à rendre
    loan.status = LoanStatus.EN_COURS
    loan.checked_out_at = now
    log("loan.checkout", "Matériel remis", loan=loan, user=user)
    if all(line.returned_at for line in loan.lines):
        _close(loan, user, now)


def checkin(loan, user, returns, deposit_returned=False):
    """Retour de tout ou partie du matériel. `returns` : {line_id: nouvel état ou None}."""
    _require(loan, LoanStatus.EN_COURS)
    lines = {line.id: line for line in loan.lines}
    if not returns:
        raise LoanError("Sélectionnez au moins un élément rendu.")
    now = datetime.now()
    for line_id, condition in returns.items():
        line = lines.get(line_id)
        if line is None:
            raise LoanError("Ligne de prêt inconnue.")
        if line.returned_at is not None:
            continue
        line.returned_at = now
        line.condition_in = condition or None
        for item, _ in line_items(line):
            if not item.is_quantity:
                changes = {}
                if item.status in (ItemStatus.EN_GARDE, ItemStatus.SORTI):
                    changes["status"] = [item.status, ItemStatus.DISPONIBLE]
                    item.status = ItemStatus.DISPONIBLE
                if condition and condition != item.condition:
                    changes["condition"] = [item.condition, condition]
                    item.condition = condition
                log("loan.checkin", f"Rendu par {loan.borrower.full_name} (prêt n°{loan.id})", item=item, loan=loan,
                    user=user, changes=changes)
    if deposit_returned:
        loan.deposit_returned = True
    if all(line.returned_at for line in loan.lines):
        _close(loan, user, now)
    else:
        log("loan.checkin", "Retour partiel", loan=loan, user=user)


def _close(loan, user, now):
    loan.status = LoanStatus.RENDU
    loan.returned_at = now
    log("loan.close", "Prêt clôturé : tout est rendu", loan=loan, user=user)


def change_end(loan, admin, new_end):
    """Prolonger ou raccourcir un prêt validé ou en cours."""
    _require(loan, LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS)
    _validate_period(loan.type, loan.start, new_end)
    pending_lines = [line for line in loan.lines if line.returned_at is None] or loan.lines
    problems, _ = check_availability(pending_lines, loan.start, new_end, exclude_loan_id=loan.id)
    if problems and loan.status != LoanStatus.DEMANDE:
        raise LoanError(" ".join(problems))
    old = loan.end
    loan.end = new_end
    log("loan.extend", "Date de fin modifiée", loan=loan, user=admin,
        changes={"end": [old.strftime("%d/%m/%Y %H:%M") if old else None,
                         new_end.strftime("%d/%m/%Y %H:%M") if new_end else None]})
    notify([loan.borrower, loan.guarantor], f"La date de fin du prêt n°{loan.id} a été modifiée.", _link(loan))


def build_lines(item_ids, quantities, kit_ids):
    """Construit les lignes à partir du formulaire ; ignore les identifiants inconnus."""
    lines = []
    for item_id in dict.fromkeys(item_ids):
        item = db.session.get(Item, item_id)
        if item is None:
            continue
        qty = quantities.get(item_id, 1) if item.is_quantity else 1
        if qty < 1:
            raise LoanError(f"Quantité invalide pour {item.code}.")
        lines.append(LoanLine(item=item, quantity=qty))
    for kit_id in dict.fromkeys(kit_ids):
        kit = db.session.get(Kit, kit_id)
        if kit is None:
            continue
        if not kit.items:
            raise LoanError(f"Le kit « {kit.name} » est vide.")
        lines.append(LoanLine(kit=kit, quantity=1))
    return lines
