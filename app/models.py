from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.db import Base


class User(Base):
    __tablename__ = "users"

    user_id       = Column(Integer, primary_key=True, index=True)
    username      = Column(String(50), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    full_name     = Column(String(100), nullable=False)
    role          = Column(String(30), nullable=False)
    is_active     = Column(Boolean, nullable=False, default=True)
    created_at    = Column(DateTime, nullable=False, server_default=func.now())


class Station(Base):
    __tablename__ = "stations"

    station_id = Column(Integer, primary_key=True, index=True)
    code       = Column(String(30), unique=True, nullable=False)
    name       = Column(String(100), nullable=False)
    is_active  = Column(Boolean, nullable=False, default=True)


class Machine(Base):
    __tablename__ = "machines"

    machine_id   = Column(Integer, primary_key=True, index=True)
    station_id   = Column(Integer, ForeignKey("stations.station_id"), nullable=False)
    machine_code = Column(String(50), unique=True, nullable=False)
    machine_name = Column(String(100), nullable=False)
    is_active    = Column(Boolean, nullable=False, default=True)


class Cliente(Base):
    __tablename__ = "clientes"

    customer_id           = Column(Integer, primary_key=True, index=True)
    name                  = Column(String(150), nullable=False)
    # Default label branding for this customer. When 'distributor', the operator
    # UI pre-checks the "Distribuidor" box for any line of this customer's orders.
    default_branding_mode = Column(String(20), nullable=False, default="normal")


class Producto(Base):
    """Legacy product config — kept for backward compat. Prefer TareRule for new tare config."""
    __tablename__ = "productos"

    product_id        = Column(Integer, primary_key=True, index=True)
    description       = Column(String(200), nullable=False)
    tare_constant_kg  = Column(Numeric(10, 3), nullable=False, default=10.000)
    tolerance_percent = Column(Numeric(5, 2), nullable=False, default=5.00)


class TareRule(Base):
    """Admin-configurable tare/bobbin rules. Supports fixed, linear_density, threshold, manual."""
    __tablename__ = "tare_rules"

    id                      = Column(Integer, primary_key=True, index=True)
    name                    = Column(String(100), nullable=False)
    rule_type               = Column(String(30), nullable=False, default="fixed")
    fixed_weight_kg         = Column(Numeric(10, 3))
    linear_density_kg_per_m = Column(Numeric(10, 6))
    default_length_m        = Column(Numeric(10, 3))
    threshold_field         = Column(String(50))
    threshold_min           = Column(Numeric(12, 3))
    threshold_max           = Column(Numeric(12, 3))
    product_code            = Column(String(50))
    station_code            = Column(String(30))
    is_default              = Column(Boolean, nullable=False, default=False)
    is_active               = Column(Boolean, nullable=False, default=True)
    notes                   = Column(Text)
    created_at              = Column(DateTime, nullable=False, server_default=func.now())
    updated_at              = Column(DateTime, nullable=False, server_default=func.now())


class SalesOrder(Base):
    __tablename__ = "sales_orders"

    sales_order_id   = Column(Integer, primary_key=True, index=True)
    sae_order_number = Column(String(50), unique=True, nullable=False)
    customer_id      = Column(Integer, ForeignKey("clientes.customer_id"), nullable=False)
    product_id       = Column(Integer, ForeignKey("productos.product_id"), nullable=False)
    ordered_kg       = Column(Numeric(12, 3), nullable=False, default=0)
    # Aspel fields
    customer_code    = Column(String(50))
    seller_code      = Column(String(50))
    seller_name      = Column(String(150))
    order_status     = Column(String(50))
    order_date       = Column(DateTime)
    delivery_date    = Column(DateTime)
    shipping_address = Column(Text)
    target_kg        = Column(Numeric(14, 3))   # KILOSxPARTIDA sum — nullable, not required
    raw_source       = Column(String(30), default="manual")  # 'manual' | 'aspel_excel' | 'csv'
    created_at       = Column(DateTime, nullable=False, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now())


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"

    sales_order_line_id       = Column(Integer, primary_key=True, index=True)
    sales_order_id            = Column(Integer, ForeignKey("sales_orders.sales_order_id"), nullable=False)
    line_number               = Column(Integer, nullable=False)
    product_description       = Column(String(255), nullable=False)
    product_code              = Column(String(50))         # CVE_ART
    presentation_type         = Column(String(100))
    measure                   = Column(String(100))
    caliber                   = Column(String(100))
    color                     = Column(String(100))
    ordered_kg                = Column(Numeric(12, 3), nullable=False, default=0)
    quantity_ordered          = Column(Numeric(16, 4))     # CANT — commercial qty, NOT kg
    target_kg                 = Column(Numeric(14, 3))     # KILOSxPARTIDA — nullable
    unit_price                = Column(Numeric(14, 4))     # PREC
    line_total                = Column(Numeric(14, 2))     # TOT_PARTIDA
    following_document        = Column(String(50))         # DOC_SIG
    completed_kg              = Column(Numeric(12, 3), nullable=False, default=0)
    status                    = Column(String(30), nullable=False, default="open")
    # Tare
    bobbin_weight_kg          = Column(Numeric(10, 3))
    require_bobbin_tare       = Column(Boolean, nullable=False, default=False)
    # Label defaults
    default_branding_mode     = Column(String(20), default="normal")
    default_print_weight_mode = Column(String(10), default="gross")
    default_tare_rule_id      = Column(Integer, ForeignKey("tare_rules.id"))
    # Legacy
    label_template            = Column(String(50), nullable=False, default="internal")
    raw_source                = Column(String(30), default="manual")
    created_at                = Column(DateTime, nullable=False, server_default=func.now())
    updated_at                = Column(DateTime, server_default=func.now())


class AspelExcelImport(Base):
    """Log of each Excel sync run."""
    __tablename__ = "aspel_excel_imports"

    id              = Column(Integer, primary_key=True, index=True)
    filename        = Column(String(255))
    source          = Column(String(30), default="upload_manual")  # upload_manual | configured_path
    triggered_by    = Column(Integer, ForeignKey("users.user_id"))
    started_at      = Column(DateTime, nullable=False, server_default=func.now())
    finished_at     = Column(DateTime)
    status          = Column(String(20), nullable=False, default="running")
    total_rows      = Column(Integer, default=0)
    imported_orders = Column(Integer, default=0)
    updated_orders  = Column(Integer, default=0)
    imported_lines  = Column(Integer, default=0)
    updated_lines   = Column(Integer, default=0)
    skipped_rows    = Column(Integer, default=0)
    failed_rows     = Column(Integer, default=0)
    error_summary   = Column(Text)


class AspelExcelRawRow(Base):
    """Staging/audit table for each row read from Excel."""
    __tablename__ = "aspel_excel_raw_rows"

    id                = Column(Integer, primary_key=True, index=True)
    import_id         = Column(Integer, ForeignKey("aspel_excel_imports.id"), nullable=False)
    row_number        = Column(Integer, nullable=False)
    raw_data          = Column(JSONB)       # raw values from Excel
    normalized_data   = Column(JSONB)       # cleaned/normalized values
    validation_status = Column(String(20), default="ok")   # ok|warning|error|skipped
    validation_errors = Column(Text)
    created_at        = Column(DateTime, nullable=False, server_default=func.now())


class ProductionLot(Base):
    __tablename__ = "production_lots"

    lot_id         = Column(Integer, primary_key=True, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.sales_order_id"), nullable=False)
    lot_code       = Column(String(50), unique=True, nullable=False)
    created_at     = Column(DateTime, nullable=False, server_default=func.now())


class Item(Base):
    __tablename__ = "items"

    item_id                   = Column(Integer, primary_key=True, index=True)
    lot_id                    = Column(Integer, ForeignKey("production_lots.lot_id"), nullable=False)
    sales_order_line_id       = Column(Integer, ForeignKey("sales_order_lines.sales_order_line_id"))
    item_code                 = Column(String(50), unique=True, nullable=False)
    roll_number               = Column(Integer)
    barcode_value             = Column(String(100))
    current_stage             = Column(String(30), nullable=False)
    next_stage                = Column(String(30))
    status                    = Column(String(30), nullable=False, default="active")

    # Weights
    gross_weight              = Column(Numeric(12, 3))  # kept for compat
    net_weight                = Column(Numeric(12, 3))  # kept for compat
    bobbin_weight_kg          = Column(Numeric(10, 3))
    print_weight_mode         = Column(String(10), nullable=False, default="gross")
    printed_weight_kg         = Column(Numeric(12, 3))
    tare_rule_id              = Column(Integer, ForeignKey("tare_rules.id"))

    # Label
    branding_mode             = Column(String(20), nullable=False, default="normal")

    # Context
    machine_id                = Column(Integer, ForeignKey("machines.machine_id"))
    shift                     = Column(String(10))
    created_by_user_id        = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    active_station_entry_event_id = Column(Integer, ForeignKey("stage_events.event_id"))
    created_at                = Column(DateTime, nullable=False, server_default=func.now())

    # Cancellation
    cancelled_at              = Column(DateTime)
    cancelled_by_user_id      = Column(Integer, ForeignKey("users.user_id"))
    cancel_reason             = Column(String(100))
    cancel_notes              = Column(Text)

    # Replacement links
    replaces_item_id          = Column(Integer, ForeignKey("items.item_id"))
    replaced_by_item_id       = Column(Integer, ForeignKey("items.item_id"))


class StageEvent(Base):
    __tablename__ = "stage_events"

    event_id          = Column(Integer, primary_key=True, index=True)
    item_id           = Column(Integer, ForeignKey("items.item_id"), nullable=False)
    event_type        = Column(String(30))
    from_stage        = Column(String(30))
    to_stage          = Column(String(30), nullable=False)
    machine_id        = Column(Integer, ForeignKey("machines.machine_id"))
    operator_id       = Column(Integer, ForeignKey("users.user_id"), nullable=False)

    gross_weight_in   = Column(Numeric(12, 3))
    net_weight_in     = Column(Numeric(12, 3))
    gross_weight_out  = Column(Numeric(12, 3))
    net_weight_out    = Column(Numeric(12, 3))

    tare_constant_kg  = Column(Numeric(10, 3), nullable=False, default=10.000)
    shrinkage_kg      = Column(Numeric(12, 3))
    tolerance_percent = Column(Numeric(5, 2), nullable=False, default=5.00)

    discrepancy_flag  = Column(Boolean, nullable=False, default=False)
    discrepancy_note  = Column(Text)
    shift             = Column(String(20))

    event_timestamp   = Column(DateTime, nullable=False, server_default=func.now())


class LabelPrintEvent(Base):
    __tablename__ = "label_print_events"

    print_event_id    = Column(Integer, primary_key=True, index=True)
    item_id           = Column(Integer, ForeignKey("items.item_id"), nullable=False)
    print_number      = Column(Integer, nullable=False)
    printed_by_user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    # Legacy field (kept for compat)
    template          = Column(String(100))
    # New fields
    label_type        = Column(String(20), default="roll")
    branding_mode     = Column(String(20), default="normal")
    print_weight_mode = Column(String(10), default="gross")
    printed_weight_kg = Column(Numeric(12, 3))
    is_reprint        = Column(Boolean, nullable=False, default=False)
    backend_used      = Column(String(50), default="browser_preview")
    reason            = Column(Text, nullable=False)
    printed_at        = Column(DateTime, nullable=False, server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"

    alert_id            = Column(Integer, primary_key=True, index=True)
    item_id             = Column(Integer, ForeignKey("items.item_id"), nullable=False)
    event_id            = Column(Integer, ForeignKey("stage_events.event_id"))
    alert_type          = Column(String(50), nullable=False)
    severity            = Column(String(20), nullable=False)
    message             = Column(Text, nullable=False)
    created_at          = Column(DateTime, nullable=False, server_default=func.now())
    resolved            = Column(Boolean, nullable=False, default=False)
    resolved_by_user_id = Column(Integer, ForeignKey("users.user_id"))
    resolved_at         = Column(DateTime)


class EventCorrection(Base):
    __tablename__ = "event_corrections"

    correction_id        = Column(Integer, primary_key=True, index=True)
    item_id              = Column(Integer, ForeignKey("items.item_id"), nullable=False)
    target_event_id      = Column(Integer, ForeignKey("stage_events.event_id"), nullable=False)
    corrected_by_user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    field_name           = Column(String(50), nullable=False)
    old_value            = Column(Text)
    new_value            = Column(Text, nullable=False)
    reason               = Column(Text, nullable=False)
    created_at           = Column(DateTime, nullable=False, server_default=func.now())


class Pallet(Base):
    __tablename__ = "pallets"

    pallet_id           = Column(Integer, primary_key=True, index=True)
    pallet_code         = Column(String(80), unique=True, nullable=False)
    sales_order_id      = Column(Integer, ForeignKey("sales_orders.sales_order_id"), nullable=False)
    sales_order_line_id = Column(Integer, ForeignKey("sales_order_lines.sales_order_line_id"))  # optional/legacy
    # Totals
    total_kg            = Column(Numeric(12, 3), nullable=False, default=0)  # legacy
    total_gross_kg      = Column(Numeric(12, 3), default=0)
    total_net_kg        = Column(Numeric(12, 3), default=0)
    total_printed_kg    = Column(Numeric(12, 3), default=0)
    pallet_weight_mode  = Column(String(10), default="gross")
    status              = Column(String(30), nullable=False, default="open")
    created_by_user_id  = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    closed_by_user_id   = Column(Integer, ForeignKey("users.user_id"))
    created_at          = Column(DateTime, nullable=False, server_default=func.now())
    closed_at           = Column(DateTime)


class PalletItem(Base):
    __tablename__ = "pallet_items"

    pallet_item_id = Column(Integer, primary_key=True, index=True)
    pallet_id      = Column(Integer, ForeignKey("pallets.pallet_id"), nullable=False)
    item_id        = Column(Integer, ForeignKey("items.item_id"), nullable=False)
    added_by_user_id = Column(Integer, ForeignKey("users.user_id"))
    added_at       = Column(DateTime, nullable=False, server_default=func.now())


class ItemInput(Base):
    """Records which items were consumed to produce a new output item."""
    __tablename__ = "item_inputs"

    input_record_id = Column(Integer, primary_key=True, index=True)
    output_item_id  = Column(Integer, ForeignKey("items.item_id"), nullable=False)
    input_item_id   = Column(Integer, ForeignKey("items.item_id"), nullable=False)
    recorded_at     = Column(DateTime, nullable=False, server_default=func.now())


class ItemCancellation(Base):
    __tablename__ = "item_cancellations"

    cancellation_id       = Column(Integer, primary_key=True, index=True)
    item_id               = Column(Integer, ForeignKey("items.item_id"), nullable=False)
    cancelled_by_user_id  = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    reason                = Column(Text, nullable=False)
    notes                 = Column(Text)
    replacement_item_id   = Column(Integer, ForeignKey("items.item_id"))
    cancelled_at          = Column(DateTime, nullable=False, server_default=func.now())


# ── Analytics & Agentic layer (migration 007) ──────────────────────────────
class AnalyticsInsight(Base):
    """Findings written by analytics agents. Agents read production, write here."""
    __tablename__ = "analytics_insights"

    insight_id   = Column(Integer, primary_key=True, index=True)
    agent        = Column(String(60), nullable=False)
    category     = Column(String(50), nullable=False)   # production|quality|commercial|logistics|data_quality
    severity     = Column(String(20), nullable=False, default="info")
    title        = Column(String(200), nullable=False)
    body         = Column(Text)
    metrics      = Column(JSONB)
    scope_type   = Column(String(30))    # order|machine|operator|customer|plant
    scope_ref    = Column(String(80))
    period_start = Column(DateTime)
    period_end   = Column(DateTime)
    status       = Column(String(20), nullable=False, default="open")  # open|ack|dismissed|resolved
    created_by   = Column(String(60), default="system")
    created_at   = Column(DateTime, nullable=False, server_default=func.now())
    updated_at   = Column(DateTime, server_default=func.now())


class AnalyticsRecommendation(Base):
    """Advisory (NON-executed) suggested actions for coordination/prioritization."""
    __tablename__ = "analytics_recommendations"

    recommendation_id = Column(Integer, primary_key=True, index=True)
    agent             = Column(String(60), nullable=False)
    target_role       = Column(String(40))   # ventas|outreach|planeacion|calidad|ceo
    priority          = Column(String(20), nullable=False, default="medium")
    title             = Column(String(200), nullable=False)
    action            = Column(Text, nullable=False)
    rationale         = Column(Text)
    scope_type        = Column(String(30))
    scope_ref         = Column(String(80))
    metrics           = Column(JSONB)
    status            = Column(String(20), nullable=False, default="pending")  # pending|accepted|done|rejected
    source_insight_id = Column(Integer, ForeignKey("analytics_insights.insight_id"))
    created_at        = Column(DateTime, nullable=False, server_default=func.now())
    updated_at        = Column(DateTime, server_default=func.now())


class AgentRun(Base):
    """Audit log of every agent execution."""
    __tablename__ = "agent_runs"

    run_id                  = Column(Integer, primary_key=True, index=True)
    agent                   = Column(String(60), nullable=False)
    trigger                 = Column(String(40), default="manual")  # manual|scheduled|event
    status                  = Column(String(20), nullable=False, default="running")  # running|done|error
    started_at              = Column(DateTime, nullable=False, server_default=func.now())
    finished_at             = Column(DateTime)
    input_summary           = Column(Text)
    output_summary          = Column(Text)
    insights_created        = Column(Integer, default=0)
    recommendations_created = Column(Integer, default=0)
    error_message           = Column(Text)
    metrics                 = Column(JSONB)


class MachineSensorStatus(Base):
    """Live on/off telemetry — one row per sensorized machine (upserted per MQTT message)."""
    __tablename__ = "machine_sensor_status"

    machine_id      = Column(Integer, ForeignKey("machines.machine_id"), primary_key=True)
    last_freq_hz    = Column(Numeric(8, 2), nullable=False, default=0)   # pulses/s from ESP32
    magnets_per_rev = Column(Integer, nullable=False, default=1)         # imanes en el rodillo (para RPM)
    is_running      = Column(Boolean, nullable=False, default=False)     # last ingest-time decision (freq>0)
    last_pulse_at   = Column(DateTime)                                   # last message received (UTC)
    updated_at      = Column(DateTime, nullable=False, server_default=func.now())


class MachineSensorEvent(Base):
    """Append-only start/stop transition log (one row per on↔off flip, not per pulse)."""
    __tablename__ = "machine_sensor_events"

    event_id    = Column(Integer, primary_key=True, index=True)
    machine_id  = Column(Integer, ForeignKey("machines.machine_id"), nullable=False)
    event_type  = Column(String(10), nullable=False)   # 'start' | 'stop'
    freq_hz     = Column(Numeric(8, 2), nullable=False, default=0)
    occurred_at = Column(DateTime, nullable=False, server_default=func.now())
