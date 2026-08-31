# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Poda las copias diarias, que nadie estaba podando.

respaldo_diario() en main.py dice en su docstring "se conservan los ultimos 7"
y luego no borra nada. Por eso habia ocho copias de memoria.jsonl ocupando 82
MB y subiendo unos 12 MB al dia.

Aqui se implementa lo que ya estaba escrito: por cada fichero respaldado se
quedan las N copias mas recientes. Las demas NO se borran — van a cuarentena
con fecha, que es como se hacen las cosas en esta casa.

Las copias de CODIGO (*.bak-*) no se tocan nunca: no hay git en este proyecto,
asi que son el unico historial que existe, y ocupan 120 KB en total.

Uso:
    python herramientas/podar_copias.py             # ensena y no toca nada
    python herramientas/podar_copias.py --aplicar   # aparta las sobrantes
    python herramientas/podar_copias.py --guardar 3 # cambia cuantas se quedan
"""

import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COPIAS = RAIZ / "copias"
GUARDAR = 7

# "2026-08-24.memoria.jsonl" y tambien "2026-08-21-003635.memoria.jsonl"
FECHADA = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-\d+)?\.(.+)$")


def agrupar():
    """Devuelve {fichero_respaldado: [(clave_orden, ruta), ...]} de nuevo a viejo."""
    grupos = defaultdict(list)
    for p in COPIAS.iterdir():
        if not p.is_file():
            continue
        m = FECHADA.match(p.name)
        if not m:
            continue                       # .bak de codigo y demas: no se tocan
        grupos[m.group(2)].append((p.name, p))
    for k in grupos:
        grupos[k].sort(reverse=True)       # el nombre empieza por fecha ISO
    return grupos


def main():
    aplicar = "--aplicar" in sys.argv
    guardar = GUARDAR
    if "--guardar" in sys.argv:
        guardar = int(sys.argv[sys.argv.index("--guardar") + 1])

    if not COPIAS.is_dir():
        print("no hay carpeta copias/")
        return

    grupos = agrupar()
    intactos = [p.name for p in COPIAS.iterdir()
                if p.is_file() and not FECHADA.match(p.name)]

    sobran = []
    print("se conservan las %d copias mas recientes de cada fichero" % guardar)
    print()
    for nombre in sorted(grupos):
        v = grupos[nombre]
        quedan, fuera = v[:guardar], v[guardar:]
        total = sum(p.stat().st_size for _, p in v)
        print("%-18s %d copias, %.1f MB" % (nombre, len(v), total / 1e6))
        for n, p in quedan:
            print("    se queda   %-34s %7.1f MB" % (n, p.stat().st_size / 1e6))
        for n, p in fuera:
            print("    SE APARTA  %-34s %7.1f MB" % (n, p.stat().st_size / 1e6))
        sobran.extend(fuera)
        print()

    if intactos:
        print("no se tocan (no llevan fecha en el nombre):")
        for n in sorted(intactos):
            print("    %s" % n)
        print()

    libera = sum(p.stat().st_size for _, p in sobran)
    print("=" * 58)
    print("  se apartarian %d fichero(s), %.1f MB" % (len(sobran), libera / 1e6))

    if not sobran:
        return
    if not aplicar:
        print("  Nada tocado. Con --aplicar se mueven a cuarentena.")
        return

    sello = datetime.now().strftime("%Y-%m-%d")
    destino = Path.home() / "cuarentena" / ("close-yui-copias-%s" % sello)
    destino.mkdir(parents=True, exist_ok=True)
    for n, p in sobran:
        shutil.move(str(p), str(destino / n))
    print("  movidos a %s" % destino)


if __name__ == "__main__":
    main()
