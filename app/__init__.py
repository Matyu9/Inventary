import os

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import UNSAFE_SECRETS, Config
from .errors import register_error_handlers
from .extensions import csrf, db, login_manager, migrate
from .services.oidc import init_oidc


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    if app.config["SECRET_KEY"] in ("", *UNSAFE_SECRETS) and not (app.debug or app.testing or os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")):
        raise RuntimeError("SECRET_KEY n'est pas définie (ou vaut encore la valeur d'exemple) : "
                           "renseignez-la dans le fichier .env avant de démarrer.")
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["UPLOAD_DIR"].mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    init_oidc(app)

    from . import models  # noqa: F401  (enregistre les modèles pour Alembic)
    from .blueprints.audits import bp as audits_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.calendar import bp as calendar_bp
    from .blueprints.consumables import bp as consumables_bp
    from .blueprints.dashboard import bp as dashboard_bp
    from .blueprints.items import bp as items_bp
    from .blueprints.kits import bp as kits_bp
    from .blueprints.loans import bp as loans_bp
    from .blueprints.locations import bp as locations_bp
    from .blueprints.maintenance import bp as maintenance_bp
    from .blueprints.referentiel import bp as referentiel_bp
    from .blueprints.reports import bp as reports_bp
    from .blueprints.roles import bp as roles_bp
    from .blueprints.scan import bp as scan_bp
    from .blueprints.settings import bp as settings_bp
    from .blueprints.transfer import bp as transfer_bp
    from .blueprints.users import bp as users_bp
    from .cli import register_cli
    from .filters import register_filters
    from .permissions import can

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(items_bp)
    app.register_blueprint(locations_bp)
    app.register_blueprint(referentiel_bp)
    app.register_blueprint(scan_bp)
    app.register_blueprint(loans_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(roles_bp)
    app.register_blueprint(kits_bp)
    app.register_blueprint(consumables_bp)
    app.register_blueprint(maintenance_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(audits_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(transfer_bp)
    register_cli(app)
    register_filters(app)

    @app.context_processor
    def inject_globals():
        return {"can": can, "asso_name": app.config["ASSO_NAME"], "code_prefix": app.config["ITEM_CODE_PREFIX"]}

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        if request.endpoint in ("auth.password", "users.create", "users.edit", "users.reset_password",
                                "auth.oidc_callback"):
            response.headers["Cache-Control"] = "no-store"  # mots de passe affichés ou saisis
        return response

    register_error_handlers(app)

    return app
