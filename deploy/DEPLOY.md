# Deploy Biotécnica MVP — Guía para arrancar YA

## Arquitectura (lo que falló la otra vez)

Hay **piezas en máquinas distintas**. El hardware NO vive en el servidor:

```
  PC SERVIDOR (oficina)                 PC OPERADOR (planta) — una por estación
  ┌─────────────────────────┐          ┌──────────────────────────────────┐
  │ Backend FastAPI :8000    │  LAN     │ Navegador → /operator            │
  │ PostgreSQL :5432         │◄────────►│ scale_agent :8787 (lee báscula)  │
  │ Mosquitto MQTT :1883     │          │ Impresora USB (= predeterminada) │
  │ (NO báscula/impresora)   │          └──────────────────────────────────┘
  └───────────▲─────────────┘
              │ WiFi/MQTT          ESP32 en cada MÁQUINA (no en la PC del operador)
              └───────────────────┤ Sensor Hall en un rodillo → ESP32 → publica RPM
                                   └────────────────────────────────────────────────
```

- El navegador del operador lee la báscula vía `http://127.0.0.1:8787` = **su propia PC**.
- Las etiquetas se imprimen con `window.print()` a la **impresora predeterminada de Windows de la PC del operador**.
- Por eso el `scale_agent` y la impresora deben estar **en cada PC de planta**, no en el servidor.
- **Monitor de máquinas (opcional):** un **ESP32 por máquina** lee un sensor Hall y publica
  por WiFi al broker **Mosquitto que corre en el server**. No usa las PC de operador. Detalle
  completo en `esp32_firmware/README_MONITOR.md`; resumen en la **Parte D**.

---

## A) PC SERVIDOR (oficina) — una sola vez

1. **IP fija + UNA sola red.** Asigna IP estática a esta PC (ej. `192.168.1.50`). Anótala.
   ⚠️ **No conectes el server a la misma red por dos interfaces a la vez** (cable + WiFi en
   la misma subred). Eso rompe el MQTT de las ESP (`rc=-2`) por el strong-host model de
   Windows. Una sola interfaz de red activa hacia la LAN de planta.
2. **Arrancar backend escuchando en la red** (no solo localhost):
   ```
   cd C:\...\biotecnica_mvp
   .\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   (sin `--reload` en producción). Para que arranque solo al boot, registra una tarea programada igual que en la estación.
3. **Firewall:** permitir el puerto entrante:
   ```powershell
   New-NetFirewallRule -DisplayName "Biotecnica Backend 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```
4. **Verificar desde otra PC de la LAN:** abrir `http://192.168.1.50:8000/ui` → debe cargar el panel admin.

> Nota de puerto: este proyecto a veces corrió en **8013**. Usa el que arranques; mantén consistencia con la URL que das a las estaciones.

---

## B) CADA PC DE OPERADOR (planta) — por estación

Requisitos: Python 3.9+ instalado, báscula y la impresora conectadas por USB.

1. Copiar la carpeta `scale_agent\` (o el repo completo) a la PC.
2. Conectar báscula (CH340) e impresora.
3. **Poner la impresora de etiquetas como PREDETERMINADA de Windows** (NO "Microsoft Print to PDF").
4. Correr el setup (ajusta IP y estación):
   ```powershell
   .\deploy\setup_station.ps1 -ServerUrl "http://192.168.1.50:8000" -Station "corte"
   ```
   El script: detecta el COM de la báscula, lo escribe en `scale_config.json`, registra el agente para **autostart al iniciar sesión**, lo arranca y verifica `/health` + `/weight`, y avisa si la impresora predeterminada está mal.
5. Crear acceso directo en el escritorio a `http://192.168.1.50:8000/operator?station=corte`.

Estaciones válidas: `corte`, `extrusion`, `impresion`, `bolseo`, `almacen`, `reciclaje`.

---

## C) Dar de alta operadores y máquinas (desde /ui, sesión admin)

La base quedó en blanco salvo el usuario **admin** y las estaciones. Antes de producir:

1. Entrar a `http://SERVIDOR:8000/ui` → login admin (`admin` / contraseña).
2. Pantalla **Usuarios** → crear cada operador (rol `operator` → aparece en el dropdown de planta).
3. Pantalla **Máquinas** → dar de alta las máquinas de cada estación.
4. (Opcional) Pantalla de **tare rules** si usan tara por regla; si no, la tara cae a 0 o al override manual.

---

## D) Monitor de máquinas (on/off por ESP32) — opcional

Guía completa y Q&A en `esp32_firmware/README_MONITOR.md`. Resumen del deploy:

**En el server (una vez):**
1. Aplicar migración: `.\venv\Scripts\python.exe apply_migration.py migrations\008_machine_sensor.sql`
2. Dependencias: `.\venv\Scripts\python.exe -m pip install paho-mqtt pyserial`
3. Instalar broker: `winget install EclipseFoundation.Mosquitto`
4. Abrir broker a la LAN + firewall (**PowerShell como admin**):
   `powershell -ExecutionPolicy Bypass -File deploy\setup_mosquitto.ps1` → debe terminar en `LISTO`.
5. `.env`: `MQTT_BROKER_HOST=localhost`, `MQTT_BROKER_PORT=1883`,
   `MQTT_TOPIC_PREFIX=biotecnica/maquinas`, `MACHINE_STALE_SECONDS=6`.
6. Reiniciar el backend; debe loguear `[mqtt_monitor] conectado al broker`.

**Por cada máquina con sensor:**
7. Dar de alta la máquina en `/ui → ⚙ Máquinas` y **anotar su `machine_id`**.
8. Flashear el ESP32 (`rodillo/rodillo.ino`, Arduino IDE) con: WiFi, `MQTT_HOST` = IP fija
   del server, `MACHINE_ID` = el id de la DB (único por ESP). Hall en **GPIO 4**.
9. Ver `/ui → 🖥 Monitor de Máquinas`: la tarjeta aparece sola al girar el rodillo.

> El `MACHINE_ID` **debe existir** en `machines` o el server ignora los mensajes en silencio
> (es por la FK). Agregar más ESP después = solo flashear con su id; aparecen solas.

---

## Gotchas (de experiencias previas)

- **Server doble-homed mata el monitor:** cable + WiFi en la misma subred → las ESP dan `rc=-2`
  (no abren TCP al broker; Windows responde por la interfaz equivocada). **Una sola red en el
  server.** Si pasa, desconecta una interfaz o ponle IP fija única.
- **Monitor MQTT usa `paho-mqtt`, NO `aiomqtt`:** en Windows uvicorn corre el Proactor loop y
  los clientes MQTT async truenan con `NotImplementedError`. paho corre en su propio hilo.
- **`machine_id` inexistente = tarjeta nunca aparece:** el server descarta en silencio mensajes
  de máquinas no dadas de alta (FK).

- **Chrome Private Network Access:** la página viene de `http://IP:8000` y llama a `127.0.0.1:8787`. El agente ya manda el header PNA + CORS. Usa Chrome/Edge actuales. Si ves errores CORS/PNA en consola, es esto.
- **Trampa del CH340 (error 31 / device not functioning):** si dos procesos abren el COM a la vez, la báscula se cuelga y no se recupera con reintentos. Regla: **un solo agente por PC**. Fix si pasa (admin):
  ```
  pnputil /restart-device "USB\VID_1A86&PID_7523\..."
  ```
- **Cada PC = su propio COM.** No copies `scale_config.json` con COM6 a ciegas; el setup lo autodetecta.
- **Zebra en pausa:** LED verde parpadeando lento = pausada (acepta bytes, no imprime). Quitar pausa con el botón.
- **Impresión agnóstica:** el sistema NO manda ZPL/EZPL; rasteriza HTML a la predeterminada. Cambiar de impresora = cambiar la predeterminada de Windows, cero código.

---

## Reset a blanco (si necesitas repetir)

```
.\venv\Scripts\python.exe deploy\wipe_data.py
```
Conserva solo `admin` + estaciones. Pide confirmación (escribe `BORRAR`). Con `--yes` corre desatendido. **Irreversible, sin backup.**
