import threading
import time
from urllib.parse import urlsplit

from authlib.integrations.base_client import OAuthError
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField
from wtforms.validators import DataRequired, EqualTo, Length

from ..extensions import db
from ..models import User
from ..services import oidc, sessions

# Limitation des tentatives : au-delà de MAX_FAILURES échecs en WINDOW secondes pour un même
# email (ou une même IP), la connexion est refusée temporairement. Mémoire par processus :
# suffisant pour une petite association, sans dépendance supplémentaire.
MAX_FAILURES = 8
WINDOW = 15 * 60
_failures = {}
_lock = threading.Lock()
# Hash factice pour que la vérification prenne le même temps si le compte n'existe pas.
_DUMMY = User()
_DUMMY.set_password("pas-un-vrai-mot-de-passe")


def _recent(key, now):
    return [t for t in _failures.get(key, []) if now - t < WINDOW]


def _throttled(keys):
    now = time.monotonic()
    with _lock:
        return any(len(_recent(k, now)) >= MAX_FAILURES for k in keys)


def _record_failure(keys):
    now = time.monotonic()
    with _lock:
        for k in keys:
            _failures[k] = _recent(k, now) + [now]


def _clear_failures(keys):
    with _lock:
        for k in keys:
            _failures.pop(k, None)

bp = Blueprint("auth", __name__)


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired()])
    password = PasswordField("Mot de passe", validators=[DataRequired()])
    remember = BooleanField("Rester connecté")


class PasswordForm(FlaskForm):
    current = PasswordField("Mot de passe actuel", validators=[DataRequired()])
    new = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirmation", validators=[EqualTo("new", "Les mots de passe ne correspondent pas.")])


@bp.before_app_request
def force_password_change():
    """Un compte créé avec un mot de passe provisoire doit le changer avant tout."""
    if (current_user.is_authenticated and current_user.must_change_password
            and request.endpoint not in ("auth.password", "auth.logout", "static")):
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
