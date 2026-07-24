# ===========================================================================
# Configura Mosquitto para el monitor de maquinas (ESP32 por WiFi/MQTT).
# REQUIERE ADMINISTRADOR (escribe en Program Files, reinicia servicio, firewall).
#
# Como correr:
#   PowerShell "Ejecutar como administrador", luego:
#     cd C:\Users\adan2\OneDrive\Escritorio\biotecnica_mvp
#     powershell -ExecutionPolicy Bypass -File deploy\setup_mosquitto.ps1
#
# Idempotente: se puede correr varias veces sin duplicar nada.
# NOTA: archivo en ASCII puro a proposito (PowerShell 5.1 lee .ps1 como ANSI).
# ===========================================================================
$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host "ERROR: corre esta ventana como Administrador." -ForegroundColor Red; exit 1 }

$conf = "C:\Program Files\mosquitto\mosquitto.conf"
if (-not (Test-Path $conf)) { Write-Host "ERROR: no existe $conf (Mosquitto instalado?)" -ForegroundColor Red; exit 1 }

# 1) Listener de red + acceso anonimo (LAN cerrada de planta) -- solo si falta
$content = Get-Content $conf -Raw
if ($content -match "(?m)^\s*listener\s+1883\s+0\.0\.0\.0") {
    Write-Host "= Config ya tenia el listener LAN, sin cambios." -ForegroundColor Yellow
} else {
    $block = "`r`n# Biotecnica: monitor de maquinas (ESP32 por WiFi/MQTT en LAN)`r`nlistener 1883 0.0.0.0`r`nallow_anonymous true`r`n"
    Add-Content -Path $conf -Value $block -Encoding ASCII
    Write-Host "+ Listener 1883 0.0.0.0 + allow_anonymous true agregados." -ForegroundColor Green
}

# 2) Regla de firewall para el puerto 1883 (inbound)
if (Get-NetFirewallRule -DisplayName "Mosquitto MQTT 1883" -ErrorAction SilentlyContinue) {
    Write-Host "= Regla de firewall ya existia." -ForegroundColor Yellow
} else {
    New-NetFirewallRule -DisplayName "Mosquitto MQTT 1883" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1883 | Out-Null
    Write-Host "+ Regla de firewall inbound TCP 1883 creada." -ForegroundColor Green
}

# 3) Reinicia el servicio
Restart-Service mosquitto
Start-Sleep -Seconds 2
Write-Host "+ Servicio mosquitto reiniciado." -ForegroundColor Green

# 4) Verifica que ahora escucha en 0.0.0.0
$listen = Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "=== Escuchando en 1883 ===" -ForegroundColor Cyan
$listen | Select-Object LocalAddress, LocalPort, State | Format-Table -AutoSize
if ($listen | Where-Object { $_.LocalAddress -eq "0.0.0.0" }) {
    Write-Host "LISTO: Mosquitto acepta conexiones de la LAN. Las ESP pueden conectarse." -ForegroundColor Green
} else {
    Write-Host "OJO: sigue solo en localhost, revisa el config." -ForegroundColor Red
}
