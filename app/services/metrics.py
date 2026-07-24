"""
Analytics metrics service.

Centralizes every aggregated / compound metric used by the /analytics routes
and the agentic snapshot. All queries are SQL-aggregated (no per-item Python
loops) and respect the production counting rules:

  - Valid production EXCLUDES status in ('cancelled','replaced','rejected').
  - Progress uses net_weight vs target_kg (KILOSxPARTIDA) with ordered_kg fallback.
  - target_kg may be NULL → never force a percentage, report "sin meta".
  - Production timing uses items.created_at; event history uses stage_events.event_timestamp.

Compound metrics (data A + data B → C) implemented here:
  - merma_tara_pct   = (gross - net) / gross           [tara efficiency]
  - kg_per_hour      = net_kg / active_hours           [real productivity]
  - eta_date         = remaining_kg / recent_rate      [projected completion]
  - risk             = eta_date vs Aspel delivery_date [orders at risk]
  - quality_score    = f(rejection, cancel, reprint)   [composite quality]
  - z-score anomalies on daily net per machine
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# Items considered valid production (the canonical filter, alias `i`)
VALID = "i.status NOT IN ('cancelled','replaced','rejected')"

# Canonical join path: item → lot → order → customer (+ line, user, machine)
JOIN_CTX = """
    FROM items i
    JOIN production_lots l ON l.lot_id = i.lot_id
    JOIN sales_orders so   ON so.sales_order_id = l.sales_order_id
    LEFT JOIN clientes c          ON c.customer_id = so.customer_id
    LEFT JOIN sales_order_lines sol ON sol.sales_order_line_id = i.sales_order_line_id
    LEFT JOIN users u             ON u.user_id = i.created_by_user_id
    LEFT JOIN machines m          ON m.machine_id = i.machine_id
"""


# ── helpers ──────────────────────────────────────────────────────────────────
def f(x) -> float:
    return float(x) if x is not None else 0.0


def _as_date(x):
    """Coerce a DATE or TIMESTAMP column value to a python date (or None)."""
    if x is None:
        return None
    return x.date() if isinstance(x, datetime) else x


def _range(col: str, date_from: Optional[str], date_to: Optional[str]) -> str:
    parts = []
    if date_from:
        parts.append(f"{col} >= :date_from")
    if date_to:
        parts.append(f"{col} <= :date_to")
    return (" AND " + " AND ".join(parts)) if parts else ""


def _params(date_from, date_to, **extra) -> dict:
    p = dict(extra)
    if date_from:
        p["date_from"] = datetime.fromisoformat(date_from)
    if date_to:
        # inclusive end-of-day if a bare date was passed
        dt = datetime.fromisoformat(date_to)
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
            dt = dt + timedelta(hours=23, minutes=59, seconds=59)
        p["date_to"] = dt
    return p


def _rows(db: Session, sql: str, params: dict) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]


def _one(db: Session, sql: str, params: dict) -> dict:
    r = db.execute(text(sql), params).mappings().first()
    return dict(r) if r else {}


def _slope(vals: list[float]) -> float:
    """Least-squares slope of a series indexed 0..n-1."""
    n = len(vals)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((xs[i] - mx) * (vals[i] - my) for i in range(n)) / den


def _moving_avg(vals: list[float], window: int = 7) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    for i in range(len(vals)):
        if i + 1 < window:
            out.append(None)
        else:
            seg = vals[i + 1 - window : i + 1]
            out.append(round(sum(seg) / window, 3))
    return out


# ── 1. Headline KPIs ──────────────────────────────────────────────────────────
def kpis(db: Session, date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
    p = _params(date_from, date_to)
    rng = _range("i.created_at", date_from, date_to)

    agg = _one(db, f"""
        SELECT
            COUNT(*) FILTER (WHERE {VALID})                              AS rolls,
            COALESCE(SUM(i.gross_weight) FILTER (WHERE {VALID}), 0)      AS gross,
            COALESCE(SUM(i.net_weight)   FILTER (WHERE {VALID}), 0)      AS net,
            COUNT(*) FILTER (WHERE i.status = 'cancelled')              AS cancelled,
            COUNT(*) FILTER (WHERE i.status = 'rejected')               AS rejected,
            COUNT(*) FILTER (WHERE i.status = 'replaced')               AS replaced,
            COALESCE(SUM(i.net_weight) FILTER (WHERE i.status='rejected'), 0) AS rejected_net,
            COUNT(*) FILTER (WHERE i.status = 'active')                 AS wip_rolls,
            COALESCE(SUM(i.net_weight) FILTER (WHERE i.status='active'), 0)   AS wip_net
        FROM items i
        WHERE 1=1 {rng}
    """, p)

    reprints = _one(db, f"""
        SELECT COUNT(*) AS n
        FROM label_print_events lpe
        WHERE lpe.is_reprint = TRUE {_range('lpe.printed_at', date_from, date_to)}
    """, _params(date_from, date_to))

    pallets = _one(db, """
        SELECT COUNT(*) FILTER (WHERE status='open')  AS open_pallets,
               COUNT(*) FILTER (WHERE status='closed') AS closed_pallets,
               EXTRACT(EPOCH FROM (NOW() - MIN(created_at) FILTER (WHERE status='open')))/3600.0 AS oldest_open_hours
        FROM pallets
    """, {})

    no_target = _one(db, """
        SELECT COUNT(*) AS n FROM sales_orders
        WHERE (target_kg IS NULL OR target_kg = 0)
          AND COALESCE(order_status,'') NOT IN ('CANCELADO','CANCELADA')
    """, {})

    gross = f(agg.get("gross"))
    net = f(agg.get("net"))
    merma = round((gross - net) / gross * 100, 2) if gross > 0 else None

    return {
        "range": {"date_from": date_from, "date_to": date_to},
        "rolls": int(agg.get("rolls") or 0),
        "gross_kg": round(gross, 3),
        "net_kg": round(net, 3),
        "merma_tara_pct": merma,
        "cancelled": int(agg.get("cancelled") or 0),
        "rejected": int(agg.get("rejected") or 0),
        "rejected_net_kg": round(f(agg.get("rejected_net")), 3),
        "replaced": int(agg.get("replaced") or 0),
        "reprints": int(reprints.get("n") or 0),
        "wip_rolls": int(agg.get("wip_rolls") or 0),
        "wip_net_kg": round(f(agg.get("wip_net")), 3),
        "open_pallets": int(pallets.get("open_pallets") or 0),
        "closed_pallets": int(pallets.get("closed_pallets") or 0),
        "oldest_open_pallet_hours": round(f(pallets.get("oldest_open_hours")), 1),
        "orders_missing_target": int(no_target.get("n") or 0),
    }


# ── 2. Daily time series + moving average + trend ──────────────────────────────
def timeseries(db: Session, date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
    p = _params(date_from, date_to)
    rng = _range("i.created_at", date_from, date_to)
    rows = _rows(db, f"""
        SELECT date_trunc('day', i.created_at)::date AS day,
               COUNT(*) FILTER (WHERE {VALID})                         AS rolls,
               COALESCE(SUM(i.net_weight)   FILTER (WHERE {VALID}),0)  AS net,
               COALESCE(SUM(i.gross_weight) FILTER (WHERE {VALID}),0)  AS gross,
               COALESCE(SUM(i.net_weight) FILTER (WHERE i.status='rejected'),0) AS rejected_net,
               COUNT(*) FILTER (WHERE i.status='cancelled')           AS cancelled
        FROM items i
        WHERE 1=1 {rng}
        GROUP BY 1 ORDER BY 1
    """, p)

    series = [{
        "day": r["day"].isoformat(),
        "rolls": int(r["rolls"] or 0),
        "net_kg": round(f(r["net"]), 3),
        "gross_kg": round(f(r["gross"]), 3),
        "rejected_net_kg": round(f(r["rejected_net"]), 3),
        "cancelled": int(r["cancelled"] or 0),
    } for r in rows]

    net_vals = [s["net_kg"] for s in series]
    ma = _moving_avg(net_vals, window=7)
    for i, s in enumerate(series):
        s["net_kg_ma7"] = ma[i]

    slope = round(_slope(net_vals), 3)
    trend = "up" if slope > 0.5 else "down" if slope < -0.5 else "flat"

    return {
        "series": series,
        "trend": trend,
        "trend_slope_kg_per_day": slope,
        "days": len(series),
        "total_net_kg": round(sum(net_vals), 3),
    }


# ── 3. Orders at risk (ETA projection vs Aspel delivery_date) ──────────────────
def orders_at_risk(db: Session, recent_days: int = 7) -> dict:
    recent_cut = datetime.now() - timedelta(days=recent_days)
    rows = _rows(db, f"""
        SELECT so.sales_order_id, so.sae_order_number, c.name AS customer,
               so.delivery_date, so.order_status, so.target_kg, so.ordered_kg,
               so.seller_name,
               COALESCE(SUM(i.net_weight) FILTER (WHERE {VALID}), 0)                            AS produced_net,
               COALESCE(SUM(i.net_weight) FILTER (WHERE {VALID} AND i.created_at >= :recent),0) AS recent_net,
               COUNT(*) FILTER (WHERE {VALID})                                                  AS rolls
        FROM sales_orders so
        LEFT JOIN clientes c          ON c.customer_id = so.customer_id
        LEFT JOIN production_lots l   ON l.sales_order_id = so.sales_order_id
        LEFT JOIN items i             ON i.lot_id = l.lot_id
        WHERE COALESCE(so.order_status,'') NOT IN ('CANCELADO','CANCELADA','ENTREGADO','FACTURADO')
        GROUP BY so.sales_order_id, c.name
        ORDER BY so.delivery_date NULLS LAST
    """, {"recent": recent_cut})

    today = datetime.now().date()
    out = []
    for r in rows:
        target = f(r["target_kg"]) or f(r["ordered_kg"])
        produced = f(r["produced_net"])
        recent = f(r["recent_net"])
        rate = recent / recent_days if recent_days else 0.0          # kg/day
        remaining = max(target - produced, 0.0) if target > 0 else None
        pct = round(produced / target * 100, 1) if target > 0 else None

        eta_days = None
        eta_date = None
        if remaining is not None and remaining > 0 and rate > 0:
            eta_days = round(remaining / rate, 1)
            eta_date = (today + timedelta(days=eta_days)).isoformat()
        elif remaining is not None and remaining <= 0:
            eta_days = 0
            eta_date = today.isoformat()

        deliv = _as_date(r["delivery_date"])
        days_to_delivery = (deliv - today).days if deliv else None

        # Risk classification (compound: meta + ritmo + fecha Aspel)
        if target <= 0:
            risk = "sin_meta"
        elif remaining is not None and remaining <= 0:
            risk = "completo"
        elif rate <= 0 and remaining and remaining > 0:
            risk = "rojo"           # work remaining but no recent production
        elif eta_days is not None and days_to_delivery is not None:
            slack = days_to_delivery - eta_days
            risk = "rojo" if slack < 0 else "ambar" if slack <= 2 else "verde"
        elif days_to_delivery is not None and days_to_delivery < 0 and remaining and remaining > 0:
            risk = "rojo"
        else:
            risk = "verde"

        out.append({
            "sales_order_id": r["sales_order_id"],
            "sae_order_number": r["sae_order_number"],
            "customer": r["customer"] or "",
            "seller_name": r["seller_name"] or "",
            "order_status": r["order_status"] or "—",
            "delivery_date": deliv.isoformat() if deliv else None,
            "days_to_delivery": days_to_delivery,
            "target_kg": round(target, 3) if target > 0 else None,
            "produced_net_kg": round(produced, 3),
            "remaining_kg": round(remaining, 3) if remaining is not None else None,
            "pct_complete": pct,
            "recent_rate_kg_day": round(rate, 2),
            "eta_days": eta_days,
            "eta_date": eta_date,
            "rolls": int(r["rolls"] or 0),
            "risk": risk,
        })

    order = {"rojo": 0, "ambar": 1, "sin_meta": 2, "verde": 3, "completo": 4}
    out.sort(key=lambda x: (order.get(x["risk"], 9),
                            x["days_to_delivery"] if x["days_to_delivery"] is not None else 9999))
    summary = {k: sum(1 for o in out if o["risk"] == k) for k in order}
    return {"orders": out, "summary": summary, "recent_days": recent_days}


# ── 4. Customer Pareto ─────────────────────────────────────────────────────────
def customers_pareto(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("i.created_at", date_from, date_to)
    rows = _rows(db, f"""
        SELECT COALESCE(c.name,'(sin cliente)') AS customer,
               COALESCE(SUM(i.net_weight) FILTER (WHERE {VALID}),0) AS net,
               COUNT(*) FILTER (WHERE {VALID})                      AS rolls
        {JOIN_CTX}
        WHERE 1=1 {rng}
        GROUP BY c.name
        HAVING COUNT(*) FILTER (WHERE {VALID}) > 0
        ORDER BY net DESC
    """, p)
    total = sum(f(r["net"]) for r in rows) or 1.0
    cum = 0.0
    out = []
    for r in rows:
        net = round(f(r["net"]), 3)
        cum += net
        out.append({
            "customer": r["customer"],
            "net_kg": net,
            "rolls": int(r["rolls"] or 0),
            "pct_of_total": round(net / total * 100, 1),
            "cumulative_pct": round(cum / total * 100, 1),
        })
    return out


# ── 5. Product mix ─────────────────────────────────────────────────────────────
def products_mix(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("i.created_at", date_from, date_to)
    rows = _rows(db, f"""
        SELECT COALESCE(sol.product_code,'(s/c)') AS product_code,
               MAX(sol.product_description)        AS product_description,
               COALESCE(SUM(i.net_weight) FILTER (WHERE {VALID}),0) AS net,
               COUNT(*) FILTER (WHERE {VALID})                      AS rolls
        {JOIN_CTX}
        WHERE 1=1 {rng}
        GROUP BY sol.product_code
        HAVING COUNT(*) FILTER (WHERE {VALID}) > 0
        ORDER BY net DESC
    """, p)
    total = sum(f(r["net"]) for r in rows) or 1.0
    return [{
        "product_code": r["product_code"],
        "product_description": r["product_description"] or "",
        "net_kg": round(f(r["net"]), 3),
        "rolls": int(r["rolls"] or 0),
        "pct_of_total": round(f(r["net"]) / total * 100, 1),
    } for r in rows]


# ── 6. Quality by machine (rejection / cancel / reprint → composite score) ─────
def quality_by_machine(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("i.created_at", date_from, date_to)
    base = _rows(db, f"""
        SELECT i.machine_id,
               COALESCE(m.machine_name, '(sin máquina)') AS machine_name,
               COUNT(*) FILTER (WHERE {VALID})                       AS valid_rolls,
               COALESCE(SUM(i.net_weight) FILTER (WHERE {VALID}),0)  AS valid_net,
               COUNT(*) FILTER (WHERE i.status='rejected')           AS rejected,
               COALESCE(SUM(i.net_weight) FILTER (WHERE i.status='rejected'),0) AS rejected_net,
               COUNT(*) FILTER (WHERE i.status='cancelled')          AS cancelled,
               COUNT(*) FILTER (WHERE i.status='replaced')           AS replaced
        FROM items i
        LEFT JOIN machines m ON m.machine_id = i.machine_id
        WHERE 1=1 {rng}
        GROUP BY i.machine_id, m.machine_name
    """, p)

    reprints = _rows(db, f"""
        SELECT i.machine_id, COUNT(*) AS reprints
        FROM label_print_events lpe
        JOIN items i ON i.item_id = lpe.item_id
        WHERE lpe.is_reprint = TRUE {_range('i.created_at', date_from, date_to)}
        GROUP BY i.machine_id
    """, _params(date_from, date_to))
    rp = {r["machine_id"]: int(r["reprints"] or 0) for r in reprints}

    out = []
    for r in base:
        valid = int(r["valid_rolls"] or 0)
        rejected = int(r["rejected"] or 0)
        cancelled = int(r["cancelled"] or 0)
        replaced = int(r["replaced"] or 0)
        reprints_n = rp.get(r["machine_id"], 0)
        attempts = valid + rejected + cancelled + replaced
        if attempts == 0:
            continue
        rej_rate = round(rejected / attempts * 100, 1)
        cancel_rate = round((cancelled + replaced) / attempts * 100, 1)
        reprint_rate = round(reprints_n / valid * 100, 1) if valid else 0.0
        # Composite quality score: 100 minus weighted defects (clamped 0..100)
        score = round(max(0.0, 100 - (rej_rate * 1.5 + cancel_rate * 1.0 + reprint_rate * 0.5)), 1)
        out.append({
            "machine_id": r["machine_id"],
            "machine_name": r["machine_name"],
            "valid_rolls": valid,
            "valid_net_kg": round(f(r["valid_net"]), 3),
            "rejected": rejected,
            "rejected_net_kg": round(f(r["rejected_net"]), 3),
            "cancelled": cancelled,
            "replaced": replaced,
            "reprints": reprints_n,
            "rejection_rate_pct": rej_rate,
            "cancel_rate_pct": cancel_rate,
            "reprint_rate_pct": reprint_rate,
            "quality_score": score,
        })
    out.sort(key=lambda x: x["quality_score"])
    return out


# ── 7. Throughput (kg/h, rolls/h) by machine | operator | shift ────────────────
def throughput(db: Session, group: str = "machine", date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("i.created_at", date_from, date_to)
    if group == "operator":
        gcol, gname, gjoin = "i.created_by_user_id", "u.full_name", "LEFT JOIN users u ON u.user_id = i.created_by_user_id"
    elif group == "shift":
        gcol, gname, gjoin = "i.shift", "i.shift", ""
    else:
        group = "machine"
        gcol, gname, gjoin = "i.machine_id", "m.machine_name", "LEFT JOIN machines m ON m.machine_id = i.machine_id"

    rows = _rows(db, f"""
        SELECT {gcol} AS gkey,
               {gname} AS gname,
               COUNT(*) FILTER (WHERE {VALID})                        AS rolls,
               COALESCE(SUM(i.net_weight) FILTER (WHERE {VALID}),0)   AS net,
               COUNT(DISTINCT date_trunc('hour', i.created_at)) FILTER (WHERE {VALID}) AS active_hours
        FROM items i
        {gjoin}
        WHERE 1=1 {rng}
        GROUP BY {gcol}, {gname}
        HAVING COUNT(*) FILTER (WHERE {VALID}) > 0
        ORDER BY net DESC
    """, p)
    out = []
    for r in rows:
        hours = int(r["active_hours"] or 0) or 1
        net = f(r["net"])
        rolls = int(r["rolls"] or 0)
        out.append({
            "group": group,
            "key": r["gkey"],
            "name": (r["gname"] or "—") if group != "shift" else (r["gkey"] or "—"),
            "rolls": rolls,
            "net_kg": round(net, 3),
            "active_hours": hours,
            "kg_per_hour": round(net / hours, 2),
            "rolls_per_hour": round(rolls / hours, 2),
        })
    return out


# ── 8. Work in progress + pallet aging ─────────────────────────────────────────
def wip(db: Session) -> dict:
    by_stage = _rows(db, """
        SELECT current_stage AS stage,
               COUNT(*) AS rolls,
               COALESCE(SUM(net_weight),0) AS net
        FROM items
        WHERE status = 'active'
        GROUP BY current_stage
        ORDER BY net DESC
    """, {})
    pallets = _rows(db, """
        SELECT p.pallet_code, p.sales_order_id, so.sae_order_number,
               COALESCE(p.total_net_kg,0) AS net,
               EXTRACT(EPOCH FROM (NOW() - p.created_at))/3600.0 AS age_hours,
               (SELECT COUNT(*) FROM pallet_items pi WHERE pi.pallet_id = p.pallet_id) AS rolls
        FROM pallets p
        LEFT JOIN sales_orders so ON so.sales_order_id = p.sales_order_id
        WHERE p.status = 'open'
        ORDER BY p.created_at ASC
    """, {})
    return {
        "by_stage": [{
            "stage": r["stage"], "rolls": int(r["rolls"] or 0), "net_kg": round(f(r["net"]), 3)
        } for r in by_stage],
        "total_wip_rolls": sum(int(r["rolls"] or 0) for r in by_stage),
        "total_wip_net_kg": round(sum(f(r["net"]) for r in by_stage), 3),
        "open_pallets": [{
            "pallet_code": r["pallet_code"],
            "sae_order_number": r["sae_order_number"],
            "net_kg": round(f(r["net"]), 3),
            "rolls": int(r["rolls"] or 0),
            "age_hours": round(f(r["age_hours"]), 1),
        } for r in pallets],
    }


# ── 9. Cycle time between stages (window LAG over stage_events) ─────────────────
def cycle_time(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("event_timestamp", date_from, date_to)
    rows = _rows(db, f"""
        WITH ev AS (
            SELECT item_id, to_stage, event_timestamp,
                   LAG(to_stage)        OVER w AS prev_stage,
                   LAG(event_timestamp) OVER w AS prev_ts
            FROM stage_events
            WHERE 1=1 {rng}
            WINDOW w AS (PARTITION BY item_id ORDER BY event_timestamp)
        )
        SELECT prev_stage, to_stage,
               COUNT(*) AS n,
               AVG(EXTRACT(EPOCH FROM (event_timestamp - prev_ts))/3600.0) AS avg_hours,
               MAX(EXTRACT(EPOCH FROM (event_timestamp - prev_ts))/3600.0) AS max_hours
        FROM ev
        WHERE prev_ts IS NOT NULL
        GROUP BY prev_stage, to_stage
        ORDER BY avg_hours DESC
    """, p)
    return [{
        "from_stage": r["prev_stage"],
        "to_stage": r["to_stage"],
        "transitions": int(r["n"] or 0),
        "avg_hours": round(f(r["avg_hours"]), 2),
        "max_hours": round(f(r["max_hours"]), 2),
    } for r in rows]


# ── 10. Anomaly detection (z-score on daily net per machine) ───────────────────
def anomalies(db: Session, date_from=None, date_to=None, z_threshold: float = 2.0) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("i.created_at", date_from, date_to)
    rows = _rows(db, f"""
        SELECT i.machine_id,
               COALESCE(m.machine_name,'(sin máquina)') AS machine_name,
               date_trunc('day', i.created_at)::date AS day,
               COALESCE(SUM(i.net_weight) FILTER (WHERE {VALID}),0)             AS net,
               COALESCE(SUM(i.net_weight) FILTER (WHERE i.status='rejected'),0) AS rejected_net
        FROM items i
        LEFT JOIN machines m ON m.machine_id = i.machine_id
        WHERE 1=1 {rng}
        GROUP BY i.machine_id, m.machine_name, day
        ORDER BY i.machine_id, day
    """, p)

    # group by machine
    by_machine: dict = {}
    for r in rows:
        by_machine.setdefault(r["machine_id"], {"name": r["machine_name"], "days": []})
        by_machine[r["machine_id"]]["days"].append({
            "day": r["day"], "net": f(r["net"]), "rejected_net": f(r["rejected_net"]),
        })

    out = []
    for mid, data in by_machine.items():
        nets = [d["net"] for d in data["days"]]
        n = len(nets)
        if n < 5:
            continue
        mean = sum(nets) / n
        var = sum((x - mean) ** 2 for x in nets) / n
        std = var ** 0.5
        if std == 0:
            continue
        last = data["days"][-1]
        z = (last["net"] - mean) / std
        if abs(z) >= z_threshold:
            out.append({
                "machine_id": mid,
                "machine_name": data["name"],
                "day": last["day"].isoformat(),
                "net_kg": round(last["net"], 3),
                "mean_kg": round(mean, 3),
                "std_kg": round(std, 3),
                "z_score": round(z, 2),
                "direction": "alto" if z > 0 else "bajo",
            })
    out.sort(key=lambda x: abs(x["z_score"]), reverse=True)
    return out


# ── 11. Aspel data quality ─────────────────────────────────────────────────────
def aspel_quality(db: Session) -> dict:
    last = _one(db, """
        SELECT id, filename, status, started_at, finished_at,
               total_rows, imported_orders, updated_orders,
               imported_lines, updated_lines, skipped_rows, failed_rows
        FROM aspel_excel_imports
        ORDER BY started_at DESC LIMIT 1
    """, {})
    rows_quality = _one(db, """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE validation_status='error')   AS errors,
               COUNT(*) FILTER (WHERE validation_status='warning') AS warnings,
               COUNT(*) FILTER (WHERE validation_status='skipped') AS skipped
        FROM aspel_excel_raw_rows
        WHERE import_id = (SELECT MAX(id) FROM aspel_excel_imports)
    """, {})
    missing_target = _one(db, """
        SELECT COUNT(*) AS n FROM sales_orders
        WHERE (target_kg IS NULL OR target_kg = 0)
          AND raw_source = 'aspel_excel'
    """, {})
    if last.get("started_at"):
        last["started_at"] = last["started_at"].isoformat()
    if last.get("finished_at"):
        last["finished_at"] = last["finished_at"].isoformat()
    return {
        "last_import": last,
        "row_quality": {k: int(v or 0) for k, v in rows_quality.items()},
        "orders_missing_target_kg": int(missing_target.get("n") or 0),
    }


# ── 12. Consolidated snapshot for agents (single JSON context) ─────────────────
def snapshot(db: Session, days: int = 30) -> dict:
    date_from = (datetime.now() - timedelta(days=days)).date().isoformat()
    k = kpis(db, date_from=date_from)
    ts = timeseries(db, date_from=date_from)
    risk = orders_at_risk(db)
    quality = quality_by_machine(db, date_from=date_from)
    anoms = anomalies(db, date_from=date_from)
    aspel = aspel_quality(db)

    open_insights = _one(db, "SELECT COUNT(*) AS n FROM analytics_insights WHERE status='open'", {})
    pending_recos = _one(db, "SELECT COUNT(*) AS n FROM analytics_recommendations WHERE status='pending'", {})

    worst_quality = quality[:3]
    risky = [o for o in risk["orders"] if o["risk"] in ("rojo", "ambar")][:10]

    return {
        "generated_at": datetime.now().isoformat(),
        "window_days": days,
        "kpis": k,
        "production_trend": {
            "trend": ts["trend"],
            "slope_kg_per_day": ts["trend_slope_kg_per_day"],
            "total_net_kg": ts["total_net_kg"],
        },
        "orders_at_risk": {"summary": risk["summary"], "top": risky},
        "worst_quality_machines": worst_quality,
        "anomalies": anoms[:5],
        "aspel_data_quality": aspel,
        "agentic_state": {
            "open_insights": int(open_insights.get("n") or 0),
            "pending_recommendations": int(pending_recos.get("n") or 0),
        },
        "counting_rules": {
            "valid_production_excludes": ["cancelled", "replaced", "rejected"],
            "progress_metric": "net_weight vs target_kg (fallback ordered_kg)",
            "production_timing": "items.created_at",
            "event_timing": "stage_events.event_timestamp",
        },
    }


# ── 13. Export datasets (flat, analyst-ready) ──────────────────────────────────
def dataset_produccion(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("i.created_at", date_from, date_to)
    return _rows(db, f"""
        SELECT i.item_code, i.roll_number, i.barcode_value,
               so.sae_order_number, c.name AS customer_name, so.seller_name,
               sol.line_number, sol.product_code, sol.product_description,
               sol.measure, sol.caliber, sol.color,
               i.current_stage, i.status,
               i.gross_weight, i.bobbin_weight_kg, i.net_weight, i.printed_weight_kg,
               i.branding_mode, i.print_weight_mode, i.shift,
               m.machine_name, u.full_name AS operator_name,
               sol.target_kg AS line_target_kg, so.delivery_date,
               i.created_at, i.cancelled_at, i.cancel_reason
        {JOIN_CTX}
        WHERE 1=1 {rng}
        ORDER BY i.created_at DESC
    """, p)


def dataset_eventos(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("se.event_timestamp", date_from, date_to)
    return _rows(db, f"""
        SELECT se.event_id, it.item_code, se.event_type, se.from_stage, se.to_stage,
               m.machine_name, u.full_name AS operator_name, se.shift,
               se.gross_weight_in, se.net_weight_in, se.gross_weight_out, se.net_weight_out,
               se.shrinkage_kg, se.discrepancy_flag, se.event_timestamp
        FROM stage_events se
        JOIN items it      ON it.item_id = se.item_id
        LEFT JOIN machines m ON m.machine_id = se.machine_id
        LEFT JOIN users u    ON u.user_id = se.operator_id
        WHERE 1=1 {rng}
        ORDER BY se.event_timestamp DESC
    """, p)


def dataset_impresiones(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("lpe.printed_at", date_from, date_to)
    return _rows(db, f"""
        SELECT lpe.print_event_id, it.item_code, lpe.print_number, lpe.label_type,
               lpe.branding_mode, lpe.print_weight_mode, lpe.printed_weight_kg,
               lpe.is_reprint, lpe.reason, u.full_name AS printed_by, lpe.printed_at
        FROM label_print_events lpe
        JOIN items it     ON it.item_id = lpe.item_id
        LEFT JOIN users u ON u.user_id = lpe.printed_by_user_id
        WHERE 1=1 {rng}
        ORDER BY lpe.printed_at DESC
    """, p)


def dataset_tarimas(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("p.created_at", date_from, date_to)
    return _rows(db, f"""
        SELECT p.pallet_code, so.sae_order_number, c.name AS customer_name,
               p.status, p.total_gross_kg, p.total_net_kg, p.total_printed_kg,
               (SELECT COUNT(*) FROM pallet_items pi WHERE pi.pallet_id = p.pallet_id) AS rolls,
               p.created_at, p.closed_at
        FROM pallets p
        LEFT JOIN sales_orders so ON so.sales_order_id = p.sales_order_id
        LEFT JOIN clientes c      ON c.customer_id = so.customer_id
        WHERE 1=1 {rng}
        ORDER BY p.created_at DESC
    """, p)


def dataset_pedidos(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("so.order_date", date_from, date_to)
    return _rows(db, f"""
        SELECT so.sae_order_number, c.name AS customer_name, so.customer_code,
               so.seller_name, so.order_status, so.order_date, so.delivery_date,
               sol.line_number, sol.product_code, sol.product_description,
               sol.quantity_ordered, sol.target_kg, sol.completed_kg,
               sol.unit_price, sol.line_total, sol.status AS line_status,
               so.shipping_address
        FROM sales_orders so
        LEFT JOIN clientes c            ON c.customer_id = so.customer_id
        LEFT JOIN sales_order_lines sol ON sol.sales_order_id = so.sales_order_id
        WHERE 1=1 {rng}
        ORDER BY so.sae_order_number, sol.line_number
    """, p)


def dataset_calidad_aspel(db: Session, date_from=None, date_to=None) -> list[dict]:
    p = _params(date_from, date_to)
    rng = _range("ai.started_at", date_from, date_to)
    return _rows(db, f"""
        SELECT ai.id AS import_id, ai.filename, ai.status, ai.started_at, ai.finished_at,
               ai.total_rows, ai.imported_orders, ai.updated_orders,
               ai.imported_lines, ai.updated_lines, ai.skipped_rows, ai.failed_rows,
               ai.error_summary
        FROM aspel_excel_imports ai
        WHERE 1=1 {rng}
        ORDER BY ai.started_at DESC
    """, p)


DATASETS = {
    "produccion":    {"fn": dataset_produccion,    "desc": "Rollos producidos con pedido, cliente, máquina, operador, pesos y tara"},
    "eventos":       {"fn": dataset_eventos,       "desc": "Eventos de etapa (trazabilidad / tiempos por estación)"},
    "impresiones":   {"fn": dataset_impresiones,   "desc": "Impresiones y reimpresiones de etiqueta"},
    "tarimas":       {"fn": dataset_tarimas,       "desc": "Tarimas con totales y rollos"},
    "pedidos":       {"fn": dataset_pedidos,       "desc": "Pedidos y líneas con meta comercial Aspel"},
    "calidad_aspel": {"fn": dataset_calidad_aspel, "desc": "Bitácora de sincronizaciones Aspel"},
}
