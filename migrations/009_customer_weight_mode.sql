-- ============================================================
-- Migration 009: Peso bruto/neto por cliente (ya no lo elige el operador)
-- Run once with: python apply_migration.py migrations/009_customer_weight_mode.sql
-- ============================================================
-- Antes el operador podía elegir Bruto/Neto en cada rollo (radio button en
-- /operator). Si por error seleccionaba "Neto" en un cliente que SÍ se le
-- cobra la tara (bobina), la etiqueta salía con el peso neto y esa bobina
-- nunca se cobraba. Ahora es un default por CLIENTE que solo el admin
-- configura (son solo 2 clientes a los que no se les cobra tara); el
-- servidor lo aplica solo, sin que el operador pueda tocarlo.
-- Mismo patrón que default_branding_mode (clientes.default_branding_mode).
-- Safe to run multiple times.

ALTER TABLE clientes
    ADD COLUMN IF NOT EXISTS default_print_weight_mode VARCHAR(10) NOT NULL DEFAULT 'gross';
