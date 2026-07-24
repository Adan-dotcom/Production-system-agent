"""
End-to-end simulation of the full production flow over ALL orders in the DB.
Exercises real HTTP endpoints via FastAPI TestClient (routing + auth + schemas + logic).

Flow per order line:
  extrusion -> corte -> impresion -> bolseo -> almacen   (full chain, source-linked)
  + a scrap roll to reciclaje
  + label print + reprint
Plus, per order: pallet create/add/close, one cancel, one cancel+replace.
Then it hits all reports/analytics endpoints and renders label/pallet previews.

Records every failure (HTTP != expected) and prints a consolidated report.
"""
import sys
import traceback
from collections import defaultdict

from fastapi.testclient import TestClient

from app.main import app
from app.db import SessionLocal
from app import models
from app.auth import create_access_token

client = TestClient(app)  # no 'with' -> skip lifespan (scheduler/mqtt) side effects

db = SessionLocal()
admin = db.query(models.User).filter(models.User.role == "admin").first()
op = db.query(models.User).filter(models.User.role == "operator").first() or admin
ADMIN_H = {"Authorization": f"Bearer {create_access_token({'user_id': admin.user_id})}"}
OP_H    = {"Authorization": f"Bearer {create_access_token({'user_id': op.user_id})}"}

# machine per station
MACH = {}
for m in db.query(models.Machine).all():
    st = db.query(models.Station).filter(models.Station.station_id == m.station_id).first()
    if st:
        MACH[st.code] = m.machine_id

failures = []   # (context, detail)
notes    = []   # informational observations
counts   = defaultdict(int)


def call(method, url, ctx, json=None, headers=OP_H, expect=200):
    counts[method + " " + url.split("?")[0].rsplit("/", 1)[0]] += 1
    try:
        r = client.request(method, url, json=json, headers=headers)
    except Exception as e:
        failures.append((ctx, f"EXCEPTION {type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}"))
        return None
    if r.status_code != expect:
        body = r.text[:300]
        failures.append((ctx, f"{method} {url} -> {r.status_code} (esperaba {expect}) :: {body}"))
        return None
    try:
        return r.json()
    except Exception:
        return r.text


def produce(line_id, station, nxt, gross, sources=None, branding="normal", ctx=""):
    payload = {
        "sales_order_line_id": line_id,
        "station": station,
        "next_stage": nxt,
        "gross_weight": gross,
        "machine_id": MACH.get(station),
        "branding_mode": branding,
        "source_item_codes": sources or [],
    }
    res = call("POST", "/items/produce", ctx or f"produce {station}->{nxt} line {line_id}", json=payload)
    return res


orders = db.query(models.SalesOrder).order_by(models.SalesOrder.sae_order_number).all()
print(f"Procesando {len(orders)} pedidos...\n")

dist_item_code = None   # keep one distributor item for preview check
normal_item_code = None
all_almacen_items = []   # (sae, item_code)
cancel_target = None
replace_target = None

for oi, so in enumerate(orders):
    sae = so.sae_order_number
    lines = db.query(models.SalesOrderLine).filter(
        models.SalesOrderLine.sales_order_id == so.sales_order_id
    ).order_by(models.SalesOrderLine.line_number).all()

    for li, line in enumerate(lines):
        lid = line.sales_order_line_id
        base = 100.0 + (li * 10)

        # decide branding for variety: every 7th line uses distributor
        branding = "distributor" if (oi + li) % 7 == 0 else "normal"

        e = produce(lid, "extrusion", "corte", base, branding=branding, ctx=f"SAE {sae} L{line.line_number} extrusion")
        if not e:
            continue
        c = produce(lid, "corte", "impresion", base * 0.97, sources=[e["item_code"]], branding=branding,
                    ctx=f"SAE {sae} L{line.line_number} corte")
        if not c:
            continue
        p = produce(lid, "impresion", "bolseo", base * 0.96, sources=[c["item_code"]], branding=branding,
                    ctx=f"SAE {sae} L{line.line_number} impresion")
        if not p:
            continue
        b = produce(lid, "bolseo", "almacen", base * 0.95, sources=[p["item_code"]], branding=branding,
                    ctx=f"SAE {sae} L{line.line_number} bolseo")
        if not b:
            continue

        all_almacen_items.append((sae, b["item_code"]))
        if branding == "distributor" and not dist_item_code:
            dist_item_code = b["item_code"]
        if branding == "normal" and not normal_item_code:
            normal_item_code = b["item_code"]

        # labels
        call("POST", f"/labels/{b['item_code']}/print", f"SAE {sae} print {b['item_code']}",
             json={"reason": "print"})
        call("POST", f"/labels/{b['item_code']}/reprint", f"SAE {sae} reprint {b['item_code']}",
             json={"reason": "reimpresion test"})

        # scrap roll -> reciclaje
        produce(lid, "extrusion", "reciclaje", 12.0, ctx=f"SAE {sae} L{line.line_number} scrap")

        # pick cancel / replace targets (use extrusion items so we don't disturb almacen counts much)
        if cancel_target is None:
            cancel_target = e["item_code"]
        elif replace_target is None:
            replace_target = (lid, e["item_code"])

# ── Cancel ──
if cancel_target:
    call("POST", f"/items/{cancel_target}/cancel", f"cancel {cancel_target}",
         json={"reason": "prueba", "notes": "cancel sim"})

# ── Cancel + replace ──
if replace_target:
    lid, old_code = replace_target
    payload = {
        "sales_order_line_id": lid, "station": "extrusion", "next_stage": "corte",
        "gross_weight": 90.0, "machine_id": MACH.get("extrusion"),
        "replaces_item_code": old_code, "source_item_codes": [],
    }
    call("POST", "/items/produce", f"cancel+replace de {old_code}", json=payload)

# ── Pallets: one per order that has almacen items ──
by_sae = defaultdict(list)
for sae, code in all_almacen_items:
    by_sae[sae].append(code)

pallet_done = 0
for sae, codes in by_sae.items():
    pres = call("POST", f"/pallets/create-for-order/{sae}", f"crear tarima {sae}")
    if not pres:
        continue
    pcode = pres.get("pallet_code")
    added = 0
    for code in codes:
        r = call("POST", f"/pallets/{pcode}/items", f"add {code} a tarima {pcode}",
                 json={"item_code": code})
        if r:
            added += 1
    if added:
        call("POST", f"/pallets/{pcode}/close", f"cerrar tarima {pcode}")
        pallet_done += 1

# ── Reports & analytics (just check they respond 200) ──
for url in [
    "/reports/by-pi", "/reports/by-operator", "/reports/by-shift", "/reports/summary",
    "/analytics/kpis", "/analytics/snapshot", "/analytics/orders-at-risk",
    "/analytics/customers", "/analytics/products", "/analytics/quality",
    "/analytics/throughput", "/analytics/wip", "/analytics/cycle-time",
    "/analytics/anomalies", "/analytics/aspel-quality", "/analytics/timeseries",
    "/alerts",
]:
    call("GET", url, f"GET {url}", headers=ADMIN_H)

# ── Label previews: branding format check ──
for code, kind in [(normal_item_code, "normal"), (dist_item_code, "distributor")]:
    if not code:
        notes.append(f"No se generó item {kind} para preview")
        continue
    r = client.get(f"/labels/{code}/preview")
    if r.status_code != 200:
        failures.append((f"preview {kind} {code}", f"{r.status_code} :: {r.text[:200]}"))
        continue
    html = r.text
    is_dist = "DISTRIBUIDOR" in html
    has_biot = "Biotécnica" in html
    if kind == "distributor" and not is_dist:
        failures.append((f"preview {kind}", "preview NO muestra formato distribuidor"))
    if kind == "normal" and is_dist:
        failures.append((f"preview {kind}", "preview normal muestra badge distribuidor"))
    notes.append(f"preview {kind} ({code}): DISTRIBUIDOR={is_dist} Biotécnica={has_biot}")

# ── Report ──
print("\n" + "=" * 70)
print("RESUMEN DE SIMULACIÓN")
print("=" * 70)
print(f"Pedidos: {len(orders)} | items en almacén: {len(all_almacen_items)} | tarimas cerradas: {pallet_done}")
print(f"Items totales en DB ahora: {db.query(models.Item).count()}")
print("\nNOTAS:")
for n in notes:
    print("  -", n)
print(f"\nFALLOS: {len(failures)}")
for ctx, det in failures:
    print(f"  [X] {ctx}\n       {det}")

db.close()
print("\nlisto.")
