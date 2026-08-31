# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""La consolidacion de dias alternos. Esto es lo que lanza el Programador.

Se ejecuta muchas veces y casi siempre no hace nada: mira si toca y se va.
Esa es la gracia. La tarea de Windows se dispara cada hora y al iniciar
sesion, y quien decide si toca es ESTE script, no el reloj del programador.

    ¿por que no una tarea a las 14:30 y ya esta?

    Medido sobre arranques reales: entre semana el equipo enciende casi
    a hora fija, pero los dias libres enciende a deshora, y una tarea
    fijada a las 14:30 se pierde si el equipo arranca a las 14:39.

    Con la condicion en el script, un dia con el ordenador apagado no salta
    el turno: solo lo retrasa hasta el siguiente arranque.

Lo que hace cuando SI toca:

    1. lee la pre-memoria acumulada por el sondeo y los nodos que ya hay
    2. un modelo bueno decide, particula por particula: refuerza / nueva / paja
    3. se copia memoria.jsonl a copias/ ANTES de tocar nada
    4. se aplican los refuerzos y se vectorizan los nodos nuevos
    5. la pre-memoria consumida se archiva, no se borra
    6. se escribe la marca de fecha, y solo si todo lo anterior salio bien

Nada se borra en ningun paso. Si una consolidacion sale mal, se restaura la
copia y no se ha perdido ni una particula.
"""

import json
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from nucleo.memoria import Memoria                          # noqa: E402
from nucleo.sondeo import (Sondeo, leer_jsonl,  # noqa: E402
                           proveedor_de, toca_consolidar)

SELLO = datetime.now().strftime("%Y%m%d-%H%M")


def log(*a):
    print("[%s]" % datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def main():
    cfg = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
    s = cfg.get("sondeo") or {}
    c = s.get("consolidacion") or {}
    marca = RAIZ / c.get("marca", ".ultima_consolidacion")

    toca, motivo = toca_consolidar(marca,
                                   c.get("horas", 44),
                                   c.get("no_antes_de", "14:30"))
    if not toca:
        log("todavia no:", motivo)
        return 0
    log("toca:", motivo)

    pre_ruta = RAIZ / s.get("pre_memoria", "pre-memoria.jsonl")
    particulas = leer_jsonl(pre_ruta)
    if not particulas:
        # Sin material no se llama a nadie. Y NO se escribe la marca: si
        # mañana hay particulas, que pueda saltar sin esperar otras 44 horas.
        log("la pre-memoria esta vacia, no hay nada que consolidar")
        return 0
    log("particulas en la pre-memoria: %d" % len(particulas))

    m = Memoria(cfg, log=log)
    nodos = list(m._vivas)
    log("nodos consolidados ahora: %d" % len(nodos))

    # -- 1. que decide el modelo bueno ------------------------------------
    lista_nodos = "\n".join("%d. %s" % (i + 1, " ".join(n["texto"].split()))
                            for i, n in enumerate(nodos))
    lista_part = "\n".join("- [%s] %s" % (p.get("tipo", "?"),
                                          " ".join(p["texto"].split()))
                           for p in particulas)
    peticion = ("RECUERDOS YA CONSOLIDADOS:\n%s\n\nPARTÍCULAS SUELTAS:\n%s"
                % (lista_nodos or "(ninguno)", lista_part))

    sonda = Sondeo(cfg, log=log)
    proveedor = proveedor_de(c)
    log("preguntando a %s ..." % (c.get("modelo") or cfg["modelo"]["id"]))
    d = sonda._pedir(sonda._consolidacion_tpl, peticion,
                     modelo=c.get("modelo") or cfg["modelo"]["id"],
                     max_tokens=c.get("max_tokens", 40000),
                     proveedor=proveedor)

    refuerzos = [r for r in (d.get("refuerzos") or [])
                 if isinstance(r.get("nodo"), int) and 1 <= r["nodo"] <= len(nodos)]
    nuevos = []
    for n in (d.get("nuevos") or []):
        t = " ".join((n.get("texto") or "").split())
        if len(t) >= 30:
            nuevos.append({"texto": t, "tipo": (n.get("tipo") or "dato").strip().lower()})
    log("refuerzos: %d | nodos nuevos: %d | paja: %s"
        % (len(refuerzos), len(nuevos), d.get("paja", "?")))

    if not refuerzos and not nuevos:
        log("el modelo no ha visto nada que merezca sobrevivir")
        # Aqui SI se archiva y se marca: la pre-memoria se ha juzgado y el
        # veredicto es que era toda paja. Volver a juzgarla mañana costaria
        # dinero para llegar a lo mismo.
        archivar_pre(pre_ruta, len(particulas))
        marca.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        return 0

    # -- 2. copia de seguridad ANTES de tocar nada ------------------------
    copias = RAIZ / "copias"
    copias.mkdir(exist_ok=True)
    respaldo = copias / ("memoria-antes-de-%s.jsonl" % SELLO)
    if m._ruta.exists():
        shutil.copy2(m._ruta, respaldo)
        log("copia de seguridad: copias/%s" % respaldo.name)

    # -- 3. refuerzos ------------------------------------------------------
    for r in refuerzos:
        n = nodos[r["nodo"] - 1]
        n["senales"] = int(n.get("senales", 1)) + 1
        nueva = " ".join((r.get("redaccion_nueva") or "").split())
        if nueva and len(nueva) >= 30 and nueva != n["texto"]:
            log("  nodo %d reescrito (%s)" % (r["nodo"], (r.get("aporta") or "")[:52]))
            n["texto"] = nueva
            n["_revectorizar"] = True
        else:
            log("  nodo %d reforzado -> %d señales (%s)"
                % (r["nodo"], n["senales"], (r.get("aporta") or "")[:52]))

    # -- 4. vectorizar lo que lo necesite ---------------------------------
    pendientes = [n for n in nodos if n.pop("_revectorizar", False)]
    textos = [n["texto"] for n in pendientes] + [n["texto"] for n in nuevos]
    if textos:
        log("vectorizando %d texto(s) con %s ..." % (len(textos), m._modelo_emb))
        vectores = m.vectorizar(textos)
        for n, v in zip(pendientes + nuevos, vectores):
            n["vector"] = v

    ahora = datetime.now().isoformat(timespec="seconds")
    for n in nuevos:
        n.setdefault("senales", 1)
        n["nota"] = 8.0
        n["origen"] = "consolidacion %s" % SELLO
        n["creada"] = ahora

    # -- 5. escribir --------------------------------------------------------
    salida = nodos + nuevos
    with m._ruta.open("w", encoding="utf-8") as f:
        for n in salida:
            f.write(json.dumps({k: v for k, v in n.items() if not k.startswith("_")},
                               ensure_ascii=False) + "\n")
    log("memoria.jsonl: %d nodos (%d + %d nuevos)"
        % (len(salida), len(nodos), len(nuevos)))

    # -- 6. releer para comprobar que ella los puede leer -------------------
    comprobacion = Memoria(cfg, log=lambda *a: None)
    if len(comprobacion._vivas) != len(salida):
        log("!! ALGO VA MAL: escritos %d, releidos %d. Restauro la copia."
            % (len(salida), len(comprobacion._vivas)))
        if respaldo.exists():
            shutil.copy2(respaldo, m._ruta)
        return 1
    dims = {len(n.get("vector") or []) for n in comprobacion._vivas}
    if len(dims) != 1:
        log("!! vectores de dimensiones distintas %s. Restauro la copia." % dims)
        shutil.copy2(respaldo, m._ruta)
        return 1
    log("releidos %d nodos, todos de %d dimensiones" % (len(comprobacion._vivas),
                                                        dims.pop()))

    # -- 7. archivar la pre-memoria y marcar la fecha ----------------------
    archivar_pre(pre_ruta, len(particulas))
    marca.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    log("consolidacion terminada. La siguiente, dentro de %s h."
        % c.get("horas", 44))
    return 0


def archivar_pre(pre_ruta, n):
    """La pre-memoria consumida se guarda con fecha y se vacia. No se borra:
    si una consolidacion sale rara, ahi estan las particulas de las que
    salio."""
    if not pre_ruta.exists():
        return
    destino = RAIZ / "copias" / ("pre-memoria-%s.jsonl" % SELLO)
    destino.parent.mkdir(exist_ok=True)
    shutil.copy2(pre_ruta, destino)
    pre_ruta.write_text("", encoding="utf-8")
    log("pre-memoria archivada (%d particulas) en copias/%s" % (n, destino.name))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:                                # noqa: BLE001
        log("FALLO:", repr(err))
        # Sin marca: si esto ha reventado, que lo vuelva a intentar a la
        # siguiente hora en vez de esperar dos dias.
        sys.exit(1)
