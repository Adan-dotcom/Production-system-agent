<#
  setup_station.ps1 — Configura UNA PC de operador en planta.

  Hace 4 cosas:
    1. Detecta el puerto COM de la báscula (CH340) y lo escribe en scale_config.json
    2. Registra el scale_agent para arrancar SOLO en cada inicio de sesión (tarea programada)
    3. Arranca el agente ahora y verifica /health + /weight
    4. Revisa que la impresora predeterminada NO sea "Microsoft Print to PDF"

  Correr en CADA PC de operador (no en el servidor), en PowerShell:
    .\deploy\setup_station.ps1 -ServerUrl "http://192.168.1.50:8000" -Station "corte"

  Parámetros:
    -ServerUrl  URL del backend en el servidor de oficina (con IP fija). Requerido.
    -Station    Estación de esta PC: corte | extrusion | impresion | bolseo | almacen | reciclaje
    -ComPort    (opcional) Forzar COM, ej "COM5". Si se omite, se autodetecta el CH340.
#>
param(
    [Parameter(Mandatory = $true)][string]$ServerUrl,
    [Parameter(Mandatory = $true)][string]$Station,
    [string]$ComPort = ""
)

$ErrorActionPreference = "Stop"
$ScaleDir = Join-Path (Split-Path $PSScriptRoot -Parent) "scale_agent"
$VenvPy   = Join-Path $ScaleDir "venv\Scripts\python.exe"
$ConfigF  = Join-Path $ScaleDir "scale_config.json"
$AgentUrl = "http://127.0.0.1:8787"

Write-Host "=== Setup estación Biotécnica ===" -ForegroundColor Cyan
Write-Host "Carpeta agente: $ScaleDir"

# ── 1. venv del agente ────────────────────────────────────────────────────
if (-not (Test-Path $VenvPy)) {
    Write-Host "[1/4] Creando venv del agente..." -ForegroundColor Yellow
    python -m venv (Join-Path $ScaleDir "venv")
    & $VenvPy -m pip install -q -r (Join-Path $ScaleDir "requirements.txt")
} else {
    Write-Host "[1/4] venv del agente OK"
}

# ── 2. Detectar puerto COM ────────────────────────────────────────────────
if ([string]::IsNullOrWhiteSpace($ComPort)) {
    Write-Host "[2/4] Autodetectando báscula (CH340)..."
    $ch340 = Get-CimInstance Win32_PnPEntity | Where-Object {
        $_.Name -match 'COM\d+' -and ($_.Name -match 'CH340' -or $_.PNPDeviceID -match 'VID_1A86')
    } | Select-Object -First 1
    if ($ch340 -and $ch340.Name -match '(COM\d+)') {
        $ComPort = $Matches[1]
        Write-Host "      Báscula detectada en $ComPort ($($ch340.Name))" -ForegroundColor Green
    } else {
        Write-Host "      No se detectó CH340. Puertos COM presentes:" -ForegroundColor Red
        Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'COM\d+' } | ForEach-Object { Write-Host "        - $($_.Name)" }
        Write-Host "      Vuelve a correr con -ComPort COMx, o conecta la báscula." -ForegroundColor Red
        exit 1
    }
}

# Escribir scale_config.json preservando los demás campos si ya existe
if (Test-Path $ConfigF) {
    $cfg = Get-Content $ConfigF -Raw | ConvertFrom-Json
} else {
    $cfg = [PSCustomObject]@{ baudrate = 9600; bytesize = 8; parity = "N"; stopbits = 1; timeout = 1; read_mode = "continuous"; command = $null; unit = "kg"; stability_window = 5; stability_tolerance_kg = 0.02; reconnect_seconds = 2 }
}
$cfg.port = $ComPort
$cfg | ConvertTo-Json | Out-File -FilePath $ConfigF -Encoding utf8
Write-Host "      scale_config.json -> port = $ComPort"

# ── 3. Tarea programada (autostart al iniciar sesión) ─────────────────────
Write-Host "[3/4] Registrando autostart del agente..."
$TaskName = "BiotecnicaScaleAgent"
$Action = New-ScheduledTaskAction -Execute $VenvPy `
    -Argument "-m uvicorn scale_agent:app --host 127.0.0.1 --port 8787" `
    -WorkingDirectory $ScaleDir
$Trigger  = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force -RunLevel Limited | Out-Null
Write-Host "      Tarea '$TaskName' registrada (arranca al iniciar sesión)."

# Arrancar ahora (matar instancia previa para evitar choque en el COM)
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -eq $VenvPy
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName

# Esperar a que /health responda (hasta ~10s)
$ok = $false
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod -Uri "$AgentUrl/health" -TimeoutSec 2
        if ($h.ok) { $ok = $true; break }
    } catch { }
}
if ($ok) {
    $w = Invoke-RestMethod -Uri "$AgentUrl/weight" -TimeoutSec 2
    Write-Host "      Agente vivo. connected=$($w.connected) port=$($w.port) weight_kg=$($w.weight_kg)" -ForegroundColor Green
    if (-not $w.connected) {
        Write-Host "      OJO: agente arriba pero báscula NO conectada (revisa cable/COM/error: $($w.error))" -ForegroundColor Yellow
    }
} else {
    Write-Host "      ERROR: el agente no respondió en 127.0.0.1:8787" -ForegroundColor Red
}

# ── 4. Impresora predeterminada ───────────────────────────────────────────
Write-Host "[4/4] Revisando impresora predeterminada..."
$def = Get-CimInstance Win32_Printer -Filter "Default=True"
if ($def) {
    if ($def.Name -match 'PDF|OneNote|Fax|XPS') {
        Write-Host "      ADVERTENCIA: la predeterminada es '$($def.Name)'." -ForegroundColor Red
        Write-Host "      Las etiquetas se imprimen a la predeterminada. Pon la Zebra/Godex como default:" -ForegroundColor Red
        Write-Host "      Configuración > Bluetooth y dispositivos > Impresoras > [tu impresora] > Predeterminada" -ForegroundColor Red
    } else {
        Write-Host "      Predeterminada: $($def.Name)" -ForegroundColor Green
    }
} else {
    Write-Host "      No hay impresora predeterminada configurada." -ForegroundColor Red
}

# ── Resumen ───────────────────────────────────────────────────────────────
$opUrl = "$ServerUrl/operator?station=$Station"
Write-Host ""
Write-Host "=== Estación lista ===" -ForegroundColor Cyan
Write-Host "Abre el navegador (Chrome/Edge) en:" -ForegroundColor White
Write-Host "    $opUrl" -ForegroundColor Green
Write-Host "Crea un acceso directo a esa URL en el escritorio para el operador."
