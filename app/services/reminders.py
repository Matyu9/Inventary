"""Rappels automatiques, lancés périodiquement par `flask run-reminders` (service scheduler)."""
from datetime import date, datetime, timedelta

from ..extensions import db
from ..models import (Item, Loan, LoanStatus, LoanType, Maintenance, Reminder, Tracking)
from . import mailer
from .notify import admins, notify


def _loan_link(loan):
    return f"/prets/{loan.id}"


def due_maintenances(until):
    """Échéances d'entretien jusqu'à `until`, en ne gardant que l'entretien le plus récent de chaque objet."""
    latest = {}
    for m in db.session.scalars(db.select(Maintenance).order_by(Maintenance.date, Maintenance.id)):
        latest[m.item_id] = m
    due = [m for m in latest.values() if m.next_due is not None and m.next_due <= until]
    return sorted(due, key=lambda m: m.next_due)


def run(now=None):
    now = now or datetime.now()
    today = now.date()
    counts = {}

    def add(kind, created):
        counts[kind] = counts.get(kind, 0) + len(created)

    # Rappels manuels arrivés à échéance.
    for reminder in db.session.scalars(db.select(Reminder).where(Reminder.sent.is_(False), Reminder.due_date <= today)):
        recipients = reminder.recipients or admins()
        add("rappels", notify(recipients, f"Rappel — {reminder.item.code} {reminder.item.name} : {reminder.message}",
                              f"/objets/{reminder.item_id}", kind="item.reminder", item=reminder.item))
        reminder.sent = True

    # Prochains entretiens (dans les 7 jours).
    for m in due_maintenances(today + timedelta(days=7)):
        when = "dépassé" if m.next_due < today else f"prévu le {m.next_due:%d/%m/%Y}"
        add("entretiens", notify(admins(), f"Entretien {when} : {m.item.code} {m.item.name} ({m.kind}).",
                                 f"/objets/{m.item_id}", dedup_key=f"maint:{m.id}:{m.next_due}",
                                 kind="item.maintenance", item=m.item))

    # Réservations validées qui commencent dans les 24 h.
    for loan in db.session.scalars(db.select(Loan).where(
            Loan.status == LoanStatus.VALIDE, Loan.start > now, Loan.start <= now + timedelta(days=1))):
        add("debuts", notify([loan.borrower, loan.guarantor],
                             f"{loan.type_label} n°{loan.id} : le matériel est à récupérer le {loan.start:%d/%m à %H:%M}.",
                             _loan_link(loan), dedup_key=f"start:{loan.id}:{loan.start:%Y%m%d%H%M}",
                             kind="loan.start_soon", loan=loan))

    # Retours attendus dans les 24 h.
    for loan in db.session.scalars(db.select(Loan).where(
            Loan.status == LoanStatus.EN_COURS, Loan.end > now, Loan.end <= now + timedelta(days=1))):
        add("retours", notify([loan.borrower, loan.guarantor],
                              f"{loan.type_label} n°{loan.id} : retour du matériel attendu le {loan.end:%d/%m à %H:%M}.",
                              _loan_link(loan), dedup_key=f"due:{loan.id}:{loan.end:%Y%m%d%H%M}",
                              kind="loan.due_soon", loan=loan))

    # Retards : une relance par jour.
    for loan in db.session.scalars(db.select(Loan).where(Loan.status == LoanStatus.EN_COURS, Loan.end < now)):
        days = (today - loan.end.date()).days
        late = f"en retard de {days} jour{'s' if days > 1 else ''}" if days else f"en retard depuis {loan.end:%H:%M}"
        add("retards", notify([loan.borrower, loan.guarantor, *admins()],
                              f"{loan.type_label} n°{loan.id} {late} ({loan.borrower.full_name}).",
                              _loan_link(loan), dedup_key=f"overdue:{loan.id}:{today}", kind="loan.overdue",
                              loan=loan))

    # Validées non récupérées 2 jours après le début.
    for loan in db.session.scalars(db.select(Loan).where(
            Loan.status == LoanStatus.VALIDE, Loan.type == LoanType.RESERVATION, Loan.start < now - timedelta(days=2))):
        add("non_recuperes", notify(admins(), f"Réservation n°{loan.id} validée mais jamais remise.", _loan_link(loan),
                                    dedup_key=f"notpicked:{loan.id}", kind="loan.not_picked", loan=loan))

    # Demandes en attente depuis plus de 2 jours : relance quotidienne des admins.
    for loan in db.session.scalars(db.select(Loan).where(
            Loan.status == LoanStatus.DEMANDE, Loan.created_at < now - timedelta(days=2))):
        add("demandes", notify(admins(), f"La demande n°{loan.id} attend toujours une validation.", _loan_link(loan),
                               dedup_key=f"pending:{loan.id}:{today}", kind="loan.pending", loan=loan))

    # Stocks bas : une relance par semaine.
    week = today.isocalendar()
    for item in db.session.scalars(db.select(Item).where(
            Item.tracking == Tracking.QUANTITE, Item.low_stock_threshold.is_not(None),
            Item.quantity <= Item.low_stock_threshold)):
        add("stocks", notify(admins(), f"Stock bas : {item.name} (il reste {item.stock_label}).",
                             f"/objets/{item.id}", dedup_key=f"lowstock:{item.id}:{week[0]}-{week[1]}",
                             kind="item.low_stock", item=item))

    db.session.commit()
    emails = mailer.deliver_pending()
    total = sum(counts.values())
    detail = ", ".join(f"{k} : {v}" for k, v in counts.items() if v)
    return f"{total} notification(s) créée(s){' (' + detail + ')' if detail else ''}, {emails} email(s) envoyé(s)."
