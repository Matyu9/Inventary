from urllib.parse import urlsplit

from authlib.integrations.base_client import OAuthError
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField
from wtforms.validators import DataRequired, EqualTo, Length

from ..extensions import db
from ..models import AccountToken, User
from ..services import accounts, mailer, oidc, sessions, throttle
from ..services.activity import log

# Limitation des tentatives : au-delà de MAX_FAILURES échecs en WINDOW secondes pour un même
# email (ou une même IP), la connexion est refusée temporairement.
MAX_FAILURES = 8
WINDOW = 15 * 60
_failures = throttle.Limiter(MAX_FAILURES, WINDOW)
# Hash factice pour que la vérification prenne le même temps si le compte n'existe pas.
_DUMMY = User()
_DUMMY.set_password("pas-un-vrai-mot-de-passe")


_throttled, _record_failure, _clear_failures = _failures.blocked, _failures.hit, _failures.clear

bp = Blueprint("auth", __name__)


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired()])
    password = PasswordField("Mot de passe", validators=[DataRequired()])
    remember = BooleanField("Rester connecté")


class PasswordForm(FlaskForm):
    current = PasswordField("Mot de passe actuel", validators=[DataRequired()])
    new = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirmation", validators=[EqualTo("new", "Les mots de passe ne correspondent pas.")])


class ForgotForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired()])


class NewPasswordForm(FlaskForm):
    new = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirmation", validators=[EqualTo("new", "Les mots de passe ne correspondent pas.")])


@bp.before_app_request
def force_password_change():
    """Un compte créé avec un mot de passe provisoire doit le changer avant tout."""
    if (current_user.is_authenticated and current_user.must_change_password
            and request.endpoint not in ("auth.password", "auth.logout", "static", "csp.report",
                                         "settings.logo_file")):
        if request.method == "GET" and not request.headers.get("HX-Request"):
            flash("Choisissez un nouveau mot de passe pour continuer.", "warning")
        return redirect(url_for("auth.password"))


def safe_next(target, default=None):
    """N'accepte qu'un chemin local (« /… »), jamais « //hôte » ni « /\\hôte » (normalisé en // par les navigateurs)."""
    if (target and target.startswith("/") and not target.startswith("//") and "\\" not in target
            and not urlsplit(target).netloc and not urlsplit(target).scheme):
        return target
    return default or url_for("dashboard.index")


def _login_page(form, status=200):
    return render_template("auth/login.html", form=form, oidc_enabled=oidc.enabled(),
                           oidc_only=oidc.enabled() and current_app.config["OIDC_ONLY"]), status


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if request.method == "POST" and oidc.enabled() and current_app.config["OIDC_ONLY"]:
        abort(404)
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        keys = (f"email:{email}", f"ip:{request.remote_addr}")
        if _throttled(keys):
            flash("Trop de tentatives : réessayez dans quelques minutes.", "error")
            return _login_page(form, 429)
        user = db.session.scalar(db.select(User).filter_by(email=email))
        valid = (user or _DUMMY).check_password(form.password.data)
        if user and user.active and valid:
            _clear_failures(keys)
            sessions.start(user, remember=form.remember.data)
            return redirect(safe_next(request.args.get("next")))
        _record_failure(keys)
        flash("Email ou mot de passe incorrect.", "error")
    return _login_page(form)


@bp.route("/connexion/oidc")
def oidc_login():
    if not oidc.enabled():
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    session["oidc_next"] = safe_next(request.args.get("next"))
    session["oidc_remember"] = request.args.get("remember") == "1"
    # BASE_URL plutôt que l'hôte de la requête : c'est l'URL déclarée chez le fournisseur.
    redirect_uri = current_app.config["BASE_URL"] + url_for(".oidc_callback")
    try:
        return oidc.client().authorize_redirect(redirect_uri)
    except Exception:
        current_app.logger.exception("Fournisseur OIDC injoignable")
        flash("Le fournisseur d'identité est injoignable pour le moment.", "error")
        return redirect(url_for(".login"))


@bp.route("/connexion/oidc/retour")
def oidc_callback():
    if not oidc.enabled():
        abort(404)
    next_url = safe_next(session.pop("oidc_next", None))
    remember = session.pop("oidc_remember", False)
    try:
        token = oidc.client().authorize_access_token()
        claims = dict(token.get("userinfo") or {})
        groups_claim = current_app.config["OIDC_GROUPS_CLAIM"]
        if claims.get("sub") and ("email" not in claims or groups_claim not in claims):
            # Certains fournisseurs ne mettent l'email ou les groupes que dans /userinfo.
            try:
                extra = oidc.client().userinfo(token=token)
            except Exception:
                current_app.logger.warning("Lecture de /userinfo impossible", exc_info=True)
                extra = {}
            if extra.get("sub") == claims["sub"]:
                claims = {**extra, **claims}
    except OAuthError as e:
        current_app.logger.warning("Connexion OIDC refusée : %s", e)
        flash("La connexion via le fournisseur d'identité a échoué ou a été annulée.", "error")
        return redirect(url_for(".login"))
    except Exception:
        current_app.logger.exception("Erreur pendant la connexion OIDC")
        flash("La connexion via le fournisseur d'identité a échoué.", "error")
        return redirect(url_for(".login"))
    try:
        user = oidc.resolve_user(claims)
    except oidc.OidcRefused as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for(".login"))
    db.session.commit()
    sessions.start(user, remember=remember)
    return redirect(next_url)


@bp.route("/deconnexion", methods=["POST"])
@login_required
def logout():
    sessions.end()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/mot-de-passe", methods=["GET", "POST"])
@login_required
def password():
    if not current_user.password_hash:
        flash("Votre compte se connecte via le fournisseur d'identité : il n'a pas de mot de passe.", "info")
        return redirect(url_for("dashboard.index"))
    form = PasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current.data):
            form.current.errors.append("Mot de passe actuel incorrect.")
        elif form.new.data == form.current.data:
            form.new.errors.append("Le nouveau mot de passe doit être différent de l'actuel.")
        else:
            current_user.set_password(form.new.data)
            current_user.must_change_password = False
            sessions.revoke(current_user, keep_current=True)  # une session volée ne survit pas au changement
            db.session.commit()
            flash("Mot de passe modifié.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/password.html", form=form)


def _password_login_enabled():
    return not (oidc.enabled() and current_app.config["OIDC_ONLY"])


@bp.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def forgot():
    if not _password_login_enabled():
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for(".password"))
    form = ForgotForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        keys = (f"reset:{email}", f"reset-ip:{request.remote_addr}")
        if _throttled(keys):
            flash("Trop de demandes : réessayez dans quelques minutes.", "error")
            return render_template("auth/forgot.html", form=form, smtp=mailer.smtp_enabled()), 429
        _record_failure(keys)  # chaque demande compte : pas d'envoi d'emails en rafale
        user = db.session.scalar(db.select(User).filter_by(email=email))
        if user and user.active and not user.archived_at and mailer.smtp_enabled():
            raw = accounts.issue(user, AccountToken.RESET)
            log("user.reset_request", f"Lien de réinitialisation demandé par {user.full_name}", user=user,
                target=user)
            db.session.commit()
            accounts.send_reset(user, raw)
        # Même réponse que le compte existe ou non : la page ne révèle pas qui a un compte.
        flash("Si un compte actif correspond à cette adresse, un lien de réinitialisation vient d'y être envoyé.",
              "info")
        return redirect(url_for(".login"))
    return render_template("auth/forgot.html", form=form, smtp=mailer.smtp_enabled())


@bp.route("/lien/<token>", methods=["GET", "POST"])
def token_link(token):
    """Lien reçu par email : choisir son mot de passe (invitation, oubli) ou confirmer une nouvelle adresse."""
    found = accounts.find(token)
    if found is None:
        flash("Ce lien n'est plus valable : il a expiré ou a déjà servi.", "error")
        return redirect(url_for(".login"))
    user = found.user
    if found.purpose == AccountToken.EMAIL:
        return _confirm_email(found, user)
    form = NewPasswordForm()
    if form.validate_on_submit():
        if current_user.is_authenticated:
            sessions.end()
        user.set_password(form.new.data)
        user.must_change_password = False
        accounts.consume(found, AccountToken.INVITE, AccountToken.RESET)
        sessions.revoke(user)  # un appareil volé ne survit pas à la réinitialisation
        invited = found.purpose == AccountToken.INVITE
        log("user.password", f"{user.full_name} a choisi son mot de passe"
            + (" (invitation)" if invited else " (mot de passe oublié)"), user=user, target=user)
        sessions.start(user)  # commit
        flash("Bienvenue ! Votre mot de passe est enregistré." if invited else "Mot de passe modifié.", "success")
        return redirect(url_for("dashboard.index"))
    return render_template("auth/token_password.html", form=form, user=user,
                           invited=found.purpose == AccountToken.INVITE)


def _confirm_email(found, user):
    taken = db.session.scalar(db.select(User).where(db.func.lower(User.email) == found.email, User.id != user.id))
    if request.method == "POST" and FlaskForm().validate_on_submit():
        if taken:
            flash("Cette adresse est déjà utilisée par un autre compte.", "error")
            return redirect(url_for(".login"))
        old = user.email
        user.email = found.email
        accounts.consume(found)
        log("user.email", f"Email de {user.full_name} modifié : {old or '—'} → {user.email}", user=user, target=user)
        db.session.commit()
        if old:
            accounts.send_email_changed_notice(user, old)
        flash("Adresse email confirmée.", "success")
        return redirect(url_for("dashboard.index") if current_user.is_authenticated else url_for(".login"))
    return render_template("auth/token_email.html", user=user, email=found.email, taken=taken,
                           form=FlaskForm())
