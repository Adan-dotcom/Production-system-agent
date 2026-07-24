-- Migration 009: incluir 'extrusion' en los CHECK constraints de etapa.
-- La migración 005 agregó la ESTACIÓN extrusion pero olvidó actualizar los
-- constraints de items.current_stage y stage_events.to_stage, por lo que
-- producir un rollo en extrusión (primera etapa del flujo) fallaba con
-- CheckViolation. Esto los alinea con VALID_STAGES del backend.

ALTER TABLE items DROP CONSTRAINT IF EXISTS items_current_stage_check;
ALTER TABLE items
    ADD CONSTRAINT items_current_stage_check
    CHECK (current_stage IN ('extrusion','corte','impresion','bolseo','almacen','reciclaje'));

ALTER TABLE stage_events DROP CONSTRAINT IF EXISTS stage_events_to_stage_check;
ALTER TABLE stage_events
    ADD CONSTRAINT stage_events_to_stage_check
    CHECK (to_stage IN ('extrusion','corte','impresion','bolseo','almacen','reciclaje'));
