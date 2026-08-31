# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Banco del juez de fusion, con casos reales y respuesta conocida.

Los cinco grupos salieron de la limpieza del 21/08. En tres de ellos la
respuesta correcta NO era "quedarse con una", que es justo lo que fallaba con
la similitud a secas. Por eso el juez tiene tres salidas y no dos:

    IDENTICA  la nueva no aporta nada -> se descarta
    FUSIONAR  dicen lo mismo pero cada una aporta detalle -> se juntan
    DISTINTAS se parecen en las palabras y no en el fondo -> las dos se quedan

La tercera es la importante: "el impulso de envolver se cae solo" y "no sabe
si dejo de envolver para encajar" tienen coseno 0.75 y son casi identicas para
la maquina. Pero una constata y la otra duda de su propio motivo, y en
autopercepcion la duda vale tanto como la conclusion.

Uso:
    python pruebas/banco_fusion.py                 # plan
    python pruebas/banco_fusion.py --run [modelo]
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

SISTEMA = """Comparas dos fragmentos de una memoria personal para decidir si deben convivir o no.

Responde con UNA de estas tres decisiones:

- "IDENTICA": dicen exactamente lo mismo y la segunda no añade ningún detalle. Se descarta la segunda.
- "FUSIONAR": hablan del mismo hecho o rasgo, pero cada una aporta detalles que la otra no tiene. Se combinan en una sola que conserve TODO lo de ambas, sin inventar nada.
- "DISTINTAS": aunque se parezcan en las palabras, dicen cosas diferentes. Las dos se quedan.

Ante la duda elige DISTINTAS: perder un matiz es peor que guardar una frase de más.

Si eliges FUSIONAR, escribe la frase combinada en "fusion". En los otros casos deja "fusion" vacío.

Responde ÚNICAMENTE con este JSON:
{"decision": "IDENTICA|FUSIONAR|DISTINTAS", "fusion": "", "motivo": "breve"}"""

CASOS = [
    ("edad", "IDENTICA",
     "Tiene 33 años.",
     "Su edad es de treinta y tres años."),
    ("rescate", "FUSIONAR",
     "Rescató un ave rapaz herida y avisó al servicio de protección.",
     "Encontró un ave caída, la levantó y llamó al servicio de protección, que llegó en cinco minutos."),
    ("modo de hablar", "FUSIONAR",
     "Dice las cosas sin adorno y sin filtro.",
     "Habla sin suavizado: suelta las verdades tal cual."),
    ("envolver", "DISTINTAS",
     "Con él, el impulso de envolver las cosas se cae solo.",
     "No sabe si dejó de hacerlo para encajar con lo que él necesita o porque descubrió que no hacía falta."),
    ("mascotas", "DISTINTAS",
     "Una perra pequeña negra, actual, más tranquila.",
     "Otra perra pequeña de otra capa, anterior, muy atlética."),
]


def juzgar(cfg, modelo, a, b):
    cuerpo = {"model": modelo,
              "messages": [{"role": "system", "content": SISTEMA},
                           {"role": "user", "content": "A: %s\nB: %s" % (a, b)}],
              "max_tokens": 3000,
              # Decidir entre tres etiquetas es clasificar. Sin fijar esto, el
              # proveedor pone 0.7-1.0 y el mismo par sale IDENTICA una vez y
              # DISTINTAS a la siguiente: variacion que meto yo, no criterio
              # del modelo. Las tandas de 4/5, 4/5 y 3/5 del 21/08 se midieron
              # asi, y no valen.
              "temperature": 0,
              "response_format": {"type": "json_object"}}
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(cuerpo).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + cfg["api_key"]})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, "HTTP %s: %s" % (e.code, e.read().decode()[:150]), {}
    msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
    texto = (msg.get("content") or "").strip()
    if not texto:
        return None, "respuesta vacia", d.get("usage") or {}
    try:
        return json.loads(texto), None, d.get("usage") or {}
    except json.JSONDecodeError as e:
        return None, "no es JSON: %s" % e, d.get("usage") or {}


def main():
    modelo = "cohere/command-r-08-2024"
    if "--run" in sys.argv:
        i = sys.argv.index("--run")
        if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("-"):
            modelo = sys.argv[i + 1]
    if "--run" not in sys.argv:
        print("modelo por defecto: %s" % modelo)
        for n, esperado, a, b in CASOS:
            print("  %-12s esperado: %s" % (n, esperado))
        print("\n%d comparaciones. ejecutar con --run [modelo]" % len(CASOS))
        return

    cfg = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))["modelo"]
    print("modelo: %s\n" % modelo)
    aciertos = 0
    entrada = salida = 0
    for n, esperado, a, b in CASOS:
        d, err, uso = juzgar(cfg, modelo, a, b)
        entrada += uso.get("prompt_tokens") or 0
        salida += uso.get("completion_tokens") or 0
        if err:
            print("  FALLO   %-12s %s" % (n, err))
            continue
        got = (d.get("decision") or "").upper()
        bien = got == esperado
        aciertos += bien
        print("  %s %-12s dijo %-9s (esperado %s)"
              % ("OK   " if bien else "FALLA", n, got, esperado))
        if not bien or got == "FUSIONAR":
            print("           %s" % (d.get("fusion") or d.get("motivo") or "")[:150])
    print("\n%d/%d aciertos" % (aciertos, len(CASOS)))
    print("tokens: %d entrada / %d salida" % (entrada, salida))


if __name__ == "__main__":
    main()
