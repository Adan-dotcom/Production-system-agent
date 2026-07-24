-- Migration: Director corrections (2026-05-07)
-- Run this against your biotecnica_mvp PostgreSQL database.

-- 1. Add bobbin tare and label template columns to sales_order_lines
ALTER TABLE sales_order_lines
  ADD COLUMN IF NOT EXISTS bobbin_weight_kg NUMERIC(10,3),
  ADD COLUMN IF NOT EXISTS require_bobbin_tare BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS label_template VARCHAR(50) NOT NULL DEFAULT 'internal';

-- 2. Create item_cancellations table
CREATE TABLE IF NOT EXISTS item_cancellations (
  cancellation_id    SERIAL PRIMARY KEY,
  item_id            INTEGER NOT NULL REFERENCES items(item_id),
  cancelled_by_user_id INTEGER NOT NULL REFERENCES users(user_id),
  reason             TEXT NOT NULL,
  notes              TEXT,
  cancelled_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 3. Allow 'cancelled' as a valid item status
ALTER TABLE items DROP CONSTRAINT IF EXISTS items_status_check;
ALTER TABLE items ADD CONSTRAINT items_status_check
  CHECK (status IN ('active','blocked','completed','rejected','cancelled'));

-- Verify
SELECT 'Migration 001 applied successfully' AS result;
