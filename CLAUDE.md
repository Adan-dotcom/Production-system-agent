# Biotécnica MVP — Contexto del Proyecto

## Stack
- FastAPI + SQLAlchemy 2.0 + PostgreSQL 17 (local)
- Jinja2 HTML para previews de etiquetas
- JWT auth (python-jose/bcrypt)
- openpyxl para leer Excel Aspel
- Python venv en `.\venv\`

## Base de datos
- DB: `biotecnica_mvp` | user: `postgres` | pw: `adansito` | host: `localhost` | port: 5432
- Migraciones aplicadas (001 → 007):
  - 001_director_corrections.sql
  - 002_major_improvements.sql
  - 003_aspel_excel.sql — tablas aspel_excel_imports, aspel_excel_raw_rows
  - 004_aspel_source.sql — columna `source` en imports (`upload_manual` / server path)
  - 005_add_extrusion_station.sql — estación `extrusion` (primera etapa, antes de corte)
  - 006_remove_blocked_status.sql — elimina status `blocked` del constraint de items
  - 007_analytics_agentic.sql — índices de performance + tablas de la capa agéntica (insights, recommendations, agent_runs)
  - 008_machine_sensor.sql — monitor on/off de máquinas (machine_sensor_status, machine_sensor_events)
- Aplicar migración: `.\venv\Scripts\python.exe apply_migration.py migrations/00X_*.sql`
- **NO recrear esquema. NO drop/recreate. NO Base.metadata.create_all como setup.**

## Arrancar el servidor
```
cd C:\Users\adan2\OneDrive\Escritorio\biotecnica_mvp
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
- Admin: usuario `admin`, contraseña `admin1234`
- UI operador: `http://localhost:8000/operator?station=corte`
- UI admin: `http://localhost:8000/ui`

## Estado actual (Junio 2026) — TODO COMPLETO + capa analítica/agéntica
✅ Backend completo: produce, cancel, reprint, pallets, tare rules, config
✅ Operator UI: registro, historial, reimprimir, cancelar, cancel-and-replace, almacén/tarimas
✅ Label preview: branding_mode (normal/distributor), autoprint USB
✅ Pallet label: summary_by_line agrupado
✅ Admin UI: máquinas, tare rules, estaciones operador, reportes visuales
✅ Migration 003 aplicada: tablas aspel_excel_imports, aspel_excel_raw_rows
✅ models.py: AspelExcelImport, AspelExcelRawRow, SalesOrder/Line con campos Aspel
✅ app/services/aspel_parser.py — detecta headers, normaliza ##, convierte fechas Excel
✅ app/routes/aspel_sync.py — POST /aspel/sync + GET /aspel/imports + GET /aspel/imports/{id}/rows
✅ app/main.py — todos los routers registrados (auth, items, alerts, labels, admin, config, imports, orders, pallets, reports, aspel_sync, analytics)
✅ app/routes/reports.py — GET /reports/by-pi, /by-operator, /by-shift
✅ Estación `extrusion` agregada (migr. 005) — flujo: extrusión → corte → ...
✅ Auto-sync: app/scheduler.py corre auto_sync_loop() en el lifespan (sync periódico del Excel)
✅ Capa analítica (CEO): app/routes/analytics.py + app/services/metrics.py — KPIs, timeseries, orders-at-risk, customers, products, quality, throughput, wip, cycle-time, anomalies, snapshot, datasets/export
✅ Capa agéntica: insights / recommendations / agent-runs (migr. 007). **Regla de oro: los agentes solo LEEN producción y ESCRIBEN en insights/recommendations; NUNCA mutan datos de producción.**
✅ app/routes/alerts.py — GET /alerts + POST /alerts/{id}/resolve

## Hardware y deploy (NO viven en el server)
- El server NO tiene hardware. La báscula + impresora viven en cada PC de operador.
- `scale_agent/` — agente Python que lee la báscula CH340 (COM6, 9600, formato `=NNNNN.N(kg)`) e imprime en la Zebra (USB001, no serial). Ver `scale_agent/README_SCALE.md`.
- `deploy/` — `setup_station.ps1` (provisiona estación), `wipe_data.py` (limpia data de prueba), `DEPLOY.md`, `CHECKLIST_PLANTA.md`.
✅ templates/index.html — pantalla "Sincronizar Excel Aspel" + Reportes con tablas visuales y barras de progreso

## Arquitectura clave

### Modelo de etiquetas
| Campo | Valores |
|-------|---------|
| `branding_mode` | `normal` / `distributor` |
| `print_weight_mode` | `gross` / `net` |
| `label_type` | `roll` / `pallet` |

### Tare (prioridad)
1. Manual override (bobbin_weight_kg en request)
2. tare_rule_id explícito
3. Default de la línea (sales_order_lines.default_tare_rule_id)
4. Legacy line.bobbin_weight_kg
5. Regla global default (tare_rules.is_default=True)
6. 0.0

### Excel Aspel — mapeo de columnas
| Excel | Campo DB |
|-------|----------|
| CVE_DOC | sae_order_number |
| NOMBRE CLIENTE | customer_name |
| CVE_CLPV | customer_code |
| CVE_ART | product_code |
| DESCR | product_description |
| STATUS | order_status |
| FECHA_DOC | order_date |
| FECHA_ENT | delivery_date |
| CANT | quantity_ordered (comercial, NO son kg) |
| KILOSxPARTIDA | target_kg (nullable, no bloquea operación) |
| CALLE | shipping_address |

### Reglas críticas del Excel
- CANT NO son kg — son unidades comerciales (piezas, millares, etc.)
- KILOSxPARTIDA puede estar vacío — NO bloquear operación si falta
- Idempotente: correr N veces sin duplicar — upsert por CVE_DOC + CVE_ART
- Registrar errores por fila sin detener la sincronización
- Guardar raw_data en aspel_excel_raw_rows para auditoría
- Detectar fila de encabezados buscando CVE_DOC/CVE_ART/DESCR
- Normalizar: trim, quitar espacios raros, convertir fechas Excel serial

## Endpoints principales
- POST /auth/select-operator — login operador
- POST /auth/login — login admin
- GET /auth/operators — lista operadores
- POST /items/produce — crear item
- GET /items/recent — historial operador
- POST /items/{code}/cancel — cancelar rollo
- POST /labels/{code}/print — imprimir
- POST /labels/{code}/reprint — reimprimir
- GET /labels/{code}/preview — HTML imprimible
- GET /pallets/open-for-order/{sae} — tarima abierta
- POST /pallets/create-for-order/{sae} — crear tarima
- POST /pallets/{code}/items — agregar rollo
- POST /pallets/{code}/close — cerrar tarima
- GET /config/machines — máquinas por estación
- GET /config/tare-rules — reglas de tara
- POST /config/tare-rules — crear regla
- GET /orders/by-sae/{sae} — pedido con líneas (incluye default_branding_mode, default_print_weight_mode)
- POST /aspel/sync — sincronizar Excel Aspel (admin only)
- GET /aspel/imports — historial de syncs
- GET /aspel/imports/{id}/rows — filas con error de un sync específico
- GET /reports/by-pi — progreso por pedido (kg neto vs target_kg, con líneas)
- GET /reports/by-operator — producción agrupada por operador
- GET /reports/by-shift — producción agrupada por turno T1/T2/T3
- GET /analytics/* — kpis, timeseries, orders-at-risk, customers, products, quality, throughput, wip, cycle-time, anomalies, aspel-quality, snapshot, datasets, export/{name}
- GET/POST /analytics/insights — insights de agentes (read prod, write insights)
- GET/POST /analytics/recommendations — recomendaciones de agentes
- GET/POST /analytics/agent-runs — registro de corridas de agentes
- GET /alerts — alertas activas | POST /alerts/{id}/resolve — resolver alerta
- GET /monitor/status — estado on/off en vivo de cada máquina sensorizada (girando/parado/sin_senal + RPM)
- GET /monitor/machines/{id}/history — últimas transiciones start/stop de una máquina

## Monitor de máquinas (on/off — sensor Hall + ESP32 + MQTT)
- Sensor Hall en un rodillo (1 imán = 1 pulso/vuelta) → ESP32 calcula freq/s → publica por MQTT.
- Broker Mosquitto en el server; subscriber `app/services/mqtt_monitor.py` (paho-mqtt, hilo propio — NO asyncio, porque en Windows uvicorn usa Proactor loop) arrancado en el lifespan.
- **La inferencia girando/parado/sin_senal la hace el SERVER al leer** `/monitor/status`, no el ESP. Umbral en `.env` (`MACHINE_STALE_SECONDS`).
- Datos atados a la tabla `machines` existente (FK). Estado vivo = 1 fila/máquina (upsert, no crece); historial = solo transiciones start/stop (acotado).
- Topic: `biotecnica/maquinas/{machine_id}/pulso` payload `{"freq_hz": float}`. machine_id debe existir en `machines`.
- Firmware + simulador + setup del broker en `esp32_firmware/` (rodillo.ino, sim_maquina.py, README_MONITOR.md).
- Dashboard en `/ui → 🖥 Monitor de Máquinas` (auto-refresh 2s, alerta si máquina >5s detenida).
- Si `MQTT_BROKER_HOST` está vacío en `.env`, el monitor se desactiva y el server arranca igual.

## Archivos clave
| Archivo | Descripción |
|---------|-------------|
| `app/models.py` | Todos los modelos SQLAlchemy |
| `app/routes/items.py` | Produce, cancel, recent |
| `app/routes/labels.py` | Print, reprint, preview |
| `app/routes/pallets.py` | CRUD tarimas |
| `app/routes/config.py` | Máquinas, tare rules |
| `app/routes/orders.py` | Pedidos + by-sae |
| `app/routes/reports.py` | Reportes y CSV export |
| `app/routes/monitor.py` | Monitor on/off de máquinas (GET status/history) |
| `app/services/mqtt_monitor.py` | Subscriber MQTT + ingesta de pulsos (lifespan) |
| `esp32_firmware/rodillo.ino` | Firmware ESP32 (Hall → MQTT) |
| `esp32_firmware/sim_maquina.py` | Simulador de ESP para probar sin hardware |
| `app/routes/auth.py` | Login, select-operator |
| `app/routes/imports.py` | CSV import (legacy — reemplazar) |
| `templates/operator.html` | UI operador |
| `templates/label_preview.html` | Preview etiqueta rollo |
| `templates/pallet_label_preview.html` | Preview etiqueta tarima |
| `templates/index.html` | UI admin |

## Reglas de negocio
- Cancelación: sin aprobación supervisor, sin restricción de fecha, solo bloqueada en tarima CERRADA
- Reimpresión: operador puede reimprimir, registra is_reprint=True
- roll_number = MAX(roll_number)+1 global
- barcode_value = "{sae_order_number}-{roll_number}"
- Status válidos: active, completed, rejected, cancelled, replaced, palletized (`blocked` eliminado en migr. 006)
- NO Excel como fuente operacional
- NO imprimir por red/IP — USB local
- NO requerir aprobación supervisor
