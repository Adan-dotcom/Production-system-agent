# Monitor de Máquinas (on/off por sensor Hall + ESP32 + MQTT)

Dashboard en vivo en `/ui` → **🖥 Monitor de Máquinas**. Cada máquina con sensor
muestra una tarjeta verde (girando) / roja (parada) / gris (sin señal), con RPM y
tiempo en el estado actual. Se refresca solo cada 2 s y muestra una alerta si una
máquina lleva > 5 s detenida.

## Flujo de datos

```
Sensor Hall (rodillo, imán)  →  ESP32  →  WiFi/MQTT  →  Mosquitto (en el server)
                                                             │
                                       FastAPI (subscriber) ◄┘ →  PostgreSQL  →  /ui
```

- El ESP32 cuenta pulsos del Hall por interrupción, cada 1 s calcula la frecuencia
  (Hz = vueltas/s con 1 imán) y la **publica siempre** (incluso 0) en:
  `biotecnica/maquinas/{machine_id}/pulso`  payload `{"freq_hz": 12.5}`.
- El server (subscriber **paho-mqtt** en `app/services/mqtt_monitor.py`, arrancado en el
  lifespan) hace upsert del estado vivo y registra solo las transiciones start/stop.
  Usa el hilo propio de paho (NO asyncio) porque en Windows uvicorn corre el Proactor
  loop, donde los clientes MQTT async truenan con `NotImplementedError`.
- **La inferencia girando/parado/sin_señal la hace el server al leer** (`/monitor/status`),
  no el ESP. Umbral configurable en `.env` (`MACHINE_STALE_SECONDS`).
- El dashboard solo muestra máquinas que **ya reportaron** (tienen fila en
  `machine_sensor_status`). Una ESP nueva aparece sola en cuanto publica.

---

## ⚠️ Antes de nada: el server debe estar en UNA sola red

**El error #1 (nos pasó):** el PC server conectado a la **misma subred por dos
interfaces a la vez** (cable Ethernet + WiFi). Las ESP conectan al WiFi pero el MQTT
falla con `rc=-2` porque Windows responde por la interfaz equivocada (strong-host model)
y el handshake TCP nunca cierra.

**Regla de oro:** el server usa **una sola interfaz de red, con IP fija** (estática o
reservada en el router). Todas las ESP apuntan a esa IP. Si el server tiene cable y
WiFi en la misma red, **desconecta una**.

---

## Instalación en planta

### 1. Migración de base de datos (una vez)
Crea las tablas `machine_sensor_status` y `machine_sensor_events`:
```
.\venv\Scripts\python.exe apply_migration.py migrations\008_machine_sensor.sql
```

### 2. Dependencias del server (una vez)
```
.\venv\Scripts\python.exe -m pip install paho-mqtt pyserial
```
(`paho-mqtt` = cliente MQTT del subscriber; `pyserial` = solo para depurar ESP por COM.)

### 3. Broker MQTT (Mosquitto) — en el PC server, una vez
```
winget install EclipseFoundation.Mosquitto
```
(o descárgalo de https://mosquitto.org/download/ e instálalo como servicio.)

Luego, en **PowerShell como Administrador**, abre el broker a la LAN + firewall:
```
cd C:\...\biotecnica_mvp
powershell -ExecutionPolicy Bypass -File deploy\setup_mosquitto.ps1
```
El script agrega `listener 1883 0.0.0.0` + `allow_anonymous true` a `mosquitto.conf`,
crea la regla de firewall inbound TCP 1883 y reinicia el servicio. Es idempotente.
Debe terminar en verde: `LISTO: Mosquitto acepta conexiones de la LAN`.

> Acceso anónimo es válido para una red de planta cerrada. Si más adelante quieres
> usuario/contraseña, se agregan en `mosquitto.conf` y en el `.env` del server.

### 4. Config del server (`.env`)
```
MQTT_BROKER_HOST=localhost        # el broker vive en el mismo server
MQTT_BROKER_PORT=1883
MQTT_TOPIC_PREFIX=biotecnica/maquinas
MACHINE_STALE_SECONDS=6           # sin mensaje > 6 s → "sin señal"
```
> Si dejas `MQTT_BROKER_HOST` vacío, el monitor se desactiva y el server arranca igual.
> Al arrancar el server debe loguear: `[mqtt_monitor] iniciado contra localhost:1883`
> y luego `[mqtt_monitor] conectado al broker, suscrito a biotecnica/maquinas/+/pulso`.

### 5. Dar de alta las máquinas
En `/ui → ⚙ Máquinas`, da de alta cada máquina que tendrá sensor. **Anota el
`machine_id` de cada una** — es el número que va en el firmware. Si el ESP usa un
`machine_id` que no existe, el server **ignora** sus mensajes en silencio (es por la FK).

### 6. Flashear cada ESP32 (Arduino IDE)
Una sola vez por preparación de PC:
- **Boards:** Preferencias → URLs adicionales:
  `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
  → Gestor de tarjetas → instala **esp32 (Espressif)**.
- **Librería:** Gestionar librerías → instala **PubSubClient** (Nick O'Leary).

Por cada ESP, abre `rodillo/rodillo.ino` y edita arriba:
| Constante | Valor |
|---|---|
| `WIFI_SSID` / `WIFI_PASS` | tu WiFi (misma red que el server) |
| `MQTT_HOST` | **IP fija del server** |
| `MACHINE_ID` | el `machine_id` de la DB (único por ESP) |
| `MAGNETS` | déjalo en **1** (ver nota de imanes abajo) |

Sensor **Hall en GPIO 4 (D4)**. Selecciona Placa (ESP32 Dev Module) + el Puerto COM,
y sube. En el Monitor Serie (115200) debe verse `MQTT... conectado` y los publish
cada 1 s.

### 7. Verificar
Abre `/ui → 🖥 Monitor de Máquinas`, gira el rodillo (imán frente al Hall) → la
tarjeta pasa a verde con sus RPM.

---

## Agregar más ESP después
1. Da de alta la máquina en `/ui → ⚙ Máquinas` (si no existe) y anota su `machine_id`.
2. Flashea la ESP con ese `MACHINE_ID` (WiFi y `MQTT_HOST` iguales para todas).
3. Aparece sola en el dashboard. Sin tocar server ni código (el subscriber usa el
   comodín `biotecnica/maquinas/+/pulso`). Estado vivo = 1 fila/máquina (no crece);
   historial = solo transiciones (acotado). Escala a decenas de ESP sin cambios.

---

## Nota sobre los imanes (RPM)
`RPM = (pulsos contados en 1 s) × 60 / nº_imanes`. Con 1 imán, RPM = pulsos/s × 60.

El nº de imanes se puede dividir en **dos** lugares: `MAGNETS` en el firmware y
`magnets_per_rev` en la tabla `machine_sensor_status`. **Define el valor en UN solo
lado** o los RPM saldrán divididos dos veces. Recomendado: deja `MAGNETS = 1` en el
firmware (reporta pulsos/s crudos) y ajusta solo `magnets_per_rev` en la DB por máquina
— así se corrige sin re-flashear.

Con 1 imán la lectura **salta de 60 en 60 RPM** (cuenta pulsos enteros por segundo): es
normal. Para más resolución a baja velocidad, pon más imanes y sube `magnets_per_rev`.

---

## 🔧 Q&A — errores, causas y soluciones

### El Monitor Serie del ESP muestra `MQTT... fallo rc=-2`
`rc=-2` = **MQTT_CONNECT_FAILED**: no logra abrir el TCP al broker. Causas, de más a menos común:
1. **Server doble-homed** (cable + WiFi en la misma subred) → desconecta una interfaz; deja el server en una sola red con IP fija.
2. **`MQTT_HOST` mal o la IP del server cambió** (DHCP) → usa IP fija/reservada y confirma que coincide con el firmware.
3. **Mosquitto solo en localhost** → corre `deploy\setup_mosquitto.ps1` como admin (debe quedar escuchando en `0.0.0.0`).
4. **Firewall 1883 cerrado** → el mismo script abre la regla; verifica que exista.
5. **ESP en otra red/SSID** (p. ej. una red "guest" con aislamiento de clientes que bloquea PC↔dispositivo) → conéctala a la misma red que el server.

Códigos de `state()` de PubSubClient: `0` conectado · `-1` desconectado · `-2` connect failed (TCP) · `-3` connection lost · `-4` timeout · `1..5` rechazo de protocolo/credenciales.

### El dashboard dice "Ninguna máquina con sensor reportando todavía"
No está llegando nada al broker, **o** llega pero el `machine_id` no existe en `machines`.
Diagnóstico: escucha el broker (ver abajo). Si **no hay mensajes** → es problema de ESP/WiFi/broker (ver `rc=-2`). Si **sí hay mensajes** pero no aparece la tarjeta → el `MACHINE_ID` del firmware no está dado de alta en `/ui → ⚙ Máquinas` (el server lo ignora por la FK). Da de alta esa máquina y usa su id.

### El ESP conecta a WiFi pero la tarjeta sale gris "Sin señal"
La ESP dejó de publicar (se reinició, perdió WiFi) o pasaron más de `MACHINE_STALE_SECONDS` sin mensaje. Revisa el Monitor Serie de esa ESP; si no imprime publish cada 1 s, está caída o sin WiFi.

### La tarjeta está verde/roja pero no corresponde con la realidad
- **Siempre roja aunque gira:** el imán no pasa frente al Hall (distancia/alineación), o el Hall no está en **GPIO 4**, o el Hall es unipolar y el imán está con el polo equivocado.
- **RPM a la mitad / raro:** el nº de imanes se está dividiendo dos veces (firmware `MAGNETS` y DB `magnets_per_rev`). Deja uno en 1.
- **RPM salta de 60 en 60:** normal con 1 imán (resolución de la ventana de 1 s).

### El server arranca pero el monitor no funciona / no hay log `[mqtt_monitor] iniciado`
- `MQTT_BROKER_HOST` vacío en `.env` → ponlo (`localhost`).
- `paho-mqtt` no instalado → `pip install paho-mqtt`.
- Si ves `NotImplementedError` en `add_reader/add_writer`: alguien volvió a meter `aiomqtt`. El subscriber DEBE usar `paho-mqtt` (hilo propio) en Windows.

### `setup_mosquitto.ps1` da error de parser ("missing terminator")
Era una versión con caracteres no-ASCII (PowerShell 5.1 lee `.ps1` como ANSI). El script ya está en ASCII puro. Córrelo **como administrador**.

### `winget install ... Mosquitto` no instala / pide permisos
Abre la terminal **como administrador**, o instala desde https://mosquitto.org/download/.

### Dos ESP "se pelean" la misma tarjeta
Tienen el mismo `MACHINE_ID` → escriben sobre la misma máquina. Asigna ids **únicos**.

### No puedo leer el COM del ESP para depurar
El **Monitor Serie del Arduino IDE** tiene el puerto ocupado. Ciérralo antes de leerlo desde otra herramienta.

---

## Herramientas de diagnóstico

**Escuchar TODO lo que llega al broker** (corre en el server; necesita `paho-mqtt`):
```python
.\venv\Scripts\python.exe -c "import paho.mqtt.client as m,time; c=m.Client(m.CallbackAPIVersion.VERSION2); c.on_message=lambda cl,u,msg:print(msg.topic,msg.payload.decode()); c.on_connect=lambda cl,u,f,rc,p=None:cl.subscribe('#'); c.connect('localhost',1883); c.loop_start(); time.sleep(8)"
```
Si no imprime nada en 8 s, ninguna ESP está llegando al broker (problema de ESP/WiFi).

**Leer el Monitor Serie de una ESP por su COM** (necesita `pyserial`, y el Monitor del
Arduino cerrado):
```python
.\venv\Scripts\python.exe -c "import serial,time; s=serial.Serial('COM5',115200,timeout=1); t=time.time();
while time.time()-t<7: print(s.readline().decode('utf-8','replace').rstrip())"
```
Te dice si conectó a WiFi (`IP=...`) y a MQTT (`conectado` o `fallo rc=...`).

**Inyectar una máquina simulada** (sin tocar el hardware, para aislar si el problema es
server o ESP):
```
.\venv\Scripts\python.exe esp32_firmware\sim_maquina.py 1   # publica como la máquina 1
```
Si la tarjeta de la máquina 1 aparece y reacciona, entonces server+broker están bien y
el problema está en la ESP real.
