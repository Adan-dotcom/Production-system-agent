-- Migration 002: Major improvements (2026-05-24)
-- Run: python apply_migration.py migrations/002_major_improvements.sql

-- ── 1. TARE RULES TABLE ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tare_rules (
    id                      SERIAL PRIMARY KEY,
    name                    VARCHAR(100) NOT NULL,
    rule_type               VARCHAR(30)  NOT NULL DEFAULT 'fixed',
    fixed_weight_kg         NUMERIC(10,3),
    linear_density_kg_per_m NUMERIC(10,6),
    default_length_m        NUMERIC(10,3),
    threshold_field         VARCHAR(50),
    threshold_min           NUMERIC(12,3),
    threshold_max           NUMERIC(12,3),
    product_code            VARCHAR(50),
    station_code            VARCHAR(30),
    is_default              BOOLEAN NOT NULL DEFAULT FALSE,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    notes                   TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO tare_rules (name, rule_type, fixed_weight_kg, is_default, is_active, notes)
SELECT 'Tara fija 10 kg (default)', 'fixed', 10.000, TRUE, TRUE,
       'Tara default migrada del sistema anterior. Ajustar por producto según necesidad.'
WHERE NOT EXISTS (SELECT 1 FROM tare_rules);

-- ── 2. NEW COLUMNS ON ITEMS ───────────────────────────────────────────────
ALTER TABLE items
    ADD COLUMN IF NOT EXISTS bobbin_weight_kg       NUMERIC(10,3),
    ADD COLUMN IF NOT EXISTS print_weight_mode      VARCHAR(10)  NOT NULL DEFAULT 'gross',
    ADD COLUMN IF NOT EXISTS printed_weight_kg      NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS branding_mode          VARCHAR(20)  NOT NULL DEFAULT 'normal',
    ADD COLUMN IF NOT EXISTS roll_number            INTEGER,
    ADD COLUMN IF NOT EXISTS barcode_value          VARCHAR(100),
    ADD COLUMN IF NOT EXISTS machine_id             INTEGER REFERENCES machines(machine_id),
    ADD COLUMN IF NOT EXISTS shift                  VARCHAR(10),
    ADD COLUMN IF NOT EXISTS cancelled_at           TIMESTAMP,
    ADD COLUMN IF NOT EXISTS cancelled_by_user_id   INTEGER REFERENCES users(user_id),
    ADD COLUMN IF NOT EXISTS cancel_reason          VARCHAR(100),
    ADD COLUMN IF NOT EXISTS cancel_notes           TEXT,
    ADD COLUMN IF NOT EXISTS replaces_item_id       INTEGER REFERENCES items(item_id),
    ADD COLUMN IF NOT EXISTS replaced_by_item_id    INTEGER REFERENCES items(item_id),
    ADD COLUMN IF NOT EXISTS tare_rule_id           INTEGER REFERENCES tare_rules(id);

-- Backfill existing items
UPDATE items i SET
    barcode_value   = i.item_code,
    roll_number     = i.item_id,
    bobbin_weight_kg = COALESCE(
        (SELECT se.tare_constant_kg FROM stage_events se
         WHERE se.item_id = i.item_id ORDER BY se.event_id LIMIT 1),
        10.000
    ),
    net_weight = CASE
        WHEN i.gross_weight IS NOT NULL AND i.net_weight IS NULL
            THEN i.gross_weight - COALESCE(
                (SELECT se.tare_constant_kg FROM stage_events se
                 WHERE se.item_id = i.item_id ORDER BY se.event_id LIMIT 1), 10.0)
        ELSE i.net_weight
    END,
    printed_weight_kg = COALESCE(i.gross_weight, 0),
    shift = COALESCE(
        (SELECT se.shift FROM stage_events se
         WHERE se.item_id = i.item_id ORDER BY se.event_id LIMIT 1),
        CASE WHEN EXTRACT(HOUR FROM i.created_at) >= 6 AND EXTRACT(HOUR FROM i.created_at) < 14 THEN 'T1'
             WHEN EXTRACT(HOUR FROM i.created_at) >= 14 AND EXTRACT(HOUR FROM i.created_at) < 22 THEN 'T2'
             ELSE 'T3' END
    ),
    machine_id = (
        SELECT se.machine_id FROM stage_events se
        WHERE se.item_id = i.item_id ORDER BY se.event_id LIMIT 1
    )
WHERE i.barcode_value IS NULL;

-- Update status constraint: add 'replaced' and 'palletized'
ALTER TABLE items DROP CONSTRAINT IF EXISTS items_status_check;
ALTER TABLE items ADD CONSTRAINT items_status_check
    CHECK (status IN ('active','blocked','completed','rejected','cancelled','replaced','palletized'));

-- ── 3. NEW COLUMNS ON SALES_ORDER_LINES ───────────────────────────────────
ALTER TABLE sales_order_lines
    ADD COLUMN IF NOT EXISTS default_branding_mode      VARCHAR(20) DEFAULT 'normal',
    ADD COLUMN IF NOT EXISTS default_print_weight_mode  VARCHAR(10) DEFAULT 'gross',
    ADD COLUMN IF NOT EXISTS default_tare_rule_id       INTEGER REFERENCES tare_rules(id);

-- Migrate old label_template to new fields
UPDATE sales_order_lines SET
    default_branding_mode     = CASE WHEN label_template = 'distributor' THEN 'distributor' ELSE 'normal' END,
    default_print_weight_mode = CASE WHEN label_template = 'gross_net'   THEN 'net'         ELSE 'gross'  END
WHERE default_branding_mode IS NULL;

-- ── 4. NEW COLUMNS ON LABEL_PRINT_EVENTS ─────────────────────────────────
ALTER TABLE label_print_events
    ADD COLUMN IF NOT EXISTS label_type         VARCHAR(20)  DEFAULT 'roll',
    ADD COLUMN IF NOT EXISTS branding_mode      VARCHAR(20)  DEFAULT 'normal',
    ADD COLUMN IF NOT EXISTS print_weight_mode  VARCHAR(10)  DEFAULT 'gross',
    ADD COLUMN IF NOT EXISTS printed_weight_kg  NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS is_reprint         BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS backend_used       VARCHAR(50)  DEFAULT 'browser_preview';

UPDATE label_print_events
    SET is_reprint = (print_number > 1)
WHERE is_reprint = FALSE AND print_number > 1;

-- ── 5. NEW COLUMNS ON PALLETS ────────────────────────────────────────────
ALTER TABLE pallets
    ADD COLUMN IF NOT EXISTS total_gross_kg      NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS total_net_kg        NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS total_printed_kg    NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS pallet_weight_mode  VARCHAR(10) DEFAULT 'gross',
    ADD COLUMN IF NOT EXISTS closed_at           TIMESTAMP,
    ADD COLUMN IF NOT EXISTS closed_by_user_id   INTEGER REFERENCES users(user_id);

UPDATE pallets SET
    total_gross_kg   = COALESCE(total_kg, 0),
    total_net_kg     = COALESCE(total_kg, 0),
    total_printed_kg = COALESCE(total_kg, 0)
WHERE total_gross_kg IS NULL;

-- ── 6. STATIONS: add is_active ────────────────────────────────────────────
ALTER TABLE stations ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

-- ── 7. ITEM_CANCELLATIONS: add replacement_item_id ────────────────────────
ALTER TABLE item_cancellations
    ADD COLUMN IF NOT EXISTS replacement_item_id INTEGER REFERENCES items(item_id);

SELECT 'Migration 002 applied successfully' AS result;
