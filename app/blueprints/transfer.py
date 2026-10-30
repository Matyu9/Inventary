"""Export de l'inventaire et import depuis un tableur."""
import json
import time
import uuid
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session,
                   url_for)
from flask_login import current_user

from ..extensions import db
from ..models import Category
from ..permissions import require
from ..services import transfer as svc
from ..services.items import search_items

bp = Blueprint("transfer", __name__)


def _import_dir():
    folder = Path(current_app.config["DATA_DIR"]) / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cleanup(max_age=24 * 3600):
    """Supprime les imports commencés et jamais terminés."""
    limit = time.time() - max_age
    for path in _import_dir().glob("*.json"):
        if path.stat().st_mtime < limit:
            path.unlink(missing_ok=True)


@bp.route("/import-export")
@require("export")
def index():
    categories = db.session.scalars(db.select(Category).order_by(Category.name)).all()
    return render_template("transfer/index.html", categories=categories, fields=svc.FIELDS)


@bp.route("/export/inventaire.<fmt>")
@require("export")
def export(fmt):
    if fmt not in ("csv", "xlsx"):
        abort(404)
    rows = svc.export_rows(db.session.scalars(search_items(request.args)))
    if fmt == "csv":
        return Response(svc.to_csv(rows), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="inventaire.csv"'})
    return Response(svc.to_xlsx(rows), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="inventaire.xlsx"'})


@bp.route("/import", methods=["POST"])
@require("import")
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choisissez un fichier.", "warning")
        return redirect(url_for(".index"))
    data = file.read()
    try:
        header, rows = svc.read_table(data, file.filename)
    except svc.TransferError as exc:
        flash(str(exc), "error")
        return redirect(url_for(".index"))
    _cleanup()
    token = uuid.uuid4().hex
    (_import_dir() / f"{token}.json").write_text(json.dumps({"header": header, "rows": rows,
                                                             "filename": file.filename}), encoding="utf-8")
    session["import_token"] = token
    return redirect(url_for(".mapping", token=token))


def _load(token):
    if session.get("import_token") != token or not token.isalnum():
        abort(404)
    path = _import_dir() / f"{token}.json"
    if not path.exists():
        abort(404)
    return path, json.loads(path.read_text(encoding="utf-8"))


@bp.route("/import/<token>", methods=["GET", "POST"])
@require("import")
def mapping(token):
    path, data = _load(token)
    header, rows = data["header"], data["rows"]
    if request.method == "GET":
        mapping = svc.guess_mapping(header)
        options = {"create_missing": True, "update_existing": True, "default_category": None}
        result = None
    else:
        mapping = {}
        for field in svc.FIELDS:
            index = request.form.get(f"map_{field}", type=int)
            mapping[field] = index if index is not None and 0 <= index < len(header) else None
        default = db.session.get(Category, request.form.get("default_category", type=int) or 0)
        options = {"create_missing": bool(request.form.get("create_missing")),
                   "update_existing": bool(request.form.get("update_existing")),
                   "default_category": default.id if default else None}
        if mapping.get("name") is None and mapping.get("code") is None:
            flash("Associez au moins la colonne « Désignation ».", "error")
            result = None
        else:
            created, updated, errors = svc.run_import(
                rows, mapping, current_user, default_category=default, create_missing=options["create_missing"],
                update_existing=options["update_existing"], prefix=current_app.config["ITEM_CODE_PREFIX"])
            result = {"created": created, "updated": updated, "errors": errors}
            if request.form.get("confirm") and not errors:
                db.session.commit()
                path.unlink(missing_ok=True)
                session.pop("import_token", None)
                flash(f"Import terminé : {created} objet(s) créé(s), {updated} mis à jour.", "success")
                return redirect(url_for("items.index"))
            db.session.rollback()  # essai à blanc, ou erreurs à corriger
    categories = db.session.scalars(db.select(Category).order_by(Category.name)).all()
    return render_template("transfer/mapping.html", token=token, header=header, rows=rows, preview=rows[:5],
                           mapping=mapping, fields=svc.FIELDS, options=options, result=result,
                           categories=categories, filename=data["filename"])
