# Agente Local de Báscula — Biotécnica MVP

El `scale_agent` corre en **cada PC de estación** y expone el peso de la báscula via HTTP local (`http://127.0.0.1:8787`). El operador puede copiar el peso al campo de registro con un botón, pero el registro sigue siendo manual (el operador siempre confirma con REGISTRAR).

---

## Instalación en PC de estación

1. **Conectar la báscula** por USB/serial al puerto COM de la computadora.
2. Abrir **Administrador de dispositivos** → Puertos (COM y LPT) → identificar el COM asignado, ej. `COM3` o `COM5`.
3. Asegurarse de tener **Python 3.9+** instalado y en el PATH.
4. Copiar la carpeta `scale_agent/` a la PC de estación (USB, red compartida, etc.).
5. Hacer **doble clic** en `run_scale_agent.bat`.  
   El .bat crea el venv, instala dependencias y arranca el agente automáticamente.
6. Dejar la ventana de CMD abierta — muestra los logs en tiempo real.

---

## Configurar el puerto COM desde la UI

1. Abrir el navegador en la PC de estación.
2. Ir a la URL del servidor, ej. `http://192.168.1.100:8000/operator?station=corte`.
3. En la tarjeta **"⚖ Báscula local"** (aparece debajo de Operador / Máquina):
   - Escribir el COM en el campo **Puerto** (ej. `COM5`).
   - Verificar el **Baudrate** (por defecto `9600`; ajustar según manual de la báscula).
   - Presionar **Guardar**.
4. El estado debe cambiar a **Conectada**.

### Alternativa: editar scale_config.json directamente

Copiar `scale_config.json.example` como `scale_config.json` y editar:

```json
{
  "port": "COM5",
  "baudrate": 9600,
  "bytesize": 8,
  "parity": "N",
  "stopbits": 1,
  "timeout": 1,
  "unit": "kg",
  "stability_window": 5,
  "stability_tolerance_kg": 0.02,
  "reconnect_seconds": 2
}
```

Reiniciar el agente después de editar el archivo.

---

## Flujo de uso

1. El operador busca PI/PAY y selecciona operador/máquina como siempre.
2. La tarjeta de báscula muestra el peso en tiempo real.
3. Cuando el peso se estabiliza, el operador presiona **"⚖ Usar peso de báscula"**.
4. El peso se copia automáticamente al campo de peso bruto.
5. El operador presiona **REGISTRAR** normalmente.

> El registro automático NO existe — el operador siempre confirma.

---

## Endpoints del agente

| Método | URL | Descripción |
|--------|-----|-------------|
| GET | `/health` | Estado del agente y conexión |
| GET | `/ports` | Lista de puertos COM disponibles |
| GET | `/weight` | Lectura actual (peso, estabilidad, raw, simulation) |
| POST | `/config` | Cambiar puerto, baudrate u otras opciones |
| POST | `/debug/simulate-weight` | **Solo para pruebas** — inyectar peso sin hardware |

### Ejemplo `/weight`

```json
{
  "ok": true,
  "connected": true,
  "weight_kg": 12.345,
  "stable": true,
  "raw": "ST,GS,+0012.345 kg",
  "port": "COM5",
  "baudrate": 9600,
  "age_ms": 183,
  "error": null
}
```

---

## Probar sin báscula física — modo simulación

El agente incluye un endpoint de debug para inyectar lecturas sin necesidad de hardware:

### Paso a paso

1. Iniciar el agente: `run_scale_agent.bat`
2. Abrir `http://127.0.0.1:8787/health` — debe responder `connected: false, error: "No hay puerto configurado"`.
3. Abrir la UI del operador, ej. `http://192.168.1.100:8000/operator?station=corte`.  
   La tarjeta muestra **"No hay puerto configurado"** — el formulario sigue funcionando normal.
4. Inyectar un peso simulado con curl o cualquier cliente HTTP:

```bash
curl -X POST http://127.0.0.1:8787/debug/simulate-weight \
     -H "Content-Type: application/json" \
     -d '{"weight_kg": 12.345, "stable": true, "raw": "SIM,ST,+0012.345 kg"}'
```

O con PowerShell:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8787/debug/simulate-weight `
  -Method POST -ContentType "application/json" `
  -Body '{"weight_kg": 12.345, "stable": true, "raw": "SIM,ST,+0012.345 kg"}'
```

5. La UI debe mostrar inmediatamente **12.345 kg** con estado **✓ Estable**.
6. Presionar **"⚖ Usar peso de báscula"** → el campo `#gwInput` (Peso bruto) queda en `12.345`.
7. `checkReady()` reacciona: si operador, máquina, PI/PAY y destino ya están seleccionados, el botón **REGISTRAR** se habilita.
8. Presionar **REGISTRAR** → hace `POST /items/produce` exactamente igual que antes.

### Respuesta de `/weight` en modo simulación

```json
{
  "ok": true,
  "connected": true,
  "weight_kg": 12.345,
  "stable": true,
  "raw": "SIM,ST,+0012.345 kg",
  "port": null,
  "baudrate": 9600,
  "age_ms": 42,
  "error": null,
  "simulation": true
}
```

El campo `simulation: true` indica que el peso es inyectado, no leído de hardware.  
Se limpia automáticamente al conectar un puerto real o al llamar `POST /config`.

---

## Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| "Agente local no disponible" | `run_scale_agent.bat` no está corriendo | Iniciarlo con doble clic |
| "Puerto no encontrado" | COM incorrecto o driver no instalado | Verificar en Adm. de dispositivos |
| "Access denied / port busy" | Otro programa (ej. PuTTY, otro agente) usa el COM | Cerrar el otro programa |
| "Peso no cambia" | Baudrate incorrecto; báscula solo manda al presionar PRINT | Revisar baudrate en manual; o usar modo `command` si la báscula lo requiere |
| "Peso raro" (ej. x1000) | Unidad incorrecta: la báscula manda en gramos | Cambiar `"unit": "g"` en config |
| "La UI no llena peso" | Fetch a 127.0.0.1:8787 bloqueado | Verificar que el agente corre; revisar consola del navegador (F12) |
| "Peso inestable: verifica" | Báscula oscilando — peso no estabilizado | Esperar a que se estabilice antes de presionar "Usar peso" |

---

## Formatos seriales soportados

El parser extrae automáticamente el número y unidad de los siguientes formatos:

```
ST,GS,+0012.345 kg     → 12.345 kg (estable)
US,GS,+0012.345 kg     → 12.345 kg (inestable)
ST,NT, 12.345kg        → 12.345 kg (estable)
+12.345 kg             → 12.345 kg
-0.002 kg              → -0.002 kg
12.345                 → 12.345 kg (asume kg)
PESO: 12.345 kg        → 12.345 kg
001234 g               → 1.234 kg
27.20 lb               → 12.361 kg
```

---

## Notas de seguridad

- El agente corre **solo en loopback** (`127.0.0.1`) — no es accesible desde la red.
- No tiene autenticación porque es un proceso local de la PC.
- No comparte ni usa tokens JWT del backend central.
- CORS abierto (`*`) es necesario porque la HTML viene del servidor central pero llama a `127.0.0.1:8787` en la PC local.
