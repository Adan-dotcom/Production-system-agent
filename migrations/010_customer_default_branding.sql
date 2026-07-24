-- Migration 010: marca a nivel CLIENTE para etiqueta de distribuidor por default.
-- Pocos clientes quieren etiqueta de distribuidor; el admin los designa en /ui y
-- al operador le aparece la casilla "Distribuidor" ya marcada para ese cliente,
-- sin tener que activarla en cada rollo.

ALTER TABLE clientes
    ADD COLUMN IF NOT EXISTS default_branding_mode VARCHAR(20) NOT NULL DEFAULT 'normal';

ALTER TABLE clientes DROP CONSTRAINT IF EXISTS clientes_default_branding_mode_check;
ALTER TABLE clientes
    ADD CONSTRAINT clientes_default_branding_mode_check
    CHECK (default_branding_mode IN ('normal','distributor'));
