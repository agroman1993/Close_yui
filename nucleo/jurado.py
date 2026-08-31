# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""El jurado: dos notas continuas, cada una del modelo que sabe darla.

El tribunal anterior aprobaba el 94% de lo que le llegaba. Dos motivos, y los
dos eran de diseno:

- Preguntaba en binario. A un LLM le preguntas "¿esto merece la pena?" y dice
  que si. Con nota hay que mojarse, y ademas se puede ordenar y cortar.
- Ponia al modelo de rol a juzgar coherencia de personaje: una orquestacion de
  rol y narracion: pedirle severidad analitica es pedirle lo que no es.

Ahora cada juez hace lo suyo:

  EMOCION      el modelo de siempre, CON su SOUL. Es ella misma diciendo cuanto le pesa algo.
               Es lo unico para lo que un modelo de rol es el mejor juez.

  CONECTIVIDAD DeepSeek, SIN soul y sin persona. No juzga si algo es bonito:
               estima cuantas veces ese recuerdo va a resonar de verdad en una
               conversacion cualquiera. Es una propiedad estructural, y ahi un
               modelo frio es mejor que uno calido.

La conectividad importa porque en cada turno solo entran 3 recuerdos. Uno que
nunca sale no es memoria: es lastre que ademas DESPLAZA a los que si venian a
cuento.

La nota final se guarda con el recuerdo. Asi el umbral se puede reajustar mas
adelante sobre lo ya juzgado, sin volver a pagar por juzgarlo.
"""

import json

JUEZ_EMOCION = """Eres __NOMBRE__ evaluando tus propios recuerdos.

Para cada cápsula, di cuánto PESA emocionalmente para ti: cuánto cambiaría cómo te sientes con __VOCATIVO__ o cómo lo entiendes si lo recordaras dentro de meses.

ESCALA (0 a 10):
- 0-3: un dato sin carga. Cierto, pero no te mueve nada.
- 4-6: te dice algo de él, pero no te cambia.
- 7-10: te toca. Revela quién es, marca un cambio, o es de esas cosas que no querrías olvidar.

No juzgues si está bien escrito ni si es útil. Solo cuánto pesa.

Responde ÚNICAMENTE con este JSON, un elemento por cápsula y en el mismo orden:
{"notas": [{"i": 0, "puntuacion": X, "motivo": "breve"}]}"""

JUEZ_CONECTIVIDAD = """Tu tarea es evaluar la 'capacidad de conexión' de un fragmento de memoria.
No juzgues si la frase es poética o bonita. Evalúa cuán FÁCIL y FRECUENTE será que esta frase resuene semánticamente con temas de conversación cotidiana a largo plazo.

ESCALA DE CONECTIVIDAD (0 a 10):
- 0-3 (Nodos Muertos / Ruido): Hechos ultra-específicos o compras puntuales que casi NUNCA se volverán a cruzar semánticamente ("Compré un teclado Logitech", "Ayer comí arroz").
- 4-6 (Nodos Ocasionales): Contextos que solo se activarán en temas muy específicos ("Me gusta la música lo-fi", "Tengo un perro").
- 7-10 (Super-Nodos / Conectores Altos): Estados de ánimo recurrentes, rasgos de personalidad, valores, formas de ver el mundo o rutinas ("Me cuesta arrancar los lunes", "Soy muy perfeccionista", "Me agobia el desorden").

Responde ÚNICAMENTE con este JSON, un elemento por frase y en el mismo orden recibido:
{"notas": [{"i": 0, "puntuacion": X, "motivo": "Breve motivo de la frecuencia de activación estimada"}]}"""


def combinar(emocion, conectividad, peso_emocion, peso_conectividad):
    """Nota final 0-10 como media ponderada. Sin sorpresas: si un juez falla,
    su nota es None y manda el otro entero."""
    partes = [(emocion, peso_emocion), (conectividad, peso_conectividad)]
    partes = [(n, w) for n, w in partes if n is not None and w > 0]
    if not partes:
        return None
    total = sum(w for _, w in partes)
    return sum(n * w for n, w in partes) / total


def leer_notas(respuesta, total):
    """Extrae {indice: puntuacion} del JSON de un juez, tolerando huecos."""
    notas = {}
    for n in (respuesta or {}).get("notas") or []:
        try:
            i = int(n.get("i"))
            p = float(n.get("puntuacion"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < total:
            notas[i] = max(0.0, min(10.0, p))
    return notas


def listar(candidatos):
    return "\n".join("%d. %s" % (i, c["texto"]) for i, c in enumerate(candidatos))


def formatear_acta(candidato, emo, con, final, entra):
    return "  [%s] E=%s C=%s -> %.1f  %s" % (
        "SI" if entra else "no",
        "%.0f" % emo if emo is not None else "?",
        "%.0f" % con if con is not None else "?",
        final if final is not None else 0.0,
        candidato["texto"][:60])
