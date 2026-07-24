-- Migration 004: add source column to aspel_excel_imports
-- Tracks whether a sync came from manual upload or the configured server path.

ALTER TABLE aspel_excel_imports
    ADD COLUMN IF NOT EXISTS source VARCHAR(30) DEFAULT 'upload_manual';

-- Back-fill existing rows
UPDATE aspel_excel_imports SET source = 'upload_manual' WHERE source IS NULL;
