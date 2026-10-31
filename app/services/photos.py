import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIZE = 2000
Image.MAX_IMAGE_PIXELS = 50_000_000  # au-delà : refus (au lieu d'un simple avertissement)
THUMB_SIZE = 400


class InvalidImage(ValueError):
    pass


def _open(file_storage):
    """Image envoyée, orientée d'après l'EXIF et en RGB (les métadonnées, dont la position GPS, sont perdues)."""
    try:
        image = Image.open(file_storage.stream)
        image.load()
    except Image.DecompressionBombError as exc:
        raise InvalidImage("Image trop grande.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("Le fichier n'est pas une image valide.") from exc
    return ImageOps.exif_transpose(image).convert("RGB")


def _folder(name):
    folder = Path(current_app.config["UPLOAD_DIR"]) / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_photo(file_storage):
    """Enregistre une image (réduite, orientée, en JPEG) et sa miniature. Renvoie (fichier, miniature)."""
    image = _open(file_storage)
    folder = _folder("photos")
    name = uuid.uuid4().hex

    full = image.copy()
    full.thumbnail((MAX_SIZE, MAX_SIZE))
    full.save(folder / f"{name}.jpg", "JPEG", quality=85)
    image.thumbnail((THUMB_SIZE, THUMB_SIZE))
    image.save(folder / f"{name}_t.jpg", "JPEG", quality=80)
    return f"photos/{name}.jpg", f"photos/{name}_t.jpg"


def delete_photo_files(photo):
    root = Path(current_app.config["UPLOAD_DIR"])
    for rel in (photo.filename, photo.thumbnail):
        (root / rel).unlink(missing_ok=True)


AVATAR_SIZE = 256


def save_avatar(file_storage):
    """Photo de profil : recadrée au centre en carré de 256 px, en JPEG. Renvoie le chemin relatif."""
    image = ImageOps.fit(_open(file_storage), (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    name = f"{uuid.uuid4().hex}.jpg"  # nom aléatoire : change à chaque photo, donc jamais servi périmé
    image.save(_folder("avatars") / name, "JPEG", quality=85)
    return f"avatars/{name}"


def delete_avatar_file(relative):
    if relative:
        (Path(current_app.config["UPLOAD_DIR"]) / relative).unlink(missing_ok=True)
