-- Elimina el estado 'blocked' deprecado del constraint de items.
-- El flujo de bloqueo por peso fue deprecado; ningún endpoint lo asignaba.

ALTER TABLE items DROP CONSTRAINT IF EXISTS items_status_check;
ALTER TABLE items
    ADD CONSTRAINT items_status_check
    CHECK (status IN ('active','completed','rejected','cancelled','replaced','palletized'));
