"""Documents joints aux objets et aux kits, avec leurs propres droits (document.view / document.edit)."""
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, request, send_from_directory, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Document, Item, Kit, get_live
from ..permissions import require
from ..services.activity import log
from ..services.documents import InvalidDocument, delete_document_files, is_inline, save_document

bp = Blueprint("documents", __name__, url_prefix="/documents")

OWNERS = {"objets": Item, "kits": Kit}


def _owner_url(document_or_owner):
    owner = document_or_owner
    if isinstance(owner, Document):
        owner = owner.item or owner.kit
    if isinstance(owner, Kit):
        return url_for("kits.detail", kit_id=owner.id)
    return url_for("items.detail", item_id=owner.id)


def _log(action, message, owner):
    log(action, message, **({"kit": owner} if isinstance(owner, Kit) else {"item": owner}))


def get_document(document_id):
    """Document dont l'objet ou le kit n'est pas à la corbeille."""
    document = db.session.get(Document, document_id) or abort(404)
    owner = get_live(Item, document.item_id) if document.item_id else get_live(Kit, document.kit_id)
    return document if owner is not None else abort(404)


@bp.route("/<kind>/<int:owner_id>", methods=["POST"])
@require("document.edit")
def add(kind, owner_id):
    owner = get_live(OWNERS.get(kind) or abort(404), owner_id) or abort(404)
    files = [f for f in request.files.getlist("documents") if f and f.filename]
    if not files:
        flash("Aucun fichier envoyé.", "warning")
    doc_kind = request.form.get("kind") if request.form.get("kind") in Document.KINDS else "autre"
    title = (request.form.get("title") or "").strip()[:160]
    added = 0
    for f in files:
        try:
            filename, original, mime, size = save_document(f)
        except InvalidDocument as exc:
            flash(f"{f.filename} : {exc}", "error")
            continue
        owner.documents.append(Document(
            filename=filename, original_name=original, mime=mime, size=size, kind=doc_kind,
            title=(title if len(files) == 1 and title else original.rsplit(".", 1)[0])[:160],
            uploaded_by_id=current_user.id))
        added += 1
    if added:
        _log("document.add", f"{added} document{'s' if added > 1 else ''} joint{'s' if added > 1 else ''}", owner)
        db.session.commit()
        flash(f"{added} document{'s' if added > 1 else ''} ajouté{'s' if added > 1 else ''}.", "success")
    return redirect(_owner_url(owner) + "#documents")


@bp.route("/<int:document_id>/<path:name>")
@require("document.view")
def show(document_id, name):
    document = get_document(document_id)
    inline = is_inline(document)
    path = Path(current_app.config["UPLOAD_DIR"]) / document.filename
    response = send_from_directory(path.parent, path.name, mimetype=document.mime, as_attachment=not inline,
                                   download_name=document.original_name)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    if document.mime.startswith("image/"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return response


@bp.route("/<int:document_id>/supprimer", methods=["POST"])
@require("document.edit")
def delete(document_id):
    document = get_document(document_id)
    owner = document.item or document.kit
    delete_document_files(document)
    db.session.delete(document)
    _log("document.delete", f"Document supprimé : {document.title}", owner)
    db.session.commit()
    flash("Document supprimé.", "success")
    return redirect(_owner_url(owner) + "#documents")
