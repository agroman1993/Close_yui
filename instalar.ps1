# Instalador de Close Yui (Windows).
# Doble click en instalar.bat o: powershell -ExecutionPolicy Bypass -File instalar.ps1
#
# Hace tres cosas: comprueba Python, instala la unica dependencia (Pillow)
# y crea config.json con tus respuestas. No toca nada fuera de esta carpeta.

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $raiz

Write-Host ""
Write-Host "=== Close Yui: instalacion ===" -ForegroundColor Cyan

# --- 1. Python ---------------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Falta Python en el PATH." -ForegroundColor Red
    Write-Host "Instalalo desde https://www.python.org/downloads/ marcando 'Add python.exe to PATH'."
    exit 1
}
$ver = python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Host "Python $ver: OK" -ForegroundColor Green

# --- 2. Dependencias ----------------------------------------------------
Write-Host "Instalando dependencias (Pillow)..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
Write-Host "Dependencias: OK" -ForegroundColor Green

# --- 3. config.json -----------------------------------------------------
if (Test-Path config.json) {
    Write-Host "config.json ya existe: no se toca." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "Vamos a crear config.json. Antes de nada, consigue:" -ForegroundColor Cyan
    Write-Host "  1. Un token de bot: pidelo a @BotFather en Telegram (/newbot)."
    Write-Host "     Usa un bot NUEVO: dos programas haciendo polling del mismo se pelean."
    Write-Host "  2. Tu id de Telegram: pidelo a @userinfobot."
    Write-Host "  3. Una api_key de cualquier proveedor compatible con OpenAI"
    Write-Host "     (OpenRouter, OpenAI, Ollama local, vLLM...)."
    Write-Host ""
    $token  = Read-Host "Token del bot (BotFather)"
    $dueno  = Read-Host "Tu id de Telegram"
    $base   = Read-Host "base_url del proveedor [https://openrouter.ai/api/v1]"
    if (-not $base) { $base = "https://openrouter.ai/api/v1" }
    $clave  = Read-Host "api_key del proveedor"
    $modelo = Read-Host "Modelo de charla (ej. openai/gpt-4o) [VENDOR/MODELO-DE-CHARLA]"
    if (-not $modelo) { $modelo = "VENDOR/MODELO-DE-CHARLA" }
    $vision = Read-Host "Modelo de vision multimodal, Enter = el mismo de charla"
    if (-not $vision) { $vision = $modelo }

    $cfg = Get-Content config.ejemplo.json -Raw -Encoding UTF8 | ConvertFrom-Json
    $cfg.token = $token
    $cfg.dueno_id = [int]$dueno
    $cfg.modelo.base_url = $base
    $cfg.modelo.api_key = $clave
    $cfg.modelo.id = $modelo
    $cfg.vision.base_url = $base
    $cfg.vision.api_key = $clave
    $cfg.vision.id = $vision
    $cfg | ConvertTo-Json -Depth 10 | Set-Content config.json -Encoding UTF8
    Write-Host "config.json creado." -ForegroundColor Green
    Write-Host "OJO: el modelo de embeddings (memoria.modelo_embedding) queda de plantilla;" -ForegroundColor Yellow
    Write-Host "si quieres memoria a largo plazo, edita config.json y pon uno real."
}

Write-Host ""
Write-Host "=== Listo ===" -ForegroundColor Cyan
Write-Host "Arranca con:   python main.py"
Write-Host "Sin ventana:   wscript arrancar.vbs"
Write-Host ""
Write-Host "Opcional (Windows): tarea programada 'guardian.vbs' cada 5 minutos para"
Write-Host "que el bot se levante solo si se cae. Ver la cabecera de guardian.vbs."
