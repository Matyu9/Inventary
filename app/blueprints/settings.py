from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Length

from ..permissions import require
from ..extensions import db
from ..models import EmailTemplate, Item, Loan, Notification, PublicReport
from ..services import mailer, reminders, sessions
from ..services.activity import log

bp = Blueprint("settings", __name__)


@bp.route("/parametres")
@require("settings.manage")
def index():
    cfg = current_app.config
    open_sessions, connected = sessions.stats()
    return render_template("settings/index.html", cfg=cfg, smtp=mailer.smtp_enabled(),
                           open_sessions=open_sessions, connected=connected)


@bp.route("/parametres/email-test", methods=["POST"])
@require("settings.manage")
def test_email():
    if not mailer.smtp_enabled():
        flash("Le SMTP n'est pas configuré (variables SMTP_* du fichier .env).", "error")
    elif not current_user.email:
        flash("Votre compte n'a pas d'adresse email.", "error")
    else:
        try:
            mailer.send(current_user.email, *mailer.compose_test(current_user))
        except Exception as exc:  # noqa: BLE001
            flash(f"Échec de l'envoi : {exc}", "error")
        else:
            flash(f"Email de test envoyé à {current_user.email}.", "success")
    return redirect(url_for(".index"))


@bp.route("/parametres/rappels", methods=["POST"])
@require("settings.manage")
def run_reminders():
    flash(reminders.run(), "info")
    return redirect(url_for(".index"))


@bp.route("/parametres/deconnecter-tout", methods=["POST"])
@require("settings.manage")
def revoke_sessions():
    """Ferme toutes les sessions (sauf la sienne) : en cas de doute sur un compte ou un appareil perdu."""
    closed = sessions.revoke_all()
    log("sessions.revoke_all", f"Déconnexion de tout le monde ({closed} session(s) fermée(s))")
    db.session.commit()
    flash(f"{closed} session(s) fermée(s) : chacun devra se reconnecter. Vous restez connecté.", "success")
    return redirect(url_for(".index"))


# ---------------------------------------------------------------------------
# Textes des emails
# ---------------------------------------------------------------------------

class EmailTemplateForm(FlaskForm):
    subject = StringField("Objet", validators=[DataRequired(), Length(max=200)])
    intro = TextAreaField("Introduction", validators=[DataRequired(), Length(max=2000)])
    button = StringField("Bouton", validators=[DataRequired(), Length(max=60)])

    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self._kind = kind

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        for field in (self.subject, self.intro, self.button):
            unknown = mailer.unknown_variables(field.data or "", self._kind)
            if unknown:
                field.errors.append("Variable(s) inconnue(s) : " + ", ".join(f"{{{v}}}" for v in unknown) + ".")
                ok = False
        return ok


def _kind_or_404(kind):
    return mailer.KINDS.get(kind) or abort(404)


@bp.route("/parametres/emails")
@require("settings.manage")
def emails():
    custom = {t.kind: t for t in db.session.scalars(db.select(EmailTemplate))}
    return render_template("settings/emails.html", kinds=mailer.KINDS, custom=custom, texts=mailer.texts)


@bp.route("/parametres/emails/<kind>", methods=["GET", "POST"])
@require("settings.manage")
def edit_email(kind):
    default = _kind_or_404(kind)
    custom = db.session.get(EmailTemplate, kind)
    subject, intro, button = mailer.texts(kind)
    form = EmailTemplateForm(kind=kind, data={"subject": subject, "intro": intro, "button": button})
    if form.validate_on_submit():
        if custom is None:
            custom = EmailTemplate(kind=kind)
            db.session.add(custom)
        custom.subject = " ".join(form.subject.data.split())
        custom.intro = form.intro.data.strip().replace("\r\n", "\n")
        custom.button = form.button.data.strip()
        log("settings.email", f"Email « {default.label} » personnalisé")
        db.session.commit()
        flash("Email enregistré.", "success")
        return redirect(url_for(".edit_email", kind=kind))
    return render_template("settings/email_form.html", form=form, kind=kind, default=default, custom=custom,
                           variables=mailer.variables_for(kind))


@bp.route("/parametres/emails/<kind>/reinitialiser", methods=["POST"])
@require("settings.manage")
def reset_email(kind):
    default = _kind_or_404(kind)
    custom = db.session.get(EmailTemplate, kind)
    if custom is not None:
        db.session.delete(custom)
        log("settings.email", f"Email « {default.label} » rétabli par défaut")
        db.session.commit()
    flash("Textes par défaut rétablis.", "success")
    return redirect(url_for(".edit_email", kind=kind))


@bp.route("/parametres/emails/<kind>/apercu")
@require("settings.manage")
def preview_email(kind):
    """Rendu HTML de l'email, d'après la dernière notification de ce type (ou le dernier prêt / objet)."""
    _kind_or_404(kind)
    sample = db.session.scalar(db.select(Notification).where(Notification.kind == kind)
                               .order_by(Notification.id.desc()).limit(1))
    if kind.startswith("loan."):
        loan = sample.loan if sample and sample.loan else db.session.scalar(
            db.select(Loan).order_by(Loan.id.desc()).limit(1))
        item = None
    else:
        loan = None
        item = sample.item if sample and sample.item else db.session.scalar(
            db.select(Item).order_by(Item.id.desc()).limit(1))
    report = None
    if kind.startswith("public."):
        report = sample.report if sample and sample.report else PublicReport(
            kind=PublicReport.TROUVE if kind.startswith("public.found") else PublicReport.PROBLEME, item=item,
            message="(texte laissé par la personne qui a scanné le QR code)", name="Camille",
            contact="06 00 00 00 00", created_at=datetime.now())
        item = None
    # Notification jamais enregistrée : n'existe que le temps du rendu.
    notification = Notification(user=current_user, kind=kind, loan=loan, item=item, report=report,
                                message=sample.message if sample else "(texte de la notification)",
                                link=sample.link if sample else "/")
    _, _, html = mailer.compose(notification)
    response = current_app.make_response(html)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"  # affiché dans un <iframe> de la page d'édition
    # Styles en ligne indispensables aux emails ; aucun script ; encadrable par l'appli seulement.
    response.headers["Content-Security-Policy"] = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                                                   "frame-ancestors 'self'")
    return response
