from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user

from ..permissions import require
from ..extensions import db
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
            mailer.send(current_user.email, f"[{current_app.config['ASSO_NAME']}] Email de test",
                        "Si vous lisez ceci, l'envoi d'emails de l'inventaire fonctionne.")
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
