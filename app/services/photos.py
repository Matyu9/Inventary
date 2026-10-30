import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIZE = 2000
Image.MAX_IMAGE_PIXELS = 50_000_000  # au-delà : refus (au lieu d'un simple avertissement)
THUMB_SIZE = 400


class InvalidImage(ValueError):
    pass


def save_photo(file_storage):
    """Enregistre une image (réduite, orientée, en JPEG) et sa miniature. Renvoie (fichier, miniature)."""
    try:
        image = Image.open(file_storage.stream)
        image.load()
    except Image.DecompressionBombError as exc:
        raise InvalidImage("Image trop grande.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("Le fichier n'est pas une image valide.") from exc

    image = ImageOps.exif_transpose(image).convert("RGB")
    folder = Path(current_app.config["UPLOAD_DIR"]) / "photos"
    folder.mkdir(parents=True, exist_ok=True)
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
