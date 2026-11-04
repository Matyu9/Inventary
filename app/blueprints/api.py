"""API en lecture seule (/api/v1), pour les tableurs, scripts, intégrations et le serveur MCP.

Authentification par jeton personnel (« Mon compte » → Accès à l'API), dans l'en-tête
`Authorization: Bearer ohn_…`. Le cookie de session n'est pas accepté : une page web ne peut pas
interroger l'API à la place de la personne connectée. Chaque appel applique les droits du propriétaire
du jeton, comme dans l'appli. Uniquement des GET : rien ne peut être modifié par cette voie.
Documentation : docs/api.md.
"""
from datetime import date, datetime, time, timedelta

from flask import Blueprint, abort, g, jsonify, request, url_for
from sqlalchemy import or_
from werkzeug.datastructures import WWWAuthenticate
from werkzeug.exceptions import TooManyRequests, Unauthorized

from ..extensions import db, fold, fold_contains
from ..models import (Category, ConsumptionMode, Item, ItemStatus, Kit, Loan, LoanLine, LoanStatus, LoanType,
                      Location, Tag, Tracking, User, get_live, item_tags)
from ..permissions import can, permissions_of
from ..services import api_tokens
from ..services import items as item_svc
from ..services.loans import UNAVAILABLE_STATUSES, blocked_quantities
from ..services.throttle import Limiter
from .kits import kit_status
from .loans import VIEWS, _in_view

bp = Blueprint("api", __name__, url_prefix="/api/v1")

# 300 appels par minute et par jeton : large pour un script, bloque une boucle emballée.
_rate = Limiter(limit=300, window=60)
PER_PAGE, MAX_PER_PAGE = 50, 100
OPEN_LOANS = (LoanStatus.DEMANDE, LoanStatus.VALIDE, LoanStatus.EN_COURS)


@bp.before_request
def authenticate():
    scheme, _, raw = (request.headers.get("Authorization") or "").partition(" ")
    token = api_tokens.authenticate(raw.strip()) if scheme.lower() == "bearer" else None
    if token is None:
        raise Unauthorized("Jeton d'accès absent, invalide ou expiré.",
                           www_authenticate=WWWAuthenticate("bearer", {"realm": "api"}))
    if not can(token.user, "api.use"):
        abort(403, "Votre rôle ne permet plus d'utiliser l'API.")
    if _rate.blocked([token.id]):
        raise TooManyRequests("Trop d'appels : patientez une minute.", retry_after=60)
    _rate.hit([token.id])
    g.api_token, g.api_user = token, token.user


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"  # données personnelles : pas de cache intermédiaire
    return response


def _need(action, obj=None):
    if not can(g.api_user, action, obj):
        abort(403, "Votre rôle ne donne pas accès à ces données.")


# ---------------------------------------------------------------------------
# Paramètres et pagination
# ---------------------------------------------------------------------------

def _flag(name):
    return (request.args.get(name) or "").lower() in ("1", "true", "oui", "yes")


def _int_arg(name):
    value = request.args.get(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        abort(400, f"Le paramètre « {name} » doit être un nombre entier.")


def _when(name, default=None, end_of_day=False):
    """Date (AAAA-MM-JJ) ou date et heure ISO 8601. Une date seule de fin couvre toute la journée."""
    value = (request.args.get(name) or "").strip()
    if not value:
        return default
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime.combine(day + timedelta(days=1), time()) if end_of_day else datetime.combine(day, time())
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        abort(400, f"Le paramètre « {name} » doit être une date AAAA-MM-JJ ou une date et heure ISO 8601.")


def _page(query, serialize):
    page = max(_int_arg("page") or 1, 1)
    per_page = min(max(_int_arg("per_page") or PER_PAGE, 1), MAX_PER_PAGE)
    result = db.paginate(query, page=page, per_page=per_page, error_out=False, count=True)
    return jsonify({"data": [serialize(obj) for obj in result.items],
                    "meta": {"page": page, "per_page": per_page, "total": result.total, "pages": result.pages}})


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------

def _iso(value):
    return value.isoformat() if value is not None else None


def _money(value):
    return float(value) if value is not None else None


def _person(user):
    return {"id": user.id, "name": user.full_name} if user is not None else None


def _location(location):
    return {"id": location.id, "name": location.name, "path": location.path} if location is not None else None


def _item_ref(item):
    return {"id": item.id, "code": item.code, "name": item.name}


def _item(item):
    data = {
        "id": item.id, "code": item.code, "name": item.name,
        "category": {"id": item.category.id, "name": item.category.name, "type": item.category.type},
        "brand": item.brand, "model": item.model, "status": item.status, "condition": item.condition,
        "location": _location(item.location),
        "kit": {"id": item.kit.id, "name": item.kit.name} if item.kit else None,
        "tags": [t.name for t in item.tags],
        "tracking": item.tracking,
        "url": url_for("items.detail", item_id=item.id, _external=True),
    }
    if item.is_quantity:
        data.update(quantity=item.quantity, unit=item.unit, low_stock_threshold=item.low_stock_threshold,
                    low_stock=item.is_low_stock, consumption_mode=item.consumption_mode)
    return data


def _item_detail(item):
    data = _item(item)
    data.update(
        serial_number=item.serial_number, description=item.description,
        purchase_date=_iso(item.purchase_date), purchase_price=_money(item.purchase_price),
        estimated_value=_money(item.estimated_value),
        custom_fields=[{"label": field.label, "value": value} for field, value in item_svc.custom_display(item)],
        maintenances=[{"date": _iso(m.date), "kind": m.kind, "provider": m.provider, "cost": _money(m.cost),
                       "next_due": _iso(m.next_due)} for m in item.maintenances[:10]],
        photos=len(item.photos), documents=len(item.documents),
        created_at=_iso(item.created_at), updated_at=_iso(item.updated_at),
        loans=[_loan_ref(loan) for loan in _open_loans(
            LoanLine.item_id == item.id, LoanLine.kit_id == item.kit_id if item.kit_id else None)],
    )
    return data


def _kit(kit):
    return {"id": kit.id, "name": kit.name, "description": kit.description, "status": kit_status(kit),
            "location": _location(kit.location), "item_count": len(kit.items),
            "url": url_for("kits.detail", kit_id=kit.id, _external=True)}


def _loan_ref(loan):
    return {"id": loan.id, "type": loan.type, "status": loan.status, "status_label": loan.status_label,
            "start": _iso(loan.start), "end": _iso(loan.end), "borrower": _person(loan.borrower)}


def _loan(loan):
    lines = []
    for line in loan.lines:
        entry = {"quantity": line.quantity, "returned_at": _iso(line.returned_at),
                 "condition_out": line.condition_out, "condition_in": line.condition_in}
        if line.kit is not None:
            entry["kit"] = {"id": line.kit.id, "name": line.kit.name, "items": [_item_ref(i) for i in line.kit.items]}
        elif line.item is not None:
            entry["item"] = _item_ref(line.item)
        else:
            entry["deleted"] = True  # objet ou kit passé à la corbeille depuis
        lines.append(entry)
    return {
        "id": loan.id, "type": loan.type, "type_label": loan.type_label, "title": loan.title,
        "status": loan.status, "status_label": loan.status_label, "overdue": loan.is_overdue,
        "awaiting_guarantor": loan.is_awaiting_guarantor,
        "start": _iso(loan.start), "end": _iso(loan.end),
        "checked_out_at": _iso(loan.checked_out_at), "returned_at": _iso(loan.returned_at),
        "borrower": _person(loan.borrower), "guarantor": _person(loan.guarantor),
        "deposit_amount": _money(loan.deposit_amount), "deposit_returned": loan.deposit_returned,
        "comment": loan.comment, "refusal_reason": loan.refusal_reason, "lines": lines,
        "url": url_for("loans.detail", loan_id=loan.id, _external=True),
    }


def _open_loans(*line_conditions):
    """Prêts demandés, validés ou en cours dont une ligne non rendue remplit une des conditions, et que le
    propriétaire du jeton a le droit de voir."""
    conditions = [c for c in line_conditions if c is not None]
    loans = db.session.scalars(
        db.select(Loan).join(LoanLine).where(Loan.status.in_(OPEN_LOANS), LoanLine.returned_at.is_(None),
                                             or_(*conditions)).distinct().order_by(Loan.start)).all()
    return [loan for loan in loans if can(g.api_user, "loan.view", loan)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/me")
def me():
    user, token = g.api_user, g.api_token
    return jsonify({"id": user.id, "name": user.full_name, "email": user.email, "role": user.role,
                    "role_label": user.role_label, "permissions": sorted(permissions_of(user.role)),
                    "token": {"name": token.name, "expires_at": _iso(token.expires_at)}})


@bp.route("/items")
def items():
    _need("inventory.view")
    query = item_svc.search_items(request.args)
    if (kit := _int_arg("kit")) is not None:
        query = query.where(Item.kit_id == kit)
    if request.args.get("tracking") in (Tracking.INDIVIDUEL, Tracking.QUANTITE):
        query = query.where(Item.tracking == request.args["tracking"])
    if _flag("low_stock"):
        query = query.where(*_low_stock())
    return _page(query, _item)


def _low_stock():
    return (Item.tracking == Tracking.QUANTITE, Item.low_stock_threshold.is_not(None),
            Item.quantity <= Item.low_stock_threshold)


@bp.route("/items/<ref>")
def item(ref):
    """Un objet, par son identifiant (42) ou par son code (OHN-0042, sans tenir compte des majuscules)."""
    _need("inventory.view")
    found = get_live(Item, int(ref)) if ref.isdigit() else db.session.scalar(
        db.select(Item).where(db.func.upper(Item.code) == ref.upper()))
    if found is None:
        abort(404, "Objet introuvable.")
    return jsonify(_item_detail(found))


@bp.route("/kits")
def kits():
    _need("inventory.view")
    query = db.select(Kit).order_by(Kit.name)
    if q := (request.args.get("q") or "").strip():
        query = query.where(fold_contains(Kit.name, q))
    return _page(query, _kit)


@bp.route("/kits/<int:kit_id>")
def kit(kit_id):
    _need("inventory.view")
    found = get_live(Kit, kit_id) or abort(404, "Kit introuvable.")
    data = _kit(found)
    data["items"] = [_item(i) for i in found.items]
    data["loans"] = [_loan_ref(loan) for loan in _open_loans(LoanLine.kit_id == found.id)]
    return jsonify(data)


@bp.route("/consumables")
def consumables():
    _need("inventory.view")
    query = db.select(Item).where(Item.tracking == Tracking.QUANTITE).order_by(Item.name)
    if _flag("low_stock"):
        query = query.where(*_low_stock())
    return _page(query, _item)


@bp.route("/locations")
def locations():
    _need("inventory.view")
    counts = dict(db.session.execute(db.select(Item.location_id, db.func.count(Item.id)).group_by(Item.location_id)).all())
    locations = db.session.scalars(db.select(Location)).all()
    data = sorted(({"id": loc.id, "name": loc.name, "path": loc.path, "parent_id": loc.parent_id,
                    "description": loc.description, "item_count": counts.get(loc.id, 0)} for loc in locations),
                  key=lambda d: d["path"].lower())
    return jsonify({"data": data})


@bp.route("/categories")
def categories():
    _need("inventory.view")
    counts = dict(db.session.execute(db.select(Item.category_id, db.func.count(Item.id)).group_by(Item.category_id)).all())
    return jsonify({"data": [{"id": c.id, "name": c.name, "type": c.type, "item_count": counts.get(c.id, 0)}
                             for c in db.session.scalars(db.select(Category).order_by(Category.name))]})


@bp.route("/tags")
def tags():
    _need("inventory.view")
    rows = db.session.execute(db.select(Tag, db.func.count(item_tags.c.item_id)).outerjoin(item_tags)
                              .group_by(Tag.id).order_by(Tag.name))
    return jsonify({"data": [{"id": tag.id, "name": tag.name, "item_count": count} for tag, count in rows]})


@bp.route("/loans")
def loans():
    """Tous les prêts avec le droit « Voir tous les prêts », sinon ceux dont on est emprunteur ou garant."""
    user = g.api_user
    if can(user, "loan.list_all"):
        query = db.select(Loan)
        if (borrower := _int_arg("borrower")) is not None:
            query = query.where(Loan.borrower_id == borrower)
    elif can(user, "myloans.view"):
        query = db.select(Loan).where(or_(Loan.borrower_id == user.id, Loan.guarantor_id == user.id))
    else:
        _need("loan.list_all")
    if _flag("mine"):
        query = query.where(or_(Loan.borrower_id == user.id, Loan.guarantor_id == user.id))
    view = request.args.get("view") or "actifs"
    if view not in VIEWS and view != "tous":
        abort(400, "Vue inconnue : " + ", ".join([*VIEWS, "tous"]) + ".")
    if view != "tous":
        query = _in_view(query, view)
    if (loan_type := request.args.get("type")) is not None:
        if loan_type not in LoanType.ALL:
            abort(400, "Type de prêt inconnu : " + ", ".join(LoanType.ALL) + ".")
        query = query.where(Loan.type == loan_type)
    if q := (request.args.get("q") or "").strip():
        people = db.select(User.id).where(or_(fold_contains(User.first_name, q), fold_contains(User.last_name, q)))
        query = query.where(or_(Loan.borrower_id.in_(people), Loan.guarantor_id.in_(people),
                                fold_contains(Loan.title, q)))
    order = Loan.start.desc() if view in ("historique", "tous") else Loan.start
    return _page(query.order_by(order, Loan.id), _loan)


@bp.route("/loans/<int:loan_id>")
def loan(loan_id):
    found = db.session.get(Loan, loan_id) or abort(404, "Prêt introuvable.")
    _need("loan.view", found)
    return jsonify(_loan(found))


@bp.route("/availability")
def availability():
    """Matériel libre sur une période (par défaut : à partir de maintenant, pour 24 h).

    Filtres des objets comme /items (q, category, tag, location). Les objets d'un kit ne se prêtent qu'avec le
    kit : ils sont comptés dans le kit, pas listés seuls.
    """
    _need("inventory.view")
    start = _when("start", default=datetime.now())
    end = _when("end", default=start + timedelta(days=1), end_of_day=True)
    if end <= start:
        abort(400, "La fin de la période doit être après son début.")
    used = blocked_quantities(start, end)

    def free(item):
        if item.status in UNAVAILABLE_STATUSES:
            return 0
        if item.is_quantity and item.consumption_mode == ConsumptionMode.CONSOMME:
            return item.quantity
        return max((item.quantity if item.is_quantity else 1) - used.get(item.id, 0), 0)

    query = item_svc.search_items(request.args).where(Item.kit_id.is_(None), Item.status != ItemStatus.REFORME)
    items = []
    for item in db.session.scalars(query):
        if (count := free(item)) > 0:
            data = _item(item)
            data["available"] = count
            items.append(data)
    kits = [_kit(kit) for kit in db.session.scalars(db.select(Kit).order_by(Kit.name))
            if kit.items and all(free(i) > 0 for i in kit.items)
            and (not (q := (request.args.get("q") or "").strip()) or fold(q) in fold(kit.name))]
    return jsonify({"start": _iso(start), "end": _iso(end), "items": items, "kits": kits})


@bp.route("/stats")
def stats():
    """Chiffres clés : objets par statut, stocks bas et, avec le droit de voir tous les prêts, les prêts."""
    _need("inventory.view")
    by_status = dict(db.session.execute(db.select(Item.status, db.func.count(Item.id)).group_by(Item.status)).all())
    data = {
        "items": {"total": sum(by_status.values()), "by_status": {s: by_status.get(s, 0) for s in ItemStatus.ALL}},
        "kits": db.session.scalar(db.select(db.func.count(Kit.id))),
        "low_stock": db.session.scalar(db.select(db.func.count(Item.id)).where(*_low_stock())),
    }
    if can(g.api_user, "loan.list_all"):
        def count(view):
            return db.session.scalar(_in_view(db.select(db.func.count(Loan.id)), view))
        data["loans"] = {"to_validate": count("demande"), "awaiting_guarantor": count("garant"),
                         "validated": count("valide"), "in_progress": count("en_cours"), "overdue": count("retard")}
    return jsonify(data)
