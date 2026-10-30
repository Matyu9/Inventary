import re

import qrcode
import qrcode.image.svg
from flask import current_app
from markupsafe import Markup

from ..extensions import db
from ..models import Item, Kit

TOKEN_IN_URL = re.compile(r"/q/([0-9a-f]{32})(?:[/?#]|$)")


def qr_url(token):
    return f"{current_app.config['BASE_URL']}/q/{token}"


def qr_svg(data):
    """QR code en SVG vectoriel (net à l'impression), sans dimensions fixes."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=1,
                       image_factory=qrcode.image.svg.SvgPathImage)
    qr.add_data(data)
    svg = qr.make_image().to_string(encoding="unicode")
    svg = re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2)
    return Markup(svg)


def find_by_token(token):
    item = db.session.scalar(db.select(Item).filter_by(qr_token=token))
    if item:
        return item
    return db.session.scalar(db.select(Kit).filter_by(qr_token=token))


def resolve_scan(text):
    """Texte lu (URL de QR, jeton brut ou code d'inventaire) → Item, Kit ou None."""
    text = (text or "").strip()
    if not text:
        return None
    if match := TOKEN_IN_URL.search(text):
        return find_by_token(match.group(1))
    if re.fullmatch(r"[0-9a-f]{32}", text):
        return find_by_token(text)
    return db.session.scalar(db.select(Item).where(db.func.upper(Item.code) == text.upper()))
