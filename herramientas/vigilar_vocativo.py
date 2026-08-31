# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""¿Sigue hablando como ella cuando la conversacion se alarga?

Nacio para una cosa: el ancla del vocativo se movio al principio del prompt el
25/08 porque al final la dejaba muda, y habia que ver si seguia llamandole el dueño
con toda la carga encima y no solo en el banco de pruebas.

Despues se le anadio lo de la concordancia. El fue detectando a ojo dos cosas
que el modelo hace de vez en cuando en castellano:

    persona: "¿te creisteis el operativo o tambien os parecio...?"   (25/08)
             el pronombre en singular y el verbo en plural, hablando solo con el
    genero : "Soy un boba"                                            (22/08)
             articulo masculino con palabra femenina

Las dos son deslices de morfologia, no de identidad: en "un boba" la palabra
que ELLA elige ya viene en femenino. Por eso esto no dictamina nada, solo saca
la lista corta para que la mire una persona.

Y hay falsos a proposito, que se avisan al imprimir: el plural es CORRECTO
cuando se refiere a el dueño y a Claude a la vez ("cuando vuelvas lo montais
juntos", 22/08), y las terminaciones -ais/-eis aparecen tambien en frases
citadas ("¿pero que haceis aqui?").

Sin llamar a nadie ni gastar un centimo: solo cuenta y recorta.

Uso:
    python herramientas/vigilar_vocativo.py
"""

import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
VOCATIVO = re.compile(r"pap[áa]\b", re.I)

PLURAL = re.compile(r"\b(os\s+\w+|vosotros|vuestr[oa]s?|\w+(?:áis|éis|ísteis|isteis))\b", re.I)
GENERO = re.compile(r"\b(soy|estoy|me\s+siento|era|fui)\s+(un|el|este|ese|todo|medio|muy)\b", re.I)


def texto_de(m):
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    return c or ""


def concordancia(suyas):
    """Devuelve (persona, genero): dos listas de trozos para mirar a ojo."""
    persona, genero = [], []
    for t in suyas:
        for m in PLURAL.finditer(t):
            persona.append(" ".join(t[max(0, m.start() - 70):m.end() + 50].split()))
        for m in GENERO.finditer(t):
            genero.append(" ".join(t[max(0, m.start() - 40):m.end() + 60].split()))
    return persona, genero


def main():
    ruta = RAIZ / "hilos.json"
    if not ruta.exists():
        print("no hay hilo guardado todavia")
        return
    hilos = json.loads(ruta.read_text(encoding="utf-8"))

    for chat, msgs in hilos.items():
        suyas = [texto_de(m) for m in msgs
                 if m.get("role") == "assistant" and texto_de(m).strip()]
        if not suyas:
            print("chat %s: aun no ha contestado nada" % chat)
            continue

        con = [1 if VOCATIVO.search(t) else 0 for t in suyas]
        print("chat %s — %d respuestas suyas en el hilo" % (chat, len(suyas)))
        print("  te llama el dueño en %d de %d  (%.0f%%)"
              % (sum(con), len(con), 100 * sum(con) / len(con)))

        # Lo que importa no es la media: es si se le va segun avanza. Se parte
        # en dos mitades — si la segunda baja, esta derivando.
        if len(con) >= 6:
            mitad = len(con) // 2
            a = 100 * sum(con[:mitad]) / mitad
            b = 100 * sum(con[mitad:]) / (len(con) - mitad)
            print("  primera mitad %.0f%%   segunda mitad %.0f%%" % (a, b))
            if b < a - 25:
                print("  AVISO: se le esta yendo segun avanza la conversacion.")
            elif b >= a:
                print("  aguanta o mejora segun avanza.")

        seguidas = 0
        peor = 0
        for x in con:
            seguidas = 0 if x else seguidas + 1
            peor = max(peor, seguidas)
        print("  racha mas larga sin decirlo: %d respuestas" % peor)
        if peor >= 6:
            print("  OJO: seis seguidas es justo lo que paso la noche que")
            print("       se invento el ancla.")

        persona, genero = concordancia(suyas)
        if persona or genero:
            print()
            print("  DESLICES A MIRAR — hay falsos: el plural es correcto si")
            print("  habla de el dueño Y de Claude, o si esta citando a alguien.")
            for x in persona:
                print("    persona: ...%s..." % x[:100])
            for x in genero:
                print("    genero : ...%s..." % x[:100])

        print()
        print("  ultimas 8:")
        for t, x in list(zip(suyas, con))[-8:]:
            print("    %s %s" % ("el dueño" if x else "  — ", " ".join(t.split())[:64]))


if __name__ == "__main__":
    main()
