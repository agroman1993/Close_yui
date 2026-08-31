# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Pasa todos los medios por todos los modelos y exporta los JSON al escritorio.

Genera dos ficheros:
  comparativa_extractores.json  datos completos (crudo incluido)
  comparativa_extractores.md    lectura comoda para comparar a ojo

Uso:
    python pruebas/exportar.py           # plan, sin llamar
    python pruebas/exportar.py --run
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from banco_visual import MEDIOS, RAIZ, RECORDATORIO, SISTEMA, bloque  # noqa: E402
from comparar import CASOS as CASOS_BASE, evaluar, llamar  # noqa: E402

ESCRITORIO = Path.home() / "Desktop"
# Edita esta lista con los modelos que quieras probar.
MODELOS = ["PROVEEDOR/MODELO-A", "PROVEEDOR/MODELO-B"]

CASOS = list(CASOS_BASE) + [
    # Añade aqui casos extra con rutas absolutas a tus propios medios:
    # ("nombre del caso", r"ruta\\absoluta\\a\\tu\\medio.mp4", None),
]


def ruta_de(fichero):
    p = Path(fichero)
    return p if p.is_absolute() else MEDIOS / fichero


def main():
    cfg = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))["modelo"]

    if "--run" not in sys.argv:
        print("modelos: %s" % ", ".join(MODELOS))
        for n, f, _ in CASOS:
            p = ruta_de(f)
            tam = "{:,} bytes".format(p.stat().st_size) if p.exists() else "NO EXISTE"
            print("  %-32s %s" % (n, tam))
        print("\n%d llamadas. ejecutar con --run" % (len(CASOS) * len(MODELOS)))
        return

    salida = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "prompt_sistema": SISTEMA,
        "recordatorio_en_turno_usuario": RECORDATORIO,
        "modelos": MODELOS,
        "resultados": [],
    }

    for nombre, fichero, marca in CASOS:
        ruta = ruta_de(fichero)
        for modelo_id in MODELOS:
            print("-> %-32s %s" % (nombre, modelo_id), flush=True)
            texto, err, uso, secs = llamar(cfg, modelo_id, [bloque(ruta)])
            fila = {
                "caso": nombre,
                "fichero": ruta.name,
                "bytes": ruta.stat().st_size,
                "modelo": modelo_id,
                "segundos": round(secs, 1),
                "tokens_entrada": uso.get("prompt_tokens"),
                "tokens_salida": uso.get("completion_tokens"),
            }
            if err:
                fila["error"] = err
                print("   FALLO: %s" % err, flush=True)
            else:
                obj, v = evaluar(nombre, marca, texto)
                fila["json"] = obj
                fila["crudo"] = texto
                fila["veredictos"] = {e: b for e, b in v}
                print("   %.1fs  %s/%s tok  %s" % (
                    secs, uso.get("prompt_tokens"), uso.get("completion_tokens"),
                    " ".join(("OK:" if b else "FALLO:") + e.replace(" ", "_") for e, b in v)), flush=True)
            salida["resultados"].append(fila)
            time.sleep(1)

    destino = ESCRITORIO / "comparativa_extractores.json"
    destino.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nJSON completo -> %s" % destino)

    # Version legible
    lineas = ["# Comparativa de extractores visuales", "",
              "Generado: %s" % salida["generado"], "",
              "Mismo system prompt y mismo recordatorio para todos.", ""]
    for nombre, fichero, _ in CASOS:
        ruta = ruta_de(fichero)
        lineas += ["", "---", "", "## %s" % nombre,
                   "", "`%s` — %s bytes" % (ruta.name, "{:,}".format(ruta.stat().st_size)), ""]
        for fila in salida["resultados"]:
            if fila["caso"] != nombre:
                continue
            lineas += ["### %s" % fila["modelo"], ""]
            if "error" in fila:
                lineas += ["FALLO: %s" % fila["error"], ""]
                continue
            v = fila.get("veredictos") or {}
            lineas += ["%ss · %s tokens entrada / %s salida · %s" % (
                fila["segundos"], fila["tokens_entrada"], fila["tokens_salida"],
                ", ".join(("OK " if b else "FALLA ") + e for e, b in v.items())), "",
                "```json", json.dumps(fila["json"], ensure_ascii=False, indent=2), "```", ""]
    destino_md = ESCRITORIO / "comparativa_extractores.md"
    destino_md.write_text("\n".join(lineas), encoding="utf-8")
    print("Lectura comoda -> %s" % destino_md)


if __name__ == "__main__":
    main()
