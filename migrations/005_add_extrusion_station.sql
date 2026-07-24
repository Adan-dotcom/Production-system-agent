-- Migration 005: Add extrusion as a real station
-- Extrusion is the first stage in the production flow, prior to corte.

INSERT INTO stations (code, name, is_active)
SELECT 'extrusion', 'Extrusión', true
WHERE NOT EXISTS (SELECT 1 FROM stations WHERE code = 'extrusion');
