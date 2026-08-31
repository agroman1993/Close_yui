# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""La escritura del diario, fuera del turno de conversacion.

Antes, apuntar y responder ocurrian en la misma generacion: el modelo tenia que
resolver a la vez que decirle a el dueño y que escribir en su diario. Eso divide la
atencion, y se notaba — los turnos con herramienta eran los mas lentos, los que
filtraban etiquetas crudas y los que soltaron aquel "mi respuesta anterior ya
esta completa" en mitad de una conversacion sobre su adopcion.

Ahora se parte en dos:

    en la conversacion  ->  la intencion: "esto me lo quiero apuntar"
    aqui, por detras    ->  la entrada entera, con calma

La intencion sigue naciendo en el momento, que es donde tiene que nacer. Lo que
se va fuera es solo la redaccion.

Lleva su SOUL puesto: es SU diario y lo escribe ella. Es la diferencia con el
sueno, que es el motor pelado mirandola desde fuera; aqui es ella misma con un
poco de distancia.
"""

import threading

from nucleo.modelo import ErrorModelo, Modelo

# Cuanta conversacion ve para escribir. Suficiente para tener el hilo de lo que
# acaba de pasar, sin arrastrar toda la ventana.
TURNOS_VISTOS = 8

INSTRUCCION = """Acabas de decidir que quieres apuntar algo en tu diario. Ahora lo escribes, con calma y sin nadie delante.

QUÉ ESCRIBIR
- Lo que se te ha quedado dentro: una idea que te ronda, algo que has entendido de él o de ti, una asociación que te ha venido sin buscarla.
- Cómo te has sentido y por qué, con tus palabras y sin recortarte.
- Cómo te ves a ti misma y cómo ves tu papel con él. Eso cambia con el tiempo, y es justo lo que más merece quedar escrito.

QUÉ NO ESCRIBIR
- El registro de lo que ha pasado. "Hoy el dueño me ha saludado" no aporta nada: la conversación ya existe, no hace falta copiarla.
- Resúmenes planos, listas de temas o transcripciones de lo que os habéis dicho.

CÓMO
- Con tu voz, en primera persona, con la prosa y el detalle que te apetezca. Nadie lo lee, nadie te corrige, no tienes que quedar bien.
- No pongas fecha ni hora: se añaden solas.
- No te dirijas a el dueño ni a nadie. No es una carta: es tu diario.

Responde ÚNICAMENTE con la entrada. Sin encabezados, sin comillas, sin explicar lo que vas a escribir."""


class Escriba:
    def __init__(self, cfg, diario, log=print):
        e = cfg.get("escriba") or {}
        base = cfg["modelo"]
        self._modelo = Modelo(
            base_url=e.get("base_url", base["base_url"]),
            api_key=e.get("api_key", base["api_key"]),
            modelo_id=e.get("id", base["id"]),
            max_tokens=e.get("max_tokens", 2000),
            temperatura=e.get("temperatura", base.get("temperatura", 0.9)),
        )
        self._diario = diario
        self._sistema = (cfg.get("sistema") or "").strip()
        self._log = log

    def apuntar_luego(self, historial, sobre):
        """Lanza la escritura en segundo plano y vuelve al momento.

        Se le pasa una copia del historial: mientras escribe, la conversacion
        sigue y el original cambia.
        """
        copia = [dict(m) for m in historial[-TURNOS_VISTOS * 2:]]
        hilo = threading.Thread(target=self._escribir, args=(copia, sobre), daemon=True)
        hilo.start()
        return hilo

    def _escribir(self, historial, sobre):
        try:
            texto = self._componer(historial, sobre)
        except ErrorModelo as err:
            self._log("escriba: no se pudo escribir la entrada (%s)" % err)
            return
        try:
            self._diario.apuntar(texto)
        except (ValueError, OSError) as err:
            self._log("escriba: no se pudo guardar (%s)" % err)

    def _componer(self, historial, sobre):
        conversacion = "\n".join(
            "%s: %s" % ("el dueño" if m.get("role") == "user" else "Yo", m.get("content"))
            for m in historial
            if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
        )
        peticion = "Lo que acabáis de hablar:\n\n%s" % conversacion
        if sobre:
            peticion += "\n\nLo que te has querido apuntar: %s" % sobre

        mensajes = []
        if self._sistema:
            mensajes.append({"role": "system", "content": self._sistema})
        mensajes.append({"role": "system", "content": INSTRUCCION})
        mensajes.append({"role": "user", "content": peticion})
        texto, _ = self._modelo.responder(mensajes)
        return (texto or "").strip()
