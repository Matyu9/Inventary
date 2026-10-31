import os
from pathlib import Path


DEFAULT_SECRET = "dev-secret-change-me"
# Valeurs connues de tous (défaut du code, modèle .env.example) : refusées en production.
UNSAFE_SECRETS = (DEFAULT_SECRET, "change-moi")


def _csv(value):
    return [v.strip() for v in value.split(",") if v.strip()]


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or DEFAULT_SECRET
    DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'inventaire.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_DIR = DATA_DIR / "uploads"
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    # Derrière un reverse proxy (Caddy, Traefik, nginx) : faire confiance à X-Forwarded-*.
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"
    SESSION_COOKIE_SAMESITE = "Lax"
    # Jeton CSRF valable toute la session (et non 1 h) : un formulaire resté ouvert sur un téléphone, comme celui
    # d'un QR code, ne doit pas échouer à l'envoi. Il reste lié à la session et protégé par SameSite.
    WTF_CSRF_TIME_LIMIT = None
    SESSION_COOKIE_SECURE = BASE_URL.startswith("https://")
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    ASSO_NAME = os.environ.get("ASSO_NAME", "Mon association")
    # Normalisé comme les codes saisis (majuscules) ; « INV- » et « inv » donnent « INV ».
    ITEM_CODE_PREFIX = os.environ.get("ITEM_CODE_PREFIX", "").strip().strip("-").upper() or "INV"

    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")
    SMTP_TLS = os.environ.get("SMTP_TLS", "true").lower() == "true"

    # Connexion OIDC (facultative) : active dès que l'URL de découverte et le client sont renseignés.
    OIDC_DISCOVERY_URL = os.environ.get("OIDC_DISCOVERY_URL", "")
    OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
    OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
    OIDC_PROVIDER_NAME = os.environ.get("OIDC_PROVIDER_NAME", "SSO")
    OIDC_SCOPES = os.environ.get("OIDC_SCOPES", "openid email profile")
    OIDC_GROUPS_CLAIM = os.environ.get("OIDC_GROUPS_CLAIM", "groups")
    OIDC_ADMIN_GROUPS = _csv(os.environ.get("OIDC_ADMIN_GROUPS", ""))
    OIDC_PROF_GROUPS = _csv(os.environ.get("OIDC_PROF_GROUPS", ""))
    # Lier un compte existant par son email seulement si le fournisseur l'a vérifié (email_verified).
    OIDC_REQUIRE_VERIFIED_EMAIL = os.environ.get("OIDC_REQUIRE_VERIFIED_EMAIL", "1") == "1"
    # Masque le formulaire email / mot de passe (la connexion passe uniquement par l'OIDC).
    OIDC_ONLY = os.environ.get("OIDC_ONLY", "0") == "1"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
