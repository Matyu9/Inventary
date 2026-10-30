"""Envoi d'emails via SMTP (facultatif)."""
import logging
import re
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app, render_template

from ..extensions import db
from ..filters import date_fr, euros
from ..models import EmailTemplate, LoanStatus, Notification, User

logger = logging.getLogger(__name__)
# Une notification plus ancienne n'est plus envoyée (ex. SMTP configuré après coup).
MAX_AGE = timedelta(days=2)


def smtp_enabled():
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and (cfg.get("SMTP_FROM") or cfg.get("SMTP_USER")))


def send(to, subject, text, html=None):
    """Envoie un email ; `html`, s'il est fourni, s'ajoute en alternative à la version texte."""
    cfg = current_app.config
    message = EmailMessage()
    message["From"] = formataddr((f"Inventaire {cfg['ASSO_NAME']}", cfg["SMTP_FROM"] or cfg["SMTP_USER"]))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
        if cfg["SMTP_TLS"]:
            smtp.starttls()
        if cfg["SMTP_USER"]:
            smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        smtp.send_message(message)


class Kind:
    def __init__(self, label, audience, subject, button, tone, intro="{message}"):
        self.label, self.audience, self.tone = label, audience, tone
        self.subject, self.intro, self.button = subject, intro, button  # textes par défaut


# Types de notification envoyés par email. Objet, introduction et bouton se personnalisent dans les paramètres
# (modèle EmailTemplate) ; le ton (ok, warn, danger ou brand) colore le bandeau et le récapitulatif.
KINDS = {
    "loan.request": Kind("Nouvelle demande de prêt", "Admins", "Nouvelle demande de {type} pour {emprunteur}",
                         "Examiner la demande", "brand"),
    "loan.validated": Kind("Prêt validé", "Emprunteur, garant, demandeur", "Votre {type} n°{numero} est validée",
                           "Voir le prêt", "ok"),
    "loan.refused": Kind("Demande refusée", "Garant, demandeur", "Demande n°{numero} refusée", "Voir la demande",
                         "danger"),
    "loan.cancelled": Kind("Prêt annulé", "Personnes concernées", "{Type} n°{numero} annulée", "Voir le prêt",
                           "danger"),
    "loan.end_changed": Kind("Date de retour modifiée", "Emprunteur, garant",
                             "{Type} n°{numero} : nouvelle date de retour", "Voir le prêt", "brand"),
    "loan.start_soon": Kind("Matériel à récupérer (veille)", "Emprunteur, garant",
                            "Matériel à récupérer le {debut}", "Voir le prêt", "brand"),
    "loan.due_soon": Kind("Retour attendu (veille)", "Emprunteur, garant",
                          "Retour du matériel attendu le {fin}", "Voir le prêt", "warn"),
    "loan.overdue": Kind("Retard (relance quotidienne)", "Emprunteur, garant, admins",
                         "Matériel en retard — {type} n°{numero}", "Voir le prêt", "danger"),
    "loan.not_picked": Kind("Réservation jamais remise", "Admins", "Réservation n°{numero} jamais remise",
                            "Voir la réservation", "warn"),
    "loan.pending": Kind("Demande en attente (relance)", "Admins", "Demande n°{numero} en attente de validation",
                         "Examiner la demande", "warn"),
    "item.reminder": Kind("Rappel sur un objet", "Destinataires du rappel", "Rappel : {code} {objet}",
                          "Voir l'objet", "brand"),
    "item.maintenance": Kind("Entretien à prévoir", "Admins", "Entretien à prévoir : {code} {objet}",
                             "Voir l'objet", "warn"),
    "item.low_stock": Kind("Stock bas", "Admins", "Stock bas : {objet}", "Voir le stock", "warn"),
}

# Variables utilisables dans les textes, par famille de type ("loan" ou "item").
VARIABLES = {
    None: {"prenom": "Prénom du destinataire", "asso": "Nom de l'association",
           "message": "Texte de la notification, tel qu'affiché dans l'appli"},
    "loan": {"numero": "Numéro du prêt", "type": "« réservation » ou « garde »", "Type": "« Réservation » ou « Garde »",
             "titre": "Titre du prêt", "emprunteur": "Nom de l'emprunteur", "garant": "Nom du garant",
             "debut": "Date et heure de début", "fin": "Date et heure de fin", "motif": "Motif du refus"},
    "item": {"code": "Code de l'objet", "objet": "Nom de l'objet", "emplacement": "Emplacement de l'objet",
             "stock": "Stock restant (objets au stock)"},
}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def variables_for(kind):
    return {**VARIABLES[None], **VARIABLES[kind.split(".")[0]]}


def fill(text, values):
    """Remplace les {variables} connues ; le reste du texte est laissé tel quel (pas de format() : rien d'exécuté)."""
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def unknown_variables(text, kind):
    return sorted(set(PLACEHOLDER.findall(text)) - set(variables_for(kind)))


def texts(kind):
    """(objet, introduction, bouton) d'un type : version personnalisée si elle existe, sinon textes par défaut."""
    custom = db.session.get(EmailTemplate, kind)
    default = KINDS[kind]
    return (custom.subject, custom.intro, custom.button) if custom else (default.subject, default.intro, default.button)


def _values(notification):
    loan, item = notification.loan, notification.item
    values = {"prenom": notification.user.first_name, "asso": current_app.config["ASSO_NAME"],
              "message": notification.message}
    if loan is not None:
        values.update(numero=loan.id, type=loan.type_label.lower(), Type=loan.type_label, titre=loan.title or "",
                      emprunteur=loan.borrower.full_name, garant=loan.guarantor.full_name if loan.guarantor else "",
                      debut=date_fr(loan.start, with_time=True), fin=date_fr(loan.end, with_time=True),
                      motif=loan.refusal_reason or "")
    if item is not None:
        values.update(code=item.code, objet=item.name, emplacement=item.location.path if item.location else "",
                      stock=item.stock_label if item.is_quantity else "")
    return values


def _line_label(line):
    if line.kit is not None:
        count = len(line.kit.items)
        return f"Kit {line.kit.name} ({count} objet{'s' if count > 1 else ''})"
    label = f"{line.item.code} {line.item.name}"
    if line.item.is_quantity:
        label += f" × {line.quantity}" + (f" {line.item.unit}" if line.item.unit else "")
    return label


def _recap(loan, item):
    """Titre, lignes (libellé, valeur) et liste du matériel du bloc récapitulatif."""
    if loan is not None:
        rows = [
            ("Statut", loan.status_label),
            ("Titre", loan.title),
            ("Emprunteur", loan.borrower.full_name),
            ("Garant", loan.guarantor.full_name if loan.guarantor else None),
            ("Début", date_fr(loan.start, with_time=True)),
            ("Fin", date_fr(loan.end, with_time=True) if loan.end else "Sans date de fin"),
            ("Caution", euros(loan.deposit_amount) if loan.deposit_amount else None),
            ("Motif du refus", loan.refusal_reason if loan.status == LoanStatus.REFUSE else None),
        ]
        return (f"{loan.type_label} n°{loan.id}", [r for r in rows if r[1]],
                [_line_label(line) for line in loan.lines])
    if item is not None:
        rows = [
            ("Emplacement", item.location.path if item.location else None),
            ("Stock", item.stock_label if item.is_quantity else None),
            ("Seuil d'alerte", item.low_stock_threshold if item.is_quantity else None),
        ]
        return f"{item.code} {item.name}", [r for r in rows if r[1] is not None], []
    return None, [], []


def compose(notification):
    """(objet, texte, html) de l'email correspondant à une notification."""
    cfg = current_app.config
    kind = KINDS.get(notification.kind)
    # Prêt ou objet supprimé depuis, ou notification d'avant les types : email générique.
    if kind and (notification.loan if notification.kind.startswith("loan.") else notification.item) is None:
        kind = None
    if kind:
        values = _values(notification)
        subject, intro, button = (fill(text, values) for text in texts(notification.kind))
        recap_title, rows, lines = _recap(notification.loan, notification.item)
    else:
        subject, intro, button = notification.message, notification.message, "Ouvrir l'inventaire"
        recap_title, rows, lines = None, [], []
    url = f"{cfg['BASE_URL']}{notification.link}" if notification.link else None
    return _render(notification.user, subject, intro, button, url, kind.tone if kind else "brand",
                   recap_title, rows, lines)


def compose_test(user):
    return _render(user, "Email de test", "Si vous lisez ceci, l'envoi d'emails de l'inventaire fonctionne.",
                   "Ouvrir l'inventaire", current_app.config["BASE_URL"], "ok")


def _render(user, subject, intro, button, url, tone, recap_title=None, rows=(), lines=()):
    subject = _shorten(" ".join(subject.split()))  # pas de retour à la ligne dans un en-tête
    # Paragraphes séparés par une ligne vide ; un simple retour à la ligne est conservé dans le paragraphe.
    paragraphs = [p.strip().splitlines() for p in re.split(r"\n\s*\n", intro.strip()) if p.strip()]
    context = {"user": user, "subject": subject, "intro": intro.strip(), "paragraphs": paragraphs,
               "button": button.strip() or "Ouvrir l'inventaire", "url": url, "tone": tone,
               "recap_title": recap_title, "rows": rows, "lines": lines}
    return (f"[{current_app.config['ASSO_NAME']}] {subject}", render_template("emails/notification.txt", **context),
            render_template("emails/notification.html", **context))


def _shorten(text, limit=100):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def deliver_pending(limit=200):
    """Envoie par email les notifications récentes pas encore envoyées. Renvoie le nombre d'emails envoyés."""
    if not smtp_enabled():
        return 0
    since = datetime.now() - MAX_AGE
    pending = db.session.scalars(
        db.select(Notification).join(User).where(
            Notification.emailed.is_(False), Notification.created_at >= since,
            User.email.is_not(None), User.active.is_(True))
        .order_by(Notification.id).limit(limit)).all()
    sent = 0
    for notification in pending:
        try:
            send(notification.user.email, *compose(notification))
        except (OSError, smtplib.SMTPException):
            logger.exception("Échec d'envoi à %s", notification.user.email)
            break  # serveur indisponible : on réessaiera au prochain passage
        notification.emailed = True
        sent += 1
    db.session.commit()
    return sent


def deliver_quietly():
    """Après une action utilisateur : tente l'envoi sans jamais faire échouer la requête."""
    try:
        return deliver_pending(limit=20)
    except Exception:  # noqa: BLE001
        logger.exception("Envoi des emails impossible")
        db.session.rollback()
        return 0
