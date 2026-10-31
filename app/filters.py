import re
from datetime import date, datetime
from decimal import Decimal

from markupsafe import Markup, escape

from .services.documents import human_size

MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def date_fr(value, with_time=False):
    if not value:
        return ""
    if isinstance(value, datetime):
        s = f"{value.day} {MONTHS[value.month - 1]} {value.year}"
        return f"{s} à {value:%H:%M}" if with_time else s
    if isinstance(value, date):
        return f"{value.day} {MONTHS[value.month - 1]} {value.year}"
    return value


def euros(value):
    if value is None or value == "":
        return ""
    value = Decimal(value)
    whole, cents = f"{value:.2f}".split(".")
    whole = f"{int(whole):,}".replace(",", " ")
    return f"{whole},{cents} €"


EMAIL = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")
PHONE = re.compile(r"^\+?[\d\s.()-]{6,20}$")


def contact_link(value):
    """Contact laissé librement : lien mailto: ou tel: s'il en a la forme, sinon simple texte (échappé)."""
    value = (value or "").strip()
    if EMAIL.match(value):
        return Markup('<a href="mailto:{0}">{0}</a>').format(value)
    if PHONE.match(value) and sum(c.isdigit() for c in value) >= 6:
        number = re.sub(r"[^\d+]", "", value)
        return Markup('<a href="tel:{0}">{1}</a>').format(number, value)
    return escape(value)


def register_filters(app):
    app.add_template_filter(date_fr, "date_fr")
    app.add_template_filter(lambda v: date_fr(v, with_time=True), "datetime_fr")
    app.add_template_filter(euros, "euros")
    app.add_template_filter(human_size, "filesize")
    app.add_template_filter(contact_link, "contact_link")
