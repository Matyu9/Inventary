from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, render_template, request

from ..filters import euros
from ..permissions import require
from ..services import reports as svc
from ..services.transfer import to_csv

bp = Blueprint("reports", __name__, url_prefix="/rapports")


def period():
    """Période choisie (?debut=AAAA-MM-JJ&fin=AAAA-MM-JJ), par défaut les 12 derniers mois."""
    today = date.today()
    try:
        start = date.fromisoformat(request.args.get("debut", ""))
    except ValueError:
        start = today - timedelta(days=365)
    try:
        end = date.fromisoformat(request.args.get("fin", ""))
    except ValueError:
        end = today
    if end < start:
        start, end = end, start
    end = min(end, date.max - timedelta(days=1))  # fin + 1 jour doit rester une date valide
    return start, end, datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1),
                                                                                         datetime.min.time())


@bp.route("")
@require("report.view")
def index():
    start, end, start_dt, end_dt = period()
    values = svc.value_by_category()
    costs = svc.maintenance_costs(start_dt, end_dt)
    usage = svc.usage(start_dt, end_dt)
    return render_template(
        "reports/index.html", start=start, end=end, kpis=svc.kpis(start_dt, end_dt), values=values,
        value_bars=[{"label": r["category"].name, "value": r["value"], "text": euros(r["value"])} for r in values],
        usage=usage,
        usage_bars=[{"label": f'{r["item"].code} {r["item"].name}', "value": r["loans"],
                     "text": f'{r["loans"]} prêt{"s" if r["loans"] > 1 else ""}'} for r in usage],
        costs=costs,
        cost_bars=[{"label": f'{r["category"]} ({r["count"]})', "value": r["cost"], "text": euros(r["cost"])}
                   for r in costs],
        idle=svc.idle_items(start_dt), compliance=svc.returns_compliance(start_dt, end_dt), custody=svc.custody(),
        bar_width=svc.bar_width,
    )


@bp.route("/<name>.csv")
@require("report.view")
def export(name):
    start, end, start_dt, end_dt = period()
    if name == "valeur":
        rows = [["Catégorie", "Nombre", "Valeur (€)", "Objets sans valeur renseignée"]] + [
            [r["category"].name, r["count"], r["value"], r["unvalued"]] for r in svc.value_by_category()]
    elif name == "utilisation":
        rows = [["Code", "Désignation", "Nombre de prêts"]] + [
            [r["item"].code, r["item"].name, r["loans"]] for r in svc.usage(start_dt, end_dt, limit=10_000)]
    elif name == "inactifs":
        rows = [["Code", "Désignation", "Catégorie", "Lieu"]] + [
            [i.code, i.name, i.category.name, i.location.path if i.location else ""] for i in svc.idle_items(start_dt)]
    elif name == "gardes":
        rows = [["Emprunteur", "Pupitre / classe", "Prêt", "Matériel", "Depuis", "Garant"]]
        for borrower, loans in svc.custody():
            for loan in loans:
                rows.append([borrower.full_name, borrower.section, loan.id,
                             ", ".join(l.kit.name if l.kit else f"{l.item.code} {l.item.name}" for l in loan.lines),
                             loan.start.date(), loan.guarantor.full_name if loan.guarantor else ""])
    elif name == "entretiens":
        rows = [["Catégorie", "Nombre d'entretiens", "Coût total (€)"]] + [
            [r["category"], r["count"], r["cost"]] for r in svc.maintenance_costs(start_dt, end_dt)]
    else:
        abort(404)
    return Response(to_csv(rows), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="rapport-{name}-{start}-{end}.csv"'})
