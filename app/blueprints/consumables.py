from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Item, StockMovement, Tracking
from ..permissions import require
from ..services import stock

bp = Blueprint("consumables", __name__)


@bp.route("/consommables")
@require("inventory.view")
def index():
    items = db.session.scalars(db.select(Item).where(Item.tracking == Tracking.QUANTITE).order_by(Item.name)).all()
    items.sort(key=lambda i: (not i.is_low_stock, i.name.lower()))
    movements = db.session.scalars(db.select(StockMovement).order_by(StockMovement.created_at.desc(),
                                                                      StockMovement.id.desc()).limit(30)).all()
    return render_template("consumables/index.html", items=items, movements=movements, reasons=stock.REASONS)


@bp.route("/objets/<int:item_id>/stock", methods=["POST"])
@require("stock.move")
def move(item_id):
    item = db.session.get(Item, item_id) or abort(404)
    try:
        delta = stock.move(item, request.form.get("reason"), request.form.get("amount", type=int), current_user,
                           request.form.get("comment"))
    except stock.StockError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash(f"Stock mis à jour ({delta:+d}).", "success")
    return redirect(request.form.get("next") == "list" and url_for(".index")
                    or url_for("items.detail", item_id=item.id) + "#stock")


@bp.app_template_global()
def stock_movements(item, limit=20):
    return db.session.scalars(db.select(StockMovement).where(StockMovement.item_id == item.id)
                              .order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(limit)).all()


@bp.app_template_global()
def stock_reasons():
    return stock.REASONS
