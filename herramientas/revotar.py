# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Maraton de fase 3: vuelve a votar TODOS los recuerdos que ya estan en vivo.

Los 138 de ahora entraron con jurados distintos a lo largo de varios dias — el
binario viejo, que aprobaba el 94%, y luego el nuevo de dos notas. Esto los
pasa a todos por el jurado de hoy, con el mismo codigo que usa ella al
promover, para que la vara sea una sola.

Nada se borra: los que no pasen el corte se apartan a un fichero aparte con
fecha, con su nota escrita al lado. Si el corte cambia manana, se rescatan sin
volver a pagar por juzgarlos.

Uso:
    python herramientas/revotar.py            # juzga y ensena, sin tocar nada
    python herramientas/revotar.py --aplicar  # ademas aparta los suspensos
"""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from nucleo.jurado import (JUEZ_CONECTIVIDAD, JUEZ_EMOCION, combinar,
                           formatear_acta)
from nucleo.memoria import Memoria

TANDA = 8


def main():
    aplicar = "--aplicar" in sys.argv
    cfg = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
    m = Memoria(cfg, log=print)

    vivos = list(m._vivas)
    print("recuerdos en vivo: %d   corte: %.1f   pesos: emocion %.1f / conectividad %.1f"
          % (len(vivos), m._corte, m._peso_emocion, m._peso_conectividad))
    print("jueces: %s (emocion)  |  %s (conectividad)"
          % (m._modelo_juez, m._modelo_frio))
    print()

    juzgados = []
    for i in range(0, len(vivos), TANDA):
        tanda = vivos[i:i + TANDA]
        emo = m._puntuar(m._modelo_juez, JUEZ_EMOCION, tanda, etiqueta="emocion")
        con = m._puntuar(m._modelo_frio, JUEZ_CONECTIVIDAD, tanda, etiqueta="conectividad")
        for j, c in enumerate(tanda):
            e, k = emo.get(j), con.get(j)
            final = combinar(e, k, m._peso_emocion, m._peso_conectividad)
            entra = final is not None and final >= m._corte
            juzgados.append({"r": c, "e": e, "k": k, "final": final, "entra": entra})
            print(formatear_acta(c, e, k, final, entra))
        print("   --- %d de %d ---" % (min(i + TANDA, len(vivos)), len(vivos)))

    # Las notas se guardan SIEMPRE, aunque no se aplique nada. Puntuar cuesta
    # dinero; volver a cortar con otro umbral, no. La primera version tiraba
    # las notas y obligaba a pagar otra vez para mirar lo mismo.
    acta = RAIZ / "copias" / ("%s.acta-revotado.json"
                              % datetime.now().strftime("%Y-%m-%d"))
    acta.write_text(json.dumps([
        {"texto": x["r"]["texto"], "tipo": x["r"].get("tipo"),
         "origen": x["r"].get("origen"), "emocion": x["e"],
         "conectividad": x["k"], "final": x["final"]}
        for x in juzgados], ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print("acta guardada en %s — con esto se puede recortar a cualquier umbral" % acta.name)
    print("sin volver a llamar a ningun juez.")

    pasan = [x for x in juzgados if x["entra"]]
    caen = [x for x in juzgados if not x["entra"]]
    sin_nota = [x for x in juzgados if x["final"] is None]

    print()
    print("=" * 60)
    print("  pasan  : %d" % len(pasan))
    print("  caen   : %d" % len(caen))
    if sin_nota:
        print("  sin nota (ningun juez contesto): %d — se dejan como estan"
              % len(sin_nota))
    if caen:
        print()
        print("  los que caen, de peor a mejor:")
        for x in sorted([c for c in caen if c["final"] is not None],
                        key=lambda y: y["final"])[:15]:
            print("    %.1f  %s" % (x["final"], x["r"]["texto"][:70]))

    if not aplicar:
        print()
        print("Nada tocado. Con --aplicar se apartan los suspensos.")
        return

    sello = datetime.now().strftime("%Y-%m-%d")
    previa = RAIZ / "copias" / ("%s.antes-de-revotar.jsonl" % sello)
    shutil.copy2(m._ruta, previa)

    # Los que no pasan se van a un fichero aparte, con su nota dentro. Sin nota
    # no se aparta nadie: que un juez no conteste no es un suspenso.
    apartados = RAIZ / "copias" / ("%s.apartados.jsonl" % sello)
    with apartados.open("a", encoding="utf-8") as f:
        for x in caen:
            if x["final"] is None:
                continue
            r = dict(x["r"])
            r["nota_revotado"] = {"emocion": x["e"], "conectividad": x["k"],
                                  "final": x["final"], "fecha": sello}
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    quedan = [x["r"] for x in juzgados if x["entra"] or x["final"] is None]
    with m._ruta.open("w", encoding="utf-8") as f:
        for r in quedan:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print()
    print("copia previa : copias/%s" % previa.name)
    print("apartados    : %s (%d)" % (apartados.name, len(caen) - len(sin_nota)))
    print("en vivo ahora: %d" % len(quedan))


if __name__ == "__main__":
    main()
