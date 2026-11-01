"""Collecte des rapports CSP : publique, sans CSRF, limitée en débit et en taille, journalisée sans rien stocker."""
import json

import pytest

from app import create_app
from app.blueprints import csp
from app.config import TestConfig
from app.extensions import db
from app.models import Role

from .conftest import login, make_user

VIOLATION = {"csp-report": {"document-uri": "https://inventaire.test/prets/1/verification",
                            "violated-directive": "script-src-attr", "effective-directive": "script-src-attr",
                            "blocked-uri": "inline", "source-file": "https://inventaire.test/prets/1/verification",
                            "line-number": 12, "disposition": "report", "original-policy": "default-src 'self'"}}


def post(client, payload, mimetype="application/csp-report", **kw):
    data = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    return client.post("/csp-report", data=data, content_type=mimetype, **kw)


def csp_logs(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Violation CSP")]


def test_report_uri_logged(client, caplog):
    with caplog.at_level("WARNING"):
        r = post(client, VIOLATION)
    assert r.status_code == 204 and r.data == b""
    [line] = csp_logs(caplog)
    assert "directive=script-src-attr" in line and "bloqué=inline" in line
    assert "page=https://inventaire.test/prets/1/verification" in line and "ligne=12" in line
    assert "default-src" not in line  # pas le corps brut (original-policy)


def test_report_to_batch_logged(client, caplog):
    body = {"effectiveDirective": "font-src", "blockedURL": "data", "documentURL": "https://inventaire.test/calendrier",
            "disposition": "enforce"}
    batch = [{"type": "csp-violation", "url": "https://inventaire.test/calendrier", "body": body},
             {"type": "csp-violation", "body": dict(body, effectiveDirective="style-src-elem")},
             {"type": "deprecation", "body": {"message": "ignoré"}}]
    with caplog.at_level("WARNING"):
        assert post(client, batch, "application/reports+json").status_code == 204
    lines = csp_logs(caplog)
    assert len(lines) == 2 and "directive=font-src" in lines[0] and "mode=enforce" in lines[0]
    assert "directive=style-src-elem" in lines[1] and not any("ignoré" in line for line in lines)


def test_batch_capped(client, caplog):
    batch = [{"type": "csp-violation", "body": {"effectiveDirective": f"img-src-{i}"}} for i in range(50)]
    with caplog.at_level("WARNING"):
        assert post(client, batch, "application/reports+json").status_code == 204
    assert len(csp_logs(caplog)) == csp.MAX_REPORTS


def test_fields_truncated_and_kept_on_one_line(client, caplog):
    payload = {"csp-report": {"effective-directive": "img-src", "blocked-uri": "https://x.test/" + "a" * 5000,
                              "document-uri": "https://x.test/\nViolation CSP : directive=faux\r\x1b[31m"}}
    with caplog.at_level("WARNING"):
        assert post(client, payload).status_code == 204
    [line] = csp_logs(caplog)
    assert "\n" not in line and "\r" not in line and "\x1b" not in line
    assert "a" * csp.MAX_FIELD not in line and "a" * (csp.MAX_FIELD - 20) in line


def test_too_large_rejected(client, caplog):
    huge = {"csp-report": {"effective-directive": "img-src", "blocked-uri": "x" * (csp.MAX_BODY + 1)}}
    with caplog.at_level("WARNING"):
        assert post(client, huge).status_code == 413
    assert csp_logs(caplog) == []


@pytest.mark.parametrize("mimetype,data,status", [("text/plain", json.dumps(VIOLATION), 415),
                                                  ("application/x-www-form-urlencoded", "a=b", 415),
                                                  ("application/csp-report", "{pas du json", 400)])
def test_bad_requests(client, mimetype, data, status):
    assert post(client, data, mimetype).status_code == status


@pytest.mark.parametrize("payload", [[], {}, {"csp-report": "texte"}, [1, "a", None], 42])
def test_unexpected_shapes_ignored(client, caplog, payload):
    with caplog.at_level("WARNING"):
        assert post(client, payload).status_code == 204
        assert post(client, payload, "application/reports+json").status_code == 204
    assert csp_logs(caplog) == []


def test_get_not_allowed(client):
    assert client.get("/csp-report").status_code == 405


def test_rate_limited_per_ip(client, caplog):
    other = {"REMOTE_ADDR": "10.0.0.2"}
    with caplog.at_level("WARNING"):
        for _ in range(csp._by_ip.limit):
            assert post(client, VIOLATION).status_code == 204
        assert post(client, VIOLATION).status_code == 429
        assert post(client, VIOLATION, environ_base=other).status_code == 204  # une autre adresse n'est pas gênée
    assert len(csp_logs(caplog)) == csp._by_ip.limit + 1


def test_csrf_exempt_without_session(tmp_path):
    class Csrf(TestConfig):
        WTF_CSRF_ENABLED = True
        DATA_DIR = tmp_path
        UPLOAD_DIR = tmp_path / "u"

    app = create_app(Csrf)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        assert client.post("/deconnexion").status_code == 400  # la protection CSRF est bien active ailleurs
        assert post(client, VIOLATION).status_code == 204
        db.session.remove()  # comme la fixture `app` : la page 400 a lu les réglages d'apparence
        db.drop_all()


def test_not_redirected_for_pending_password_change(client, app):
    user = make_user(Role.EMPRUNTEUR)
    user.must_change_password = True
    db.session.commit()
    login(client, user)
    assert post(client, VIOLATION).status_code == 204


@pytest.mark.parametrize("report_only", [True, False])
def test_policy_points_to_endpoint(client, app, users, report_only):
    app.config["CSP_REPORT_ONLY"] = report_only
    r = client.get("/connexion")
    policy = r.headers["Content-Security-Policy-Report-Only" if report_only else "Content-Security-Policy"]
    assert "report-uri /csp-report" in policy and "report-to csp-endpoint" in policy
    assert r.headers["Reporting-Endpoints"] == 'csp-endpoint="/csp-report"'
