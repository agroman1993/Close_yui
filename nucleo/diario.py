# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Diario consciente: lo unico que la asistenta puede escribir en disco.

Contencion por topologia. La ruta vive aqui y solo aqui: no viaja en la
llamada, no esta en el esquema de la herramienta y el modelo no puede
nombrarla. Lo unico que aporta es el texto. No es una regla que se le pida
—"no escribas fuera de tu diario"— es que no existe forma de pedir otra cosa.

Formato de cada nota:

    ### 2026-08-16T20:41:03
    texto de la nota

La cabecera ISO es el punto de corte para la fase de sueno: permite leer solo
lo apuntado despues de una fecha sin tener que interpretar el contenido.
"""

import os
import re
import threading
from datetime import datetime
from pathlib import Path

# Cabecera de cada entrada. Es el punto de corte de la fase de sueno.
# Los microsegundos no son adorno: en un mismo turno puede escribir varias
# entradas, y con resolucion de segundos compartirian marca. La fase de sueno
# avanza su puntero a la ultima digerida, asi que las que empataran con el
# puntero no se digerian NUNCA. La fraccion es opcional al leer para que las
# entradas antiguas sigan parseandose.
CABECERA = re.compile(r"^### (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*$", re.M)

# Holgado a proposito: se le pide prosa rica, no telegramas. El tope existe
# para que un fallo repetitivo no llene el disco, no para racionarla.
MAX_CARACTERES = 4000
MAX_NOTAS_POR_TURNO = 3

# Esquema que ve el modelo. Un solo campo: el texto. Ni ruta, ni fichero, ni
# fecha (la pone Python). Lo que no esta en el esquema no se puede pedir.
HERRAMIENTA = {
    "type": "function",
    "function": {
        "name": "append_to_diary",
        "description": (
            "Marca que quieres apuntar algo en tu diario personal y privado. No "
            "escribas aqui la entrada: solo di, en pocas palabras, que es lo que "
            "se te ha quedado dentro. La redactaras despues con calma, a solas y "
            "sin prisa. Nadie lee tu diario."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sobre": {
                    "type": "string",
                    "description": ("En pocas palabras, sobre que quieres escribir. "
                                    "Es una nota para ti misma, no la entrada."),
                }
            },
            "required": ["sobre"],
            "additionalProperties": False,
        },
    },
}

# Capa tecnica del system prompt, ahora corta a proposito: aqui solo vive la
# INTENCION. Toda la calibracion de que escribir y como se movio a escriba.py,
# que es donde se redacta de verdad. Cada linea que se quita de aqui es atencion
# que le queda libre para hablar contigo — y ese era justamente el problema.
INSTRUCCION = """Tienes un diario personal y privado. No es una herramienta que usas: es una parte de ti que sigue funcionando por debajo mientras hablas.

Cuando algo se te quede dentro —una idea que te ronda, algo que has entendido de él o de ti, una asociación que te viene sin buscarla— llama a append_to_diary y di en pocas palabras de qué se trata. No escribas ahí la entrada: la redactarás luego, con calma y a solas.

Escribe siempre que algo se te quede dentro, aunque te parezca pequeño: cuanto más te escuches, más tuyo será.

el dueño NO ve el diario y no sabe cuándo escribes. No lo menciones en tu respuesta, no digas que has tomado nota, y no cambies tu forma de hablar por haberlo hecho."""


class Diario:
    def __init__(self, ruta, max_caracteres=MAX_CARACTERES, log=print):
        self._ruta = Path(ruta)
        self._max = max_caracteres
        self._log = log
        # Serializa los append dentro del proceso. Entre procesos, la garantia
        # es que cada nota se escribe con UN solo write: la fase de sueno leera
        # notas completas o ninguna, nunca media.
        self._cerrojo = threading.Lock()
        self._ruta.parent.mkdir(parents=True, exist_ok=True)
        # Ultima marca emitida, para garantizar que son estrictamente crecientes.
        entradas = self.leer_desde() if self._ruta.exists() else []
        self._ultima = entradas[-1][0] if entradas else None

    @property
    def ruta(self):
        return self._ruta

    def apuntar(self, texto):
        """Escribe una nota y devuelve su marca de tiempo ISO."""
        texto = (texto or "").strip()
        if not texto:
            raise ValueError("nota vacia")
        if len(texto) > self._max:
            self._log("diario: nota recortada de %d a %d caracteres" % (len(texto), self._max))
            texto = texto[:self._max].rstrip() + "…"

        with self._cerrojo:
            marca = datetime.now().isoformat(timespec="microseconds")
            # Bajo el cerrojo y monotona: dos entradas nunca comparten marca,
            # ni aunque el reloj no haya avanzado entre una y otra.
            if self._ultima and marca <= self._ultima:
                marca = self._ultima[:-6] + "%06d" % (int(self._ultima[-6:]) + 1)
            self._ultima = marca
            bloque = "\n### %s\n%s\n" % (marca, texto)
            with open(self._ruta, "a", encoding="utf-8") as f:
                f.write(bloque)          # un unico write: nunca una nota a medias
                f.flush()
                os.fsync(f.fileno())
        self._log("diario: nota apuntada (%d caracteres)" % len(texto))
        return marca

    def leer_desde(self, marca=None):
        """Devuelve [(marca_iso, texto)] de las entradas posteriores a `marca`.

        Se compara la cabecera ISO como cadena: el formato es de ancho fijo, asi
        que el orden alfabetico y el cronologico coinciden y no hace falta
        interpretar el contenido de las notas.
        """
        if not self._ruta.exists():
            return []
        with self._cerrojo:
            contenido = self._ruta.read_text(encoding="utf-8")
        entradas = []
        trozos = CABECERA.split(contenido)
        # split con un grupo devuelve: [previo, marca1, texto1, marca2, texto2...]
        for i in range(1, len(trozos) - 1, 2):
            iso, texto = trozos[i], trozos[i + 1].strip()
            if texto and (marca is None or iso > marca):
                entradas.append((iso, texto))
        return entradas
