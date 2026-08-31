# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Compara varios modelos extractores sobre los mismos medios.

Mismo prompt, mismos ficheros, mismo criterio. Sirve para elegir el nodo de
la Fase 2 con datos y no por intuicion.

Uso:
    python pruebas/comparar.py            # plan y coste, sin llamar
    python pruebas/comparar.py --run
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from banco_visual import CLAVES, MEDIOS, RAIZ, RECORDATORIO, SISTEMA, bloque, revisar  # noqa: E402

# Edita esta lista con los modelos multimodales que quieras comparar.
MODELOS = ["PROVEEDOR/MODELO-A", "PROVEEDOR/MODELO-B"]

CASOS = [
    ("imagen", "frame.jpg", None),
    ("video con subtitulos", "clip.mp4", None),
    ("pantalla azul + voz", "solo_audio.mp4", "oye"),   # 'oye' = prueba de audio
]

# Palabras del audio del clip. Si aparecen describiendo la pantalla azul,
# el modelo ha oido de verdad: en pantalla no hay un solo caracter.
PISTAS_AUDIO = ("voz", "audio", "dice", "narra", "habla", "sonido", "musica")


def llamar(cfg, modelo_id, contenido):
    peticion = {
        "model": modelo_id,
        "messages": [
            {"role": "system", "content": SISTEMA},
            {"role": "user", "content": list(contenido) + [{"type": "text", "text": RECORDATORIO}]},
        ],
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(peticion).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["api_key"]})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, "HTTP %s: %s" % (e.code, e.read().decode()[:200]), {}, time.time() - t0
    except Exception as e:                                        # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e), {}, time.time() - t0

    msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
    texto = (msg.get("content") or "").strip()
    if not texto and (msg.get("reasoning") or "").strip():
        return None, "agoto el presupuesto razonando y no escribio respuesta", d.get("usage") or {}, time.time() - t0
    return texto, None, d.get("usage") or {}, time.time() - t0


def evaluar(nombre, marca, texto):
    """Devuelve (obj, lista de veredictos)."""
    obj, notas = revisar(texto)
    v = []
    v.append(("json limpio", not any(n.startswith("FALLO") for n in notas)))
    if obj:
        v.append(("esquema exacto", sorted(obj) == sorted(CLAVES)))
        v.append(("tipo respeta enum", str(obj.get("tipo", "")).strip().lower() in ("video", "imagen")))
        if marca == "oye":
            blob = json.dumps(obj, ensure_ascii=False).lower()
            v.append(("OYE el audio", any(p in blob for p in PISTAS_AUDIO)))
    return obj, v


def main():
    cfg = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))["modelo"]
    if "--run" not in sys.argv:
        print("modelos: %s" % ", ".join(MODELOS))
        for n, f, _ in CASOS:
            print("  %-22s %s" % (n, f))
        print("\n%d llamadas (%d casos x %d modelos). ejecutar con --run"
              % (len(CASOS) * len(MODELOS), len(CASOS), len(MODELOS)))
        return

    resumen = {}
    for modelo_id in MODELOS:
        print("\n" + "#" * 70)
        print("# " + modelo_id)
        print("#" * 70)
        resumen[modelo_id] = []
        for nombre, fichero, marca in CASOS:
            texto, err, uso, secs = llamar(cfg, modelo_id, [bloque(MEDIOS / fichero)])
            print("\n--- %s ---" % nombre)
            if err:
                print("  FALLO (%.1fs): %s" % (secs, err))
                resumen[modelo_id].append((nombre, [("llamada ok", False)]))
                continue
            obj, v = evaluar(nombre, marca, texto)
            print("  %.1fs  %s entrada / %s salida"
                  % (secs, uso.get("prompt_tokens", "?"), uso.get("completion_tokens", "?")))
            for etiqueta, bien in v:
                print("   %s %s" % ("OK   " if bien else "FALLO", etiqueta))
            resumen[modelo_id].append((nombre, v))
            if obj:
                print("  " + json.dumps(obj, ensure_ascii=False, indent=2).replace("\n", "\n  ")[:900])

    print("\n" + "=" * 70)
    print("RESUMEN")
    print("=" * 70)
    for modelo_id, casos in resumen.items():
        total = sum(len(v) for _, v in casos)
        bien = sum(1 for _, v in casos for _, b in v if b)
        print("  %-28s %d/%d comprobaciones" % (modelo_id, bien, total))
        for nombre, v in casos:
            fallos = [e for e, b in v if not b]
            if fallos:
                print("      %-22s falla: %s" % (nombre, ", ".join(fallos)))


if __name__ == "__main__":
    main()
