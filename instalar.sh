#!/usr/bin/env bash
# Instalador de Close Yui (Ubuntu/Debian).
# Uso: ./instalar.sh
#
# Hace tres cosas: prepara python3 + venv, instala la unica dependencia
# (Pillow) y crea config.json con tus respuestas. No toca nada fuera de
# esta carpeta (salvo apt-get si falta python3, que necesita sudo).

set -e
cd "$(dirname "$0")"

echo ""
echo "=== Close Yui: instalacion ==="

# --- 1. Python ----------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "Falta python3; instalando con apt (necesita sudo)..."
    sudo apt-get update && sudo apt-get install -y python3 python3-venv
fi
echo "Python: $(python3 --version)"

# --- 2. venv + dependencias ----------------------------------------------
if [ ! -d .venv ]; then
    python3 -m venv .venv 2>/dev/null || {
        echo "Falta el modulo venv; instalando python3-venv (sudo)..."
        sudo apt-get install -y python3-venv
        python3 -m venv .venv
    }
fi
# shellcheck disable=SC1091
. .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "Dependencias: OK (en .venv)"

# --- 3. config.json -------------------------------------------------------
if [ -f config.json ]; then
    echo "config.json ya existe: no se toca."
else
    echo ""
    echo "Vamos a crear config.json. Antes de nada, consigue:"
    echo "  1. Un token de bot: pidelo a @BotFather en Telegram (/newbot)."
    echo "     Usa un bot NUEVO: dos programas haciendo polling del mismo se pelean."
    echo "  2. Tu id de Telegram: pidelo a @userinfobot."
    echo "  3. Una api_key de cualquier proveedor compatible con OpenAI"
    echo "     (OpenRouter, OpenAI, Ollama local, vLLM...)."
    echo ""
    read -rp "Token del bot (BotFather): " TOKEN
    read -rp "Tu id de Telegram: " DUENO
    read -rp "base_url del proveedor [https://openrouter.ai/api/v1]: " BASE
    BASE=${BASE:-https://openrouter.ai/api/v1}
    read -rp "api_key del proveedor: " CLAVE
    read -rp "Modelo de charla [VENDOR/MODELO-DE-CHARLA]: " MODELO
    MODELO=${MODELO:-VENDOR/MODELO-DE-CHARLA}
    read -rp "Modelo de vision multimodal (Enter = el de charla): " VISION
    VISION=${VISION:-$MODELO}

    python - "$TOKEN" "$DUENO" "$BASE" "$CLAVE" "$MODELO" "$VISION" <<'PYEOF'
import json, sys
from pathlib import Path

token, dueno, base, clave, modelo, vision = sys.argv[1:7]
cfg = json.loads(Path("config.ejemplo.json").read_text(encoding="utf-8"))
cfg["token"] = token
cfg["dueno_id"] = int(dueno)
cfg["modelo"].update({"base_url": base, "api_key": clave, "id": modelo})
cfg["vision"].update({"base_url": base, "api_key": clave, "id": vision})
Path("config.json").write_text(
    json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("config.json creado.")
PYEOF
    echo "OJO: memoria.modelo_embedding queda de plantilla; si quieres memoria"
    echo "a largo plazo, edita config.json y pon un modelo de embeddings real."
fi

echo ""
echo "=== Listo ==="
echo "Arranca con:   .venv/bin/python main.py"
echo "En segundo plano (sin ventana en Windows) o con systemd en Linux."
echo ""
echo "Opcional (Ubuntu): unidad de usuario systemd para arranque automatico:"
echo "  ver README.md, seccion 'Servicio opcional'."
