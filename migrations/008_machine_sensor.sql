-- ============================================================
-- Migration 008: Machine on/off monitor (Hall sensor + ESP32 + MQTT)
-- Run once with: python apply_migration.py migrations/008_machine_sensor.sql
-- ============================================================
-- Adds telemetry for the live machine on/off dashboard in /ui.
-- A Hall sensor on a roller of each machine generates pulses (1 neodymium
-- magnet → 1 pulse per revolution). An ESP32 publishes the per-second pulse
-- frequency over MQTT; the server infers girando/parado.
--
-- Telemetry is tied to the EXISTING machines table (no standalone entity).
-- It NEVER touches production columns/data. Safe to run multiple times.

-- ── 1. Live status: ONE row per sensorized machine (upserted on each message) ──
-- The dashboard reads this. The on/off state is INFERRED at read time from
-- last_freq_hz + staleness of last_pulse_at, so the threshold stays tunable.
CREATE TABLE IF NOT EXISTS machine_sensor_status (
    machine_id      INTEGER PRIMARY KEY REFERENCES machines(machine_id),
    last_freq_hz    NUMERIC(8,2) NOT NULL DEFAULT 0,   -- pulses/s from the ESP32
    magnets_per_rev SMALLINT     NOT NULL DEFAULT 1,    -- imanes en el rodillo (para RPM)
    is_running      BOOLEAN      NOT NULL DEFAULT FALSE, -- last ingest-time decision (freq>0)
    last_pulse_at   TIMESTAMP,                          -- last message received (UTC)
    updated_at      TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── 2. Transition log: append a row ONLY on a start/stop change ───────────────
-- Bounded by nature (one row per on→off / off→on flip, not per pulse), so it
-- answers "qué máquina trabajó y cuándo paró" without unbounded growth.
CREATE TABLE IF NOT EXISTS machine_sensor_events (
    event_id    SERIAL PRIMARY KEY,
    machine_id  INTEGER     NOT NULL REFERENCES machines(machine_id),
    event_type  VARCHAR(10) NOT NULL,                  -- 'start' | 'stop'
    freq_hz     NUMERIC(8,2) NOT NULL DEFAULT 0,
    occurred_at TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_machine_events_machine ON machine_sensor_events(machine_id);
CREATE INDEX IF NOT EXISTS idx_machine_events_at      ON machine_sensor_events(machine_id, occurred_at);
