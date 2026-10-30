"""Recherche, filtres et champs personnalisés des objets."""
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_

from ..extensions import db, fold, fold_contains
from ..models import CustomField, CustomFieldValue, FieldType, Item, Location, Tag

FILTERS = ("q", "category", "tag", "location", "status", "condition")


def location_descendants(location_id):
    """Identifiants du lieu et de tous ses sous-lieux."""
    children = {}
    for loc_id, parent_id in db.session.execute(db.select(Location.id, Location.parent_id)):
        children.setdefault(parent_id, []).append(loc_id)
    result, stack = set(), [location_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def search_items(args):
    """Construit la requête de la liste d'inventaire à partir des paramètres GET."""
    query = db.select(Item)
    if q := (args.get("q") or "").strip():
        def match(column):
            return fold_contains(column, q)

        custom_match = db.select(CustomFieldValue.id).where(
            CustomFieldValue.item_id == Item.id, match(CustomFieldValue.value)).exists()
        query = query.where(or_(
            match(Item.code), match(Item.name), match(Item.brand), match(Item.model),
            match(Item.serial_number), match(Item.description), custom_match, Item.tags.any(match(Tag.name)),
        ))
    if category := _int(args.get("category")):
        query = query.where(Item.category_id == category)
    if tag := _int(args.get("tag")):
        query = query.where(Item.tags.any(Tag.id == tag))
    if location := _int(args.get("location")):
        query = query.where(Item.location_id.in_(location_descendants(location)))
    if status := args.get("status"):
        query = query.where(Item.status == status)
    if condition := args.get("condition"):
        query = query.where(Item.condition == condition)
    return query.order_by(Item.code)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_tags(raw):
    """« bois, Étude ,bois » → objets Tag (créés si besoin), sans doublon."""
    names, seen = [], set()
    for name in (raw or "").split(","):
        name = " ".join(name.split())[:50]
        if name and fold(name) not in seen:
            seen.add(fold(name))
            names.append(name)
    tags = []
    for name in names:
        tag = db.session.scalar(db.select(Tag).where(db.func.fold(Tag.name) == fold(name)))
        if tag is None:
            tag = Tag(name=name)
            db.session.add(tag)
        tags.append(tag)
    return tags


def fields_for_category(category_id):
    fields = db.session.scalars(db.select(CustomField).order_by(CustomField.position, CustomField.label)).all()
    return [f for f in fields if not f.categories or any(c.id == category_id for c in f.categories)]


def validate_custom_values(fields, form):
    """Lit les champs `cf_<id>` du formulaire. Renvoie (valeurs normalisées, erreurs)."""
    values, errors = {}, {}
    for field in fields:
        raw = (form.get(f"cf_{field.id}") or "").strip()
        if field.type == FieldType.BOOLEEN:
            values[field.id] = "1" if raw in ("1", "on", "true") else "0"
            continue
        if not raw:
            if field.required:
                errors[field.id] = "Ce champ est obligatoire."
            values[field.id] = None
            continue
        try:
            if field.type == FieldType.NOMBRE:
                raw = str(Decimal(raw.replace(",", ".")))
            elif field.type == FieldType.DATE:
                raw = date.fromisoformat(raw).isoformat()
            elif field.type == FieldType.LISTE and raw not in (field.options or []):
                raise ValueError
        except (InvalidOperation, ValueError):
            errors[field.id] = "Valeur invalide."
            continue
        values[field.id] = raw
    return values, errors


def apply_custom_values(item, values):
    """Remplace les valeurs de l'objet ; renvoie {libellé: [ancien, nouveau]} pour le journal."""
    existing = {v.field_id: v for v in item.custom_values}
    changes = {}
    for field_id, value in values.items():
        current = existing.get(field_id)
        old = current.value if current else None
        if old == value:
            continue
        label = db.session.get(CustomField, field_id).label
        changes[label] = [old, value]
        if value is None:
            item.custom_values.remove(current)
        elif current:
            current.value = value
        else:
            item.custom_values.append(CustomFieldValue(field_id=field_id, value=value))
    return changes


def custom_display(item):
    """[(champ, valeur affichable)] pour la fiche objet."""
    values = {v.field_id: v.value for v in item.custom_values}
    out = []
    for field in fields_for_category(item.category_id):
        value = values.get(field.id)
        if field.type == FieldType.BOOLEEN:
            value = "Oui" if value == "1" else "Non"
        elif field.type == FieldType.DATE and value:
            value = date.fromisoformat(value).strftime("%d/%m/%Y")
        if value not in (None, ""):
            out.append((field, value))
    return out
