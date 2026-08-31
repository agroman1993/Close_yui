# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Fusiona recuerdos casi identicos.

El tribunal aprueba el 94% de lo que le llega y ademas juzga cada capsula
contra la nada, sin mirar si ya existe algo igual. Resultado: tres formas de
decir lo mismo ocupando tres huecos.

Y no solo ocupan: en la recuperacion solo entran 3 recuerdos por turno, asi
que tres duplicados del mismo rasgo DESPLAZAN a lo demas. El ruido no es
pasivo, compite.

De cada grupo se queda uno: el que mas senales tenga y, a igualdad, el mas
informativo (el mas largo). Los demas se apartan a un fichero, no se pierden.

Uso:
    python herramientas/limpiar_memoria.py                 # que agruparia
    python herramientas/limpiar_memoria.py --umbral 0.80
    python herramientas/limpiar_memoria.py --limpiar
"""

import json
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MEMORIA = RAIZ / "memoria.jsonl"
APARTADOS = RAIZ / "copias" / "memoria-fusionados.jsonl"

UMBRAL = 0.78


def cargar():
    rs = []
    for l in MEMORIA.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        v = r.get("vector") or []
        r["_n"] = math.sqrt(sum(x * x for x in v)) or 1.0
        rs.append(r)
    return rs


def cos(a, b):
    return sum(x * y for x, y in zip(a["vector"], b["vector"])) / (a["_n"] * b["_n"])


def agrupar(rs, umbral):
    """Union-find sencillo: lo que se parece por encima del umbral va junto."""
    padre = list(range(len(rs)))

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    for i in range(len(rs)):
        for j in range(i + 1, len(rs)):
            if cos(rs[i], rs[j]) >= umbral:
                padre[raiz(i)] = raiz(j)
    grupos = {}
    for i in range(len(rs)):
        grupos.setdefault(raiz(i), []).append(i)
    return [g for g in grupos.values() if len(g) > 1]


def mejor(rs, grupo):
    """El que mas senales tenga; a igualdad, el mas informativo."""
    return max(grupo, key=lambda i: (rs[i].get("senales", 1), len(rs[i]["texto"])))


def main():
    umbral = UMBRAL
    if "--umbral" in sys.argv:
        umbral = float(sys.argv[sys.argv.index("--umbral") + 1])

    rs = cargar()
    grupos = agrupar(rs, umbral)
    fuera = set()
    print("recuerdos: %d    umbral: %.2f\n" % (len(rs), umbral))
    for g in grupos:
        k = mejor(rs, g)
        print("grupo de %d:" % len(g))
        for i in g:
            marca = "  SE QUEDA " if i == k else "  se aparta"
            if i != k:
                fuera.add(i)
            print("%s [%d señal] %s" % (marca, rs[i].get("senales", 1), rs[i]["texto"][:88]))
        print()
    print("=" * 60)
    print("%d grupo(s), %d recuerdo(s) se apartarian, quedarian %d"
          % (len(grupos), len(fuera), len(rs) - len(fuera)))

    if "--limpiar" not in sys.argv:
        print("\nNada tocado. Para hacerlo de verdad:")
        print("  python herramientas/limpiar_memoria.py --limpiar")
        return
    if not fuera:
        print("\nNada que fusionar.")
        return

    APARTADOS.parent.mkdir(parents=True, exist_ok=True)
    copia = APARTADOS.parent / ("%s.memoria.jsonl" % datetime.now().strftime("%Y-%m-%d-%H%M%S"))
    shutil.copy(MEMORIA, copia)

    with open(APARTADOS, "a", encoding="utf-8") as f:
        for i in sorted(fuera):
            f.write(json.dumps({k: v for k, v in rs[i].items() if not k.startswith("_")},
                               ensure_ascii=False) + "\n")
    with open(MEMORIA, "w", encoding="utf-8") as f:
        for i, r in enumerate(rs):
            if i in fuera:
                continue
            f.write(json.dumps({k: v for k, v in r.items() if not k.startswith("_")},
                               ensure_ascii=False) + "\n")
    print("\ncopia completa en   : %s" % copia.name)
    print("apartados guardados : %s" % APARTADOS.name)
    print("memoria.jsonl       : %d recuerdos" % (len(rs) - len(fuera)))


if __name__ == "__main__":
    main()
