SET client_encoding TO 'UTF8';
INSERT INTO stations (code, name, is_active)
VALUES ('extrusion', 'Extrusión', true)
ON CONFLICT (code) DO NOTHING;
