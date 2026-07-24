-- ============================================================
-- Migration 007: Analytics performance indexes + agentic base
-- Run once with: python apply_migration.py migrations/007_analytics_agentic.sql
-- ============================================================
-- This migration ONLY adds indexes and NEW tables for the analytics &
-- agentic layer. It does NOT touch existing production columns/data.
-- Safe to run multiple times (IF NOT EXISTS everywhere).

-- ── 1. Performance indexes for analytics queries ─────────────────────────────
-- Filters used by /analytics: created_at, status, current_stage, line, user,
-- machine, shift, and the lot→order join path.
CREATE INDEX IF NOT EXISTS idx_items_created_at        ON items(created_at);
CREATE INDEX IF NOT EXISTS idx_items_status            ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_current_stage     ON items(current_stage);
CREATE INDEX IF NOT EXISTS idx_items_line              ON items(sales_order_line_id);
CREATE INDEX IF NOT EXISTS idx_items_lot               ON items(lot_id);
CREATE INDEX IF NOT EXISTS idx_items_created_by        ON items(created_by_user_id);
CREATE INDEX IF NOT EXISTS idx_items_machine           ON items(machine_id);
CREATE INDEX IF NOT EXISTS idx_items_shift             ON items(shift);
CREATE INDEX IF NOT EXISTS idx_items_status_created    ON items(status, created_at);

CREATE INDEX IF NOT EXISTS idx_stage_events_item       ON stage_events(item_id);
CREATE INDEX IF NOT EXISTS idx_stage_events_ts         ON stage_events(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_stage_events_machine    ON stage_events(machine_id);
CREATE INDEX IF NOT EXISTS idx_stage_events_operator   ON stage_events(operator_id);

CREATE INDEX IF NOT EXISTS idx_lpe_item                ON label_print_events(item_id);
CREATE INDEX IF NOT EXISTS idx_lpe_printed_at          ON label_print_events(printed_at);
CREATE INDEX IF NOT EXISTS idx_lpe_reprint             ON label_print_events(is_reprint);

CREATE INDEX IF NOT EXISTS idx_pallets_order           ON pallets(sales_order_id);
CREATE INDEX IF NOT EXISTS idx_pallets_status          ON pallets(status);
CREATE INDEX IF NOT EXISTS idx_pallet_items_pallet     ON pallet_items(pallet_id);
CREATE INDEX IF NOT EXISTS idx_pallet_items_item       ON pallet_items(item_id);

CREATE INDEX IF NOT EXISTS idx_lots_order              ON production_lots(sales_order_id);
CREATE INDEX IF NOT EXISTS idx_cancellations_item      ON item_cancellations(item_id);

-- ── 2. Agentic layer: insights written by analytics agents ───────────────────
-- Agents READ production and WRITE here. They never mutate production data.
CREATE TABLE IF NOT EXISTS analytics_insights (
    insight_id    SERIAL PRIMARY KEY,
    agent         VARCHAR(60)  NOT NULL,            -- 'analitica' | 'calidad' | 'planeacion' | ...
    category      VARCHAR(50)  NOT NULL,            -- 'production' | 'quality' | 'commercial' | 'logistics' | 'data_quality'
    severity      VARCHAR(20)  NOT NULL DEFAULT 'info',  -- info | low | medium | high | critical
    title         VARCHAR(200) NOT NULL,
    body          TEXT,
    metrics       JSONB,                            -- supporting numbers
    scope_type    VARCHAR(30),                      -- 'order' | 'machine' | 'operator' | 'customer' | 'plant' | ...
    scope_ref     VARCHAR(80),                      -- e.g. sae_order_number, machine_code
    period_start  TIMESTAMP,
    period_end    TIMESTAMP,
    status        VARCHAR(20)  NOT NULL DEFAULT 'open',  -- open | ack | dismissed | resolved
    created_by    VARCHAR(60)  DEFAULT 'system',
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_insights_status   ON analytics_insights(status);
CREATE INDEX IF NOT EXISTS idx_insights_category ON analytics_insights(category);
CREATE INDEX IF NOT EXISTS idx_insights_created  ON analytics_insights(created_at);

-- ── 3. Recommendations: suggested (NON-executed) actions ─────────────────────
-- A recommendation is advisory. Operational actions still go through validated
-- endpoints; this table is only a coordination/priority queue for humans/agents.
CREATE TABLE IF NOT EXISTS analytics_recommendations (
    recommendation_id SERIAL PRIMARY KEY,
    agent             VARCHAR(60)  NOT NULL,
    target_role       VARCHAR(40),                   -- 'ventas' | 'outreach' | 'planeacion' | 'calidad' | 'ceo'
    priority          VARCHAR(20)  NOT NULL DEFAULT 'medium',  -- low | medium | high | urgent
    title             VARCHAR(200) NOT NULL,
    action            TEXT         NOT NULL,          -- what is suggested
    rationale         TEXT,                           -- why (data behind it)
    scope_type        VARCHAR(30),
    scope_ref         VARCHAR(80),
    metrics           JSONB,
    status            VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending | accepted | done | rejected
    source_insight_id INTEGER REFERENCES analytics_insights(insight_id),
    created_at        TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_recos_status   ON analytics_recommendations(status);
CREATE INDEX IF NOT EXISTS idx_recos_role     ON analytics_recommendations(target_role);
CREATE INDEX IF NOT EXISTS idx_recos_priority ON analytics_recommendations(priority);

-- ── 4. Agent run log: auditability of every agent execution ──────────────────
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id        SERIAL PRIMARY KEY,
    agent         VARCHAR(60)  NOT NULL,
    trigger       VARCHAR(40)  DEFAULT 'manual',     -- manual | scheduled | event
    status        VARCHAR(20)  NOT NULL DEFAULT 'running',  -- running | done | error
    started_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMP,
    input_summary TEXT,
    output_summary TEXT,
    insights_created        INTEGER DEFAULT 0,
    recommendations_created INTEGER DEFAULT 0,
    error_message TEXT,
    metrics       JSONB
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent  ON agent_runs(agent);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started ON agent_runs(started_at);
