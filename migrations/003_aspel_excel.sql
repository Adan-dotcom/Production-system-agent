-- ============================================================
-- Migration 003: Aspel Excel sync + reports improvements
-- Run once with: python apply_migration.py migrations/003_aspel_excel.sql
-- ============================================================

-- ── 1. Extend sales_orders with Aspel fields ──────────────────────────────
ALTER TABLE sales_orders
    ADD COLUMN IF NOT EXISTS customer_code    VARCHAR(50),
    ADD COLUMN IF NOT EXISTS seller_code      VARCHAR(50),
    ADD COLUMN IF NOT EXISTS seller_name      VARCHAR(150),
    ADD COLUMN IF NOT EXISTS order_status     VARCHAR(50),
    ADD COLUMN IF NOT EXISTS order_date       DATE,
    ADD COLUMN IF NOT EXISTS delivery_date    DATE,
    ADD COLUMN IF NOT EXISTS shipping_address TEXT,
    ADD COLUMN IF NOT EXISTS target_kg        NUMERIC(14,3),
    ADD COLUMN IF NOT EXISTS raw_source       VARCHAR(30) DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS updated_at       TIMESTAMP DEFAULT NOW();

-- ── 2. Extend sales_order_lines with Aspel fields ────────────────────────
ALTER TABLE sales_order_lines
    ADD COLUMN IF NOT EXISTS product_code        VARCHAR(50),
    ADD COLUMN IF NOT EXISTS quantity_ordered     NUMERIC(16,4),
    ADD COLUMN IF NOT EXISTS target_kg            NUMERIC(14,3),
    ADD COLUMN IF NOT EXISTS unit_price           NUMERIC(14,4),
    ADD COLUMN IF NOT EXISTS line_total           NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS following_document   VARCHAR(50),
    ADD COLUMN IF NOT EXISTS raw_source           VARCHAR(30) DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS updated_at           TIMESTAMP DEFAULT NOW();

-- ── 3. Aspel import log ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aspel_excel_imports (
    id              SERIAL PRIMARY KEY,
    filename        VARCHAR(255),
    triggered_by    INTEGER REFERENCES users(user_id),
    started_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMP,
    status          VARCHAR(20) NOT NULL DEFAULT 'running',  -- running|done|error
    total_rows      INTEGER DEFAULT 0,
    imported_orders INTEGER DEFAULT 0,
    updated_orders  INTEGER DEFAULT 0,
    imported_lines  INTEGER DEFAULT 0,
    updated_lines   INTEGER DEFAULT 0,
    skipped_rows    INTEGER DEFAULT 0,
    failed_rows     INTEGER DEFAULT 0,
    error_summary   TEXT
);

-- ── 4. Raw staging rows for audit ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aspel_excel_raw_rows (
    id                SERIAL PRIMARY KEY,
    import_id         INTEGER NOT NULL REFERENCES aspel_excel_imports(id) ON DELETE CASCADE,
    row_number        INTEGER NOT NULL,
    raw_data          JSONB,
    normalized_data   JSONB,
    validation_status VARCHAR(20) DEFAULT 'ok',   -- ok|warning|error|skipped
    validation_errors TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_rows_import  ON aspel_excel_raw_rows(import_id);
CREATE INDEX IF NOT EXISTS idx_raw_rows_status  ON aspel_excel_raw_rows(validation_status);
CREATE INDEX IF NOT EXISTS idx_sales_orders_ccode ON sales_orders(customer_code);
CREATE INDEX IF NOT EXISTS idx_sol_product_code   ON sales_order_lines(product_code);
