"""Corbeille : restaurer ou effacer pour de bon les objets et kits supprimés."""
from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Item, Kit
from ..permissions import require
from ..services import trash

bp = Blueprint("trash", __name__, url_prefix="/corbeille")

MODELS = {"objets": Item, "kits": Kit}


def _get(kind, obj_id):
    model = MODELS.get(kind) or abort(404)
    return trash.get_trashed(model, obj_id) or abort(404)


@bp.route("")
@require("item.delete")
def index():
    return render_template("trash/index.html", items=trash.trashed_items(), kits=trash.trashed_kits(),
                           days_left=trash.days_left, retention=trash.RETENTION_DAYS)


@bp.route("/<kind>/<int:obj_id>/restaurer", methods=["POST"])
@require("item.delete")
def restore(kind, obj_id):
    obj = _get(kind, obj_id)
    if isinstance(obj, Kit):
        back, elsewhere = trash.restore_kit(obj, current_user)
        db.session.commit()
        message = f"Kit « {obj.name} » restauré"
        if back:
            message += f", {back} objet{'s' if back > 1 else ''} remis dedans"
        if elsewhere:
            plural = "s" if elsewhere > 1 else ""
            message += (f" ({elsewhere} non remis : entré{plural} dans un autre kit, prêté{plural} seul{plural} "
                        f"ou supprimé{plural} entre-temps)")
        flash(message + ".", "success")
        return redirect(url_for("kits.detail", kit_id=obj.id))
    trash.restore_item(obj, current_user)
    db.session.commit()
    flash(f"Objet {obj.code} restauré.", "success")
    return redirect(url_for("items.detail", item_id=obj.id))


@bp.route("/<kind>/<int:obj_id>/supprimer", methods=["POST"])
@require("item.delete")
def purge(kind, obj_id):
    obj = _get(kind, obj_id)
    label = obj.name if isinstance(obj, Kit) else obj.code
    trash.purge([obj], current_user)
    db.session.commit()
    flash(f"{label} supprimé définitivement.", "success")
    return redirect(url_for(".index"))


@bp.route("/vider", methods=["POST"])
@require("settings.manage")
def empty():
    count = trash.purge(trash.trashed_items() + trash.trashed_kits(), current_user)
    db.session.commit()
    flash(f"Corbeille vidée ({count} élément{'s' if count > 1 else ''} supprimé{'s' if count > 1 else ''}).",
          "success")
    return redirect(url_for(".index"))
