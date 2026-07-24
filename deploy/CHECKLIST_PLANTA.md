# ✅ Checklist de instalación en planta — Biotécnica MVP

> Guía de campo para quien haga el montaje (aunque no sea el dev).
> Acompaña a `DEPLOY.md` (ahí está el detalle técnico). Aquí van los pasos en orden, para tachar.
> Tiempo estimado: servidor ~20 min · cada estación ~10 min.

---

## 🧠 Lo único que tienes que entender antes de empezar

Son **dos tipos de máquina**. El hardware **NO va en el servidor**:

```
  PC SERVIDOR (oficina)            PC OPERADOR (planta) — una por estación
  ┌────────────────────┐   LAN    ┌──────────────────────────────────┐
  │ Backend + Base de   │◄──────►│ Navegador → /operator            │
  │ datos. SIN báscula  │         │ scale_agent (lee la báscula)     │
  │ ni impresora.       │         │ Impresora USB = PREDETERMINADA   │
  └────────────────────┘          └──────────────────────────────────┘
```

- La báscula y la impresora van en **cada PC de planta**, conectadas por **USB**.
- El navegador del operador lee la báscula de **su propia PC** (`127.0.0.1`), e imprime a la **impresora predeterminada de Windows de esa PC**.

---

## 📦 Material que debes llevar

- [ ] 1 PC servidor (Windows) con el proyecto copiado e IP de red disponible
- [ ] 1 PC por estación de planta (Windows 10/11, con Python 3.9+ y Chrome o Edge)
- [ ] 1 báscula por estación con su **adaptador USB-Serial CH340** + cable USB
- [ ] 1 impresora de etiquetas por estación + cable USB
- [ ] Cables de red / acceso a la misma LAN para todas las PCs
- [ ] Usuario administrador de Windows en cada PC (para firewall y tarea programada)
- [ ] *(Monitor de máquinas, opcional)* 1 **ESP32 + sensor Hall + imán** por máquina a monitorear, y WiFi que alcance al server

---

## PARTE A — PC SERVIDOR (oficina) · se hace UNA vez

- [ ] **A1.** Asignar **IP fija** a la PC servidor. Anótala aquí: `192.168.____.____`
- [ ] **A2.** Abrir el puerto en el firewall (PowerShell **como administrador**):
  ```powershell
  New-NetFirewallRule -DisplayName "Biotecnica Backend 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
  ```
- [ ] **A3.** Arrancar el backend escuchando en la red (sin `--reload`):
  ```
  cd C:\...\biotecnica_mvp
  .\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  ```
  > Para que arranque solo al prender la PC, registrar una tarea programada igual que la del agente (ver DEPLOY.md).
- [ ] **A4.** Desde **otra PC de la LAN**, abrir `http://<IP-servidor>:8000/ui` → debe cargar el panel admin.
- [ ] **A5.** (Si es instalación nueva) Dejar la base limpia:
  ```
  .\venv\Scripts\python.exe deploy\wipe_data.py
  ```
  Conserva solo el usuario `admin` y las estaciones. Pide escribir `BORRAR`. **Irreversible.**

> ⚠️ Usa el **mismo puerto** (8000 ó el que elijas) en TODAS las URLs que des a las estaciones.

---

## PARTE B — CADA PC DE OPERADOR (planta) · repetir por estación

> Estación = una de: `corte` · `extrusion` · `impresion` · `bolseo` · `almacen` · `reciclaje`

- [ ] **B1.** Conectar la **báscula (USB/CH340)** y la **impresora (USB)** a la PC.
- [ ] **B2.** Encender la impresora y poner papel/etiquetas. Si es Zebra y el **LED verde parpadea lento = está EN PAUSA** → quitar pausa con el botón.
- [ ] **B3.** Poner la impresora de etiquetas como **PREDETERMINADA de Windows**
      (Configuración > Bluetooth y dispositivos > Impresoras > [tu impresora] > Predeterminar).
      **NO** dejar "Microsoft Print to PDF".
- [ ] **B4.** Copiar el proyecto (o al menos la carpeta `scale_agent\`) a la PC.
      ⚠️ Si copiaste de otra estación, **borra el `scale_config.json`** que venga — cada PC tiene su propio COM.
- [ ] **B5.** Correr el setup en PowerShell (ajusta IP y estación):
  ```powershell
  .\deploy\setup_station.ps1 -ServerUrl "http://<IP-servidor>:8000" -Station "corte"
  ```
  El script: detecta el COM de la báscula, lo guarda, registra el agente en **autostart al iniciar sesión**, lo arranca y verifica `/health` + `/weight`, y avisa si la impresora predeterminada está mal.
- [ ] **B6.** Confirmar en la salida del script: `Agente vivo. connected=True ... weight_kg=<algo>`.
      Si dice `connected=False` → revisar cable/COM (ver tabla de problemas).
- [ ] **B7.** Crear un **acceso directo en el escritorio** a:
      `http://<IP-servidor>:8000/operator?station=corte`
- [ ] **B8.** Abrir esa URL en **Chrome/Edge**, seleccionar operador, y hacer una **prueba real**: poner peso en la báscula → debe verse el peso vivo → registrar un rollo → debe imprimir la etiqueta.

---

## PARTE C — Dar de alta operadores y máquinas (desde `/ui`, sesión admin)

- [ ] **C1.** Entrar a `http://<IP-servidor>:8000/ui` → login `admin`.
- [ ] **C2.** Pantalla **Usuarios** → crear cada operador (rol `operator`; así aparece en el menú de planta).
- [ ] **C3.** Pantalla **Máquinas** → dar de alta las máquinas de cada estación.
- [ ] **C4.** (Opcional) **Reglas de tara** si usan tara por regla; si no, la tara queda en 0 o se mete manual.
- [ ] **C5.** (Opcional) Sincronizar el **Excel de Aspel** para cargar los pedidos PI (pantalla de sincronización).

---

## PARTE D — Monitor de máquinas (opcional, ESP32 por máquina)

> Detalle y solución de problemas en `esp32_firmware/README_MONITOR.md`.
> ⚠️ El server debe estar en **una sola red con IP fija** (cable + WiFi en la misma subred rompe el MQTT de las ESP).

**En el server (una vez):**
- [ ] **D1.** Migración: `.\venv\Scripts\python.exe apply_migration.py migrations\008_machine_sensor.sql`
- [ ] **D2.** Dependencias: `.\venv\Scripts\python.exe -m pip install paho-mqtt pyserial`
- [ ] **D3.** Broker: `winget install EclipseFoundation.Mosquitto`
- [ ] **D4.** Abrir broker a la LAN (**PowerShell admin**): `powershell -ExecutionPolicy Bypass -File deploy\setup_mosquitto.ps1` → debe decir `LISTO`.
- [ ] **D5.** `.env`: `MQTT_BROKER_HOST=localhost`, `MQTT_BROKER_PORT=1883`, `MQTT_TOPIC_PREFIX=biotecnica/maquinas`, `MACHINE_STALE_SECONDS=6`. Reiniciar backend → debe loguear `[mqtt_monitor] conectado al broker`.

**Por cada máquina con sensor:**
- [ ] **D6.** Dar de alta la máquina en `/ui → ⚙ Máquinas` y **anotar su `machine_id`**.
- [ ] **D7.** Flashear el ESP32 (`rodillo/rodillo.ino`, Arduino IDE): WiFi, `MQTT_HOST` = IP fija del server, `MACHINE_ID` = id de la DB (único por ESP), Hall en **GPIO 4**.
- [ ] **D8.** Verificar en `/ui → 🖥 Monitor de Máquinas`: la tarjeta aparece y se pone verde al girar el rodillo.

---

## 🔧 Si algo falla — tabla rápida

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `/ui` no carga desde otra PC | Firewall o IP mal | Repetir A2; verificar IP fija (A1); ping al servidor |
| La página del operador carga pero **no lee la báscula** | Agente caído o COM mal | Ver `http://127.0.0.1:8787/health` en esa PC; revisar cable; reabrir tarea `BiotecnicaScaleAgent` |
| Error **CORS / Private Network** en consola del navegador | Navegador viejo | Usar Chrome/Edge actuales (el agente ya manda los headers) |
| Báscula colgada: **error 31 / "device not functioning"** | Se abrió el COM 2 veces o se mató el agente a la fuerza | Reset del dispositivo (admin): `pnputil /restart-device "USB\VID_1A86&PID_7523\..."` y reiniciar el agente. **Nunca correr 2 agentes ni otros scripts sobre el mismo COM.** |
| Imprime al lugar equivocado / sale PDF | Impresora predeterminada incorrecta | Repetir B3: poner la de etiquetas como predeterminada |
| Zebra acepta pero no imprime | Está EN PAUSA (LED verde lento) | Quitar pausa con el botón |
| Operador no aparece en el menú | No se creó con rol `operator` | Repetir C2 |
| ESP: Monitor Serie dice `MQTT... fallo rc=-2` | Server doble-homed, IP mal, broker en localhost o firewall | Una sola red en server (A1); confirmar `MQTT_HOST`; correr `setup_mosquitto.ps1` admin (D4) |
| Dashboard: "Ninguna máquina con sensor reportando" | No llega nada al broker, **o** `machine_id` no existe en `machines` | Si no llega nada → ver `rc=-2`. Si llega → dar de alta esa máquina (D6) |
| Tarjeta gris "Sin señal" | La ESP dejó de publicar / perdió WiFi | Revisar Monitor Serie de esa ESP (debe publicar cada 1 s) |
| Tarjeta roja aunque la máquina gira | Imán no pasa frente al Hall, o Hall no en GPIO 4 | Acercar/alinear imán; verificar pin D4 |
| RPM sale a la mitad | Nº de imanes dividido dos veces (firmware + DB) | Dejar `MAGNETS=1` en firmware, ajustar solo `magnets_per_rev` en DB |

---

## 📞 Reglas de oro (para no romper nada)

1. **Un solo agente de báscula por PC.** No lo lances a mano si la tarea programada ya lo levantó.
2. **No copies el `scale_config.json` entre PCs** — cada una detecta su propio COM con el setup.
3. **No corras scripts de prueba que abran el COM** mientras el agente está corriendo.
4. **La impresora se cambia sin tocar código**: basta cambiar la predeterminada de Windows.
5. **Mismo puerto** del backend en todas las URLs de las estaciones.
6. **Server en UNA sola red con IP fija.** Cable + WiFi en la misma subred rompe el MQTT de las ESP (`rc=-2`).
