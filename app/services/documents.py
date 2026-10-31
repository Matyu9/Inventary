"""Documents joints (factures, notices…) : contrôle du type réel du fichier, enregistrement et suppression."""
import uuid
from pathlib import Path, PurePath

from flask import current_app

FOLDER = "documents"
ZIP = (b"PK\x03\x04",)
# Extension → (type MIME servi, débuts de fichier acceptés). None : texte (sans octet nul).
FORMATS = {
    "pdf": ("application/pdf", (b"%PDF-",)),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "webp": ("image/webp", (b"RIFF",)),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ZIP),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ZIP),
    "odt": ("application/vnd.oasis.opendocument.text", ZIP),
    "ods": ("application/vnd.oasis.opendocument.spreadsheet", ZIP),
    "txt": ("text/plain; charset=utf-8", None),
    "csv": ("text/csv; charset=utf-8", None),
}
# Affichés dans le navigateur ; tout le reste est proposé au téléchargement.
INLINE = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
ACCEPT = ",".join(f".{ext}" for ext in FORMATS)


class InvalidDocument(ValueError):
    pass


def _looks_like(data, ext):
    _mime, signatures = FORMATS[ext]
    if signatures is None:  # texte : tout octet est valide en Latin-1, seul l'octet nul trahit un binaire
        return b"\x00" not in data
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return data.startswith(signatures)


def clean_name(filename):
    """Nom d'origine sans chemin ni caractères de contrôle, pour l'affichage et le téléchargement."""
    name = PurePath((filename or "").replace("\\", "/")).name
    name = "".join(c for c in name if c.isprintable()).strip()
    return name[-200:] or "document"


def save_document(file_storage):
    """Enregistre le fichier s'il est bien ce que son extension annonce. Renvoie (chemin, nom, type, taille)."""
    original = clean_name(file_storage.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in FORMATS:
        raise InvalidDocument("Format non accepté (PDF, image, Word, Excel, OpenDocument ou texte).")
    data = file_storage.stream.read()
    if not data:
        raise InvalidDocument("Le fichier est vide.")
    if not _looks_like(data[:4096], ext):
        raise InvalidDocument(f"Le contenu ne correspond pas à un fichier .{ext}.")
    folder = Path(current_app.config["UPLOAD_DIR"]) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (folder / name).write_bytes(data)
    return f"{FOLDER}/{name}", original, FORMATS[ext][0], len(data)


def delete_document_files(document):
    (Path(current_app.config["UPLOAD_DIR"]) / document.filename).unlink(missing_ok=True)


def is_inline(document):
    return document.mime in INLINE


def human_size(n):
    if n < 1024:
        return f"{n} o"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} Ko"
    return f"{n / 1024 ** 2:.1f} Mo".replace(".", ",")
