"""
Analytics & agentic API.

Read endpoints (any authenticated user — the CEO uses /ui):
  GET  /analytics/kpis            headline + compound KPIs
  GET  /analytics/timeseries      daily series + 7d moving avg + trend
  GET  /analytics/orders-at-risk  ETA projection vs Aspel delivery_date (semáforo)
  GET  /analytics/customers       Pareto by customer
  GET  /analytics/products        product mix
  GET  /analytics/quality         quality by machine (composite score)
  GET  /analytics/throughput      kg/h & rolls/h by machine|operator|shift
  GET  /analytics/wip             work-in-progress + open-pallet aging
  GET  /analytics/cycle-time      avg hours between stages
  GET  /analytics/anomalies       z-score anomalies on daily net per machine
  GET  /analytics/aspel-quality   Aspel sync data quality
  GET  /analytics/snapshot        consolidated JSON context for agents
  GET  /analytics/datasets        list of exportable datasets
  GET  /analytics/export/{name}   download dataset as csv|json

Agentic layer (admin/service token — agents read production, write here):
  GET/POST           /analytics/insights ; PATCH /analytics/insights/{id}
  GET/POST           /analytics/recommendations ; PATCH /analytics/recommendations/{id}
  POST/PATCH/GET     /analytics/agent-runs
"""
import csv
import io
import json
from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app import models
from app.auth import get_current_user, require_admin
from app.services import metrics

router = APIRouter()


# ── serialization helpers ──────────────────────────────────────────────────────
def _json_safe(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _rows_json(rows: list[dict]) -> list[dict]:
    return [{k: _json_safe(v) for k, v in r.items()} for r in rows]


def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if v is None else _json_safe(v)) for k, v in r.items()})
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── READ: metrics ───────────────────────────────────────────────────────────────
@router.get("/kpis")
def get_kpis(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
             db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.kpis(db, date_from, date_to)


@router.get("/timeseries")
def get_timeseries(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                   db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.timeseries(db, date_from, date_to)


@router.get("/orders-at-risk")
def get_orders_at_risk(recent_days: int = Query(7, ge=1, le=90),
                       db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.orders_at_risk(db, recent_days)


@router.get("/customers")
def get_customers(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                  db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.customers_pareto(db, date_from, date_to)


@router.get("/products")
def get_products(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                 db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.products_mix(db, date_from, date_to)


@router.get("/quality")
def get_quality(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.quality_by_machine(db, date_from, date_to)


@router.get("/throughput")
def get_throughput(group: str = Query("machine", pattern="^(machine|operator|shift)$"),
                   date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                   db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.throughput(db, group, date_from, date_to)


@router.get("/wip")
def get_wip(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.wip(db)


@router.get("/cycle-time")
def get_cycle_time(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                   db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.cycle_time(db, date_from, date_to)


@router.get("/anomalies")
def get_anomalies(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                  z_threshold: float = Query(2.0, ge=1.0, le=5.0),
                  db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.anomalies(db, date_from, date_to, z_threshold)


@router.get("/aspel-quality")
def get_aspel_quality(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.aspel_quality(db)


@router.get("/snapshot")
def get_snapshot(days: int = Query(30, ge=1, le=365),
                 db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return metrics.snapshot(db, days)


# ── READ: dataset catalog + export ──────────────────────────────────────────────
@router.get("/datasets")
def list_datasets(current_user=Depends(get_current_user)):
    return [{"name": k, "description": v["desc"],
             "formats": ["csv", "json"],
             "export_url": f"/analytics/export/{k}"} for k, v in metrics.DATASETS.items()]


@router.get("/export/{name}")
def export_dataset(name: str, format: str = Query("csv", pattern="^(csv|json)$"),
                   date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                   db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    ds = metrics.DATASETS.get(name)
    if not ds:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' no existe. Ver /analytics/datasets")
    rows = ds["fn"](db, date_from, date_to)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if format == "json":
        payload = json.dumps(_rows_json(rows), ensure_ascii=False, indent=2)
        return StreamingResponse(
            iter([payload]),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={name}_{stamp}.json"},
        )
    return _csv_response(rows, f"{name}_{stamp}.csv")


# ── AGENTIC: insights ────────────────────────────────────────────────────────────
class InsightIn(BaseModel):
    agent: str
    category: str
    severity: str = "info"
    title: str
    body: Optional[str] = None
    metrics: Optional[dict] = None
    scope_type: Optional[str] = None
    scope_ref: Optional[str] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    created_by: Optional[str] = "system"


class StatusPatch(BaseModel):
    status: str


def _insight_dict(x: models.AnalyticsInsight) -> dict:
    return {
        "insight_id": x.insight_id, "agent": x.agent, "category": x.category,
        "severity": x.severity, "title": x.title, "body": x.body, "metrics": x.metrics,
        "scope_type": x.scope_type, "scope_ref": x.scope_ref,
        "period_start": x.period_start.isoformat() if x.period_start else None,
        "period_end": x.period_end.isoformat() if x.period_end else None,
        "status": x.status, "created_by": x.created_by,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }


@router.get("/insights")
def list_insights(status: Optional[str] = Query(None), category: Optional[str] = Query(None),
                  severity: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500),
                  db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    q = db.query(models.AnalyticsInsight)
    if status:
        q = q.filter(models.AnalyticsInsight.status == status)
    if category:
        q = q.filter(models.AnalyticsInsight.category == category)
    if severity:
        q = q.filter(models.AnalyticsInsight.severity == severity)
    rows = q.order_by(models.AnalyticsInsight.created_at.desc()).limit(limit).all()
    return [_insight_dict(x) for x in rows]


@router.post("/insights")
def create_insight(payload: InsightIn, db: Session = Depends(get_db),
                   current_user=Depends(require_admin)):
    x = models.AnalyticsInsight(**payload.model_dump())
    db.add(x)
    db.commit()
    db.refresh(x)
    return _insight_dict(x)


@router.patch("/insights/{insight_id}")
def update_insight(insight_id: int, patch: StatusPatch, db: Session = Depends(get_db),
                   current_user=Depends(require_admin)):
    x = db.query(models.AnalyticsInsight).filter(models.AnalyticsInsight.insight_id == insight_id).first()
    if not x:
        raise HTTPException(status_code=404, detail="Insight no encontrado")
    x.status = patch.status
    db.commit()
    db.refresh(x)
    return _insight_dict(x)


# ── AGENTIC: recommendations ─────────────────────────────────────────────────────
class RecoIn(BaseModel):
    agent: str
    target_role: Optional[str] = None
    priority: str = "medium"
    title: str
    action: str
    rationale: Optional[str] = None
    scope_type: Optional[str] = None
    scope_ref: Optional[str] = None
    metrics: Optional[dict] = None
    source_insight_id: Optional[int] = None


def _reco_dict(x: models.AnalyticsRecommendation) -> dict:
    return {
        "recommendation_id": x.recommendation_id, "agent": x.agent,
        "target_role": x.target_role, "priority": x.priority, "title": x.title,
        "action": x.action, "rationale": x.rationale, "scope_type": x.scope_type,
        "scope_ref": x.scope_ref, "metrics": x.metrics, "status": x.status,
        "source_insight_id": x.source_insight_id,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    }


@router.get("/recommendations")
def list_recommendations(status: Optional[str] = Query(None), target_role: Optional[str] = Query(None),
                         priority: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500),
                         db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    q = db.query(models.AnalyticsRecommendation)
    if status:
        q = q.filter(models.AnalyticsRecommendation.status == status)
    if target_role:
        q = q.filter(models.AnalyticsRecommendation.target_role == target_role)
    if priority:
        q = q.filter(models.AnalyticsRecommendation.priority == priority)
    rows = q.order_by(models.AnalyticsRecommendation.created_at.desc()).limit(limit).all()
    return [_reco_dict(x) for x in rows]


@router.post("/recommendations")
def create_recommendation(payload: RecoIn, db: Session = Depends(get_db),
                          current_user=Depends(require_admin)):
    x = models.AnalyticsRecommendation(**payload.model_dump())
    db.add(x)
    db.commit()
    db.refresh(x)
    return _reco_dict(x)


@router.patch("/recommendations/{recommendation_id}")
def update_recommendation(recommendation_id: int, patch: StatusPatch, db: Session = Depends(get_db),
                          current_user=Depends(require_admin)):
    x = db.query(models.AnalyticsRecommendation).filter(
        models.AnalyticsRecommendation.recommendation_id == recommendation_id).first()
    if not x:
        raise HTTPException(status_code=404, detail="Recomendación no encontrada")
    x.status = patch.status
    db.commit()
    db.refresh(x)
    return _reco_dict(x)


# ── AGENTIC: agent run log ───────────────────────────────────────────────────────
class RunIn(BaseModel):
    agent: str
    trigger: str = "manual"
    input_summary: Optional[str] = None


class RunPatch(BaseModel):
    status: Optional[str] = None
    output_summary: Optional[str] = None
    insights_created: Optional[int] = None
    recommendations_created: Optional[int] = None
    error_message: Optional[str] = None
    metrics: Optional[dict] = None


def _run_dict(x: models.AgentRun) -> dict:
    return {
        "run_id": x.run_id, "agent": x.agent, "trigger": x.trigger, "status": x.status,
        "started_at": x.started_at.isoformat() if x.started_at else None,
        "finished_at": x.finished_at.isoformat() if x.finished_at else None,
        "input_summary": x.input_summary, "output_summary": x.output_summary,
        "insights_created": x.insights_created, "recommendations_created": x.recommendations_created,
        "error_message": x.error_message, "metrics": x.metrics,
    }


@router.get("/agent-runs")
def list_agent_runs(agent: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200),
                    db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    q = db.query(models.AgentRun)
    if agent:
        q = q.filter(models.AgentRun.agent == agent)
    rows = q.order_by(models.AgentRun.started_at.desc()).limit(limit).all()
    return [_run_dict(x) for x in rows]


@router.post("/agent-runs")
def create_agent_run(payload: RunIn, db: Session = Depends(get_db),
                     current_user=Depends(require_admin)):
    x = models.AgentRun(**payload.model_dump())
    db.add(x)
    db.commit()
    db.refresh(x)
    return _run_dict(x)


@router.patch("/agent-runs/{run_id}")
def update_agent_run(run_id: int, patch: RunPatch, db: Session = Depends(get_db),
                     current_user=Depends(require_admin)):
    x = db.query(models.AgentRun).filter(models.AgentRun.run_id == run_id).first()
    if not x:
        raise HTTPException(status_code=404, detail="Agent run no encontrado")
    data = patch.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(x, k, v)
    if data.get("status") in ("done", "error") and not x.finished_at:
        x.finished_at = datetime.now()
    db.commit()
    db.refresh(x)
    return _run_dict(x)
