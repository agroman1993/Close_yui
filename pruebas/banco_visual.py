# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Banco aseptico para el nodo extractor visual.

Llama al modelo de vision DIRECTAMENTE, sin Telegram y sin persona:
solo el system prompt de extraccion. Sirve para saber que devuelve de verdad
antes de conectarlo, en vez de suponerlo.

Uso:
    python pruebas/banco_visual.py           # muestra el plan, no llama
    python pruebas/banco_visual.py --run
"""

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MEDIOS = Path(__file__).parent / "medios"

SISTEMA = """Eres un sensor de visión por computador y un analizador de contexto visual. Tu única función es analizar la imagen o vídeo recibido y generar una descripción exhaustiva estructurada estrictamente en formato JSON.

REGLAS OBLIGATORIAS:
1. Responde ÚNICAMENTE con el objeto JSON crudo. No incluyas intros, salidas, explicaciones ni bloques de código markdown (sin ```json).
2. Si es un vídeo, analiza la línea temporal (marcas de tiempo), acciones clave, texto en pantalla, ambiente y detalles visuales relevantes.
3. No interpretes sentimientos ni intentes hablar como un humano. Sé quirúrgico y preciso.

Estructura requerida:
{
  "tipo": "video|imagen",
  "descripcion_general": "Resumen conciso en 1 frase",
  "elementos_clave": ["elemento1", "elemento2"],
  "linea_temporal_o_detalles": "Descripción cronológica o espacial detallada del contenido visual y auditivo"
}"""

CLAVES = ("tipo", "descripcion_general", "elementos_clave", "linea_temporal_o_detalles")


def b64(p):
    return base64.b64encode(p.read_bytes()).decode()


def bloque(ruta):
    if ruta.suffix.lower() in (".mp4", ".webm", ".mov"):
        return {"type": "video_url",
                "video_url": {"url": "data:video/mp4;base64," + b64(ruta)}}
    return {"type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + b64(ruta)}}


# Con video, GLM ignora el system prompt y responde en prosa (comprobado).
# Repetir la orden en el propio turno del usuario, pegada al medio, es lo que
# hace que la respete. Con imagen no hace falta, pero no molesta.
RECORDATORIO = ("Analiza este medio y responde ÚNICAMENTE con el objeto JSON crudo "
                "con las claves exactas: tipo, descripcion_general, elementos_clave, "
                "linea_temporal_o_detalles. En español. Sin markdown, sin ```, sin texto fuera del JSON.")


def llamar(cfg, contenido, forzar=True):
    partes = list(contenido)
    if forzar:
        partes.append({"type": "text", "text": RECORDATORIO})
    peticion = {
        "model": cfg["id"],
        "messages": [{"role": "system", "content": SISTEMA},
                     {"role": "user", "content": partes}],
        "max_tokens": 3000,
    }
    if forzar:
        peticion["response_format"] = {"type": "json_object"}
    cuerpo = json.dumps(peticion).encode()
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions",
                                 data=cuerpo, headers={
                                     "Content-Type": "application/json",
                                     "Authorization": "Bearer " + cfg["api_key"]})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, "HTTP %s: %s" % (e.code, e.read().decode()[:300]), {}, time.time() - t0
    msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
    return (msg.get("content") or "").strip(), None, d.get("usage") or {}, time.time() - t0


def revisar(texto):
    """Comprueba que el JSON sale limpio y con el esquema pedido."""
    notas = []
    if texto.startswith("```"):
        notas.append("FALLO: viene envuelto en markdown (```)")
        texto = texto.strip("`").lstrip("json").strip()
    try:
        obj = json.loads(texto)
    except json.JSONDecodeError as err:
        notas.append("FALLO: no es JSON valido (%s)" % err)
        return None, notas
    faltan = [k for k in CLAVES if k not in obj]
    sobran = [k for k in obj if k not in CLAVES]
    if faltan:
        notas.append("FALLO: faltan claves %s" % faltan)
    if sobran:
        notas.append("aviso: claves extra %s" % sobran)
    if not faltan and not sobran:
        notas.append("OK: JSON limpio y esquema exacto")
    return obj, notas


def main():
    cfg = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))["modelo"]
    cfg = dict(cfg, id="z-ai/glm-4.6v")

    casos = [("imagen", MEDIOS / "frame.jpg"), ("video", MEDIOS / "clip.mp4")]
    if "--run" not in sys.argv:
        print("modelo: %s  (aseptico: sin Telegram y sin personaje, solo el prompt de extraccion)\n" % cfg["id"])
        for n, p in casos:
            tam = "{:,} bytes".format(p.stat().st_size) if p.exists() else "NO EXISTE"
            print("  %-7s %-12s %s" % (n, p.name, tam))
        print("\n2 llamadas. ejecutar con --run")
        return

    for nombre, ruta in casos:
        print("=" * 68)
        print("PRUEBA: %s  (%s)" % (nombre, ruta.name))
        print("=" * 68)
        texto, err, uso, secs = llamar(cfg, [bloque(ruta)])
        if err:
            print("  FALLO (%.1fs): %s\n" % (secs, err))
            continue
        obj, notas = revisar(texto)
        print("  (%.1fs, %s tokens entrada / %s salida)"
              % (secs, uso.get("prompt_tokens", "?"), uso.get("completion_tokens", "?")))
        for n in notas:
            print("  " + n)
        print()
        print(json.dumps(obj, ensure_ascii=False, indent=2) if obj else texto[:1500])
        print()


if __name__ == "__main__":
    main()
