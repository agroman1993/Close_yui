# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""MEMORY.md: lo que el dueño le pide que se apunte, con su letra y a mano.

Encargo suyo del 29/08. Es el tercer tipo de memoria y no se parece a los
otros dos:

    USER.md        su ficha. Lo estable que no cambia nunca. Lo escribe el.
    memoria.jsonl  lo que ella deduce sola de hablar. Vectores y parecido.
    MEMORY.md      lo que el le dice EXPLICITAMENTE que se apunte. <- esto

La diferencia con la memoria vectorial no es tecnica, es de intencion: lo de
memoria.jsonl ella lo dedujo; esto se lo dijeron. Y a diferencia del diario
que se apago el 29/08, esto no se dispara solo ni le come turnos: se dispara
unas pocas veces al dia, cuando el lo pide.

CONTENCION POR TOPOLOGIA, que es lo unico que hace que esto sea seguro:

    Las herramientas de escribir y borrar NO EXISTEN salvo en el turno en que
    el lo ha pedido. No se le dice "solo apunta cuando te lo pidan": es que en
    los otros turnos no hay nada que llamar. El modelo no puede decidir
    apuntar por su cuenta porque no tiene con que.

    Quien abre esa puerta es un regex sobre lo que el escribe, en Python,
    antes de llamar a nadie. Ver `pide_apunte()`.

Y el tope de lineas no es manía: este fichero entra ENTERO en su prompt en
cada turno. Sin tope, en un mes son 4000 tokens fijos por mensaje y habriamos
recreado por otro lado el problema que resolvimos el 28/08 quitando la
dependencia del diario.
"""

import os
import re
import threading
from pathlib import Path

# El fichero entra entero en el prompt en CADA turno. Con 60 lineas de
# apuntes normales son unos 900 tokens; a partir de ahi se avisa en el log
# para poder podarlo a mano antes de que se note en la factura.
MAX_LINEAS = 60
MAX_CARACTERES = 4000

# La puerta. Si esto no casa con lo que el dueño acaba de escribir, las
# herramientas ni se le pasan al modelo.
#
# Deliberadamente generoso: el escribe rapido y con dislexia, asi que no se
# exigen tildes ni ortografia. Un falso positivo aqui es barato —la
# herramienta esta disponible y ella no la usa— y un falso negativo se
# arregla diciendolo otra vez o con /apunta.
PIDE_APUNTE = re.compile(
    r"\b("
    r"ap[uú]nta(te|telo|lo|te\s+esto)?"
    r"|anota(lo|te)?"
    r"|gu[aá]rda(te|lo)?\s+(esto|eso)"
    r"|acu[eé]rdate"
    r"|recuerda\s+(esto|eso|que)"
    r"|no\s+se\s+te\s+olvide"
    r"|met[ea]\s+esto\s+en\s+(tu\s+)?memoria"
    r"|(a[ñn]ade|agrega)\s+(esto|eso)\s+a\s+(tu\s+)?memoria"
    r"|olvida(te)?\s+(de\s+)?(esto|eso|lo\s+de)"
    r"|borra\s+(esto|eso|lo\s+de)"
    r"|quita\s+(esto|eso|lo\s+de)\s+de\s+(tu\s+)?memoria"
    r")\b", re.I)

# Y el camino garantizado, por si el regex no lo caza.
COMANDO = re.compile(r"^\s*/(apunta|olvida)\b", re.I)

# Los comentarios HTML del fichero son instrucciones para el dueño, no para ella.
# Se quitan ENTEROS: saltar solo las lineas que empiezan por "<!--" dejaba
# pasar el cuerpo del comentario al prompt (cazado el 29/08 porque el contador
# decia 17 apuntes donde habia 7).
COMENTARIO = re.compile(r"<!--.*?-->", re.S)


def pide_apunte(texto):
    """¿el dueño esta pidiendo que se apunte o que se olvide algo?

    Se mira SOLO lo que escribe el, nunca lo que escribe ella. Si esto
    mirara la respuesta del modelo, el modelo podria abrirse la puerta solo
    escribiendo 'apúntate esto', y entonces la contencion no seria contencion.
    """
    t = texto or ""
    return bool(COMANDO.match(t) or PIDE_APUNTE.search(t))


APUNTAR = {
    "type": "function",
    "function": {
        "name": "apuntar_en_memoria",
        "description": (
            "Apunta algo en tu memoria escrita, la que el dueño te pide que guardes. "
            "Úsala cuando él te haya pedido que te apuntes o recuerdes algo. "
            "Escribe el apunte con tus palabras, en una o dos frases que se "
            "entiendan solas dentro de meses, sin depender de esta conversación."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {
                    "type": "string",
                    "description": "El apunte, en una o dos frases autocontenidas.",
                }
            },
            "required": ["texto"],
            "additionalProperties": False,
        },
    },
}

OLVIDAR = {
    "type": "function",
    "function": {
        "name": "olvidar_de_memoria",
        "description": (
            "Borra de tu memoria escrita los apuntes que hablen de algo. Úsala "
            "solo cuando el dueño te pida olvidar o quitar algo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sobre": {
                    "type": "string",
                    "description": ("Unas palabras de lo que hay que borrar. Se "
                                    "borran los apuntes que las contengan."),
                }
            },
            "required": ["sobre"],
            "additionalProperties": False,
        },
    },
}

# Va en su prompt junto al contenido del fichero.
BLOQUE = ("[MEMORIA ESCRITA] Cosas que el dueño te ha pedido expresamente que "
          "apuntes. No son deducciones tuyas: te las dijo él. Úsalas cuando "
          "vengan a cuento, sin recitarlas.")


class ErrorApuntes(Exception):
    pass


class Apuntes:
    """El fichero MEMORY.md. La ruta vive aqui y solo aqui: no viaja en la
    llamada ni esta en el esquema, asi que el modelo no puede nombrar otra."""

    def __init__(self, ruta="MEMORY.md", log=print, persona=None):
        self._ruta = Path(ruta)
        self._log = log
        self._cerrojo = threading.Lock()
        nombre, vocativo = persona or ("Yui", "Papá")
        self._cabecera = CABECERA.replace("__NOMBRE__", nombre).replace("__VOCATIVO__", vocativo)

    # -- lectura -----------------------------------------------------------

    def lineas(self):
        if not self._ruta.exists():
            return []
        crudo = self._ruta.read_text(encoding="utf-8")
        # El comentario se quita ENTERO, no linea a linea. Saltar solo las
        # que empiezan por "<!--" dejaba pasar el cuerpo del comentario, y
        # las instrucciones para el dueño acababan en el prompt de ella.
        crudo = COMENTARIO.sub("", crudo)
        fuera = []
        for l in crudo.splitlines():
            l = l.strip()
            if l and not l.startswith("#"):
                fuera.append(l.lstrip("- ").strip())
        return [l for l in fuera if l]

    def como_bloque(self):
        ls = self.lineas()
        if not ls:
            return None
        return BLOQUE + "\n\n" + "\n".join("- %s" % l for l in ls)

    # -- escritura ---------------------------------------------------------

    def apuntar(self, texto):
        texto = " ".join((texto or "").split())
        if len(texto) < 5:
            raise ErrorApuntes("el apunte está vacío")
        with self._cerrojo:
            ls = self.lineas()
            # Nada de duplicados literales: el mismo apunte dos veces solo
            # ocupa sitio en el prompt.
            if any(texto.lower() == l.lower() for l in ls):
                return "Ya lo tenías apuntado, no lo he repetido."
            ls.append(texto)
            self._escribir(ls)
            aviso = ""
            if len(ls) > MAX_LINEAS:
                aviso = (" (Aviso: ya van %d apuntes, más de los %d "
                         "recomendados; conviene podar.)" % (len(ls), MAX_LINEAS))
                self._log("apuntes: %d lineas, por encima del tope de %d"
                          % (len(ls), MAX_LINEAS))
            self._log("apuntes: +1 (%d en total) — %s" % (len(ls), texto[:70]))
            return "Apuntado.%s" % aviso

    def olvidar(self, sobre):
        sobre = " ".join((sobre or "").split()).lower()
        if len(sobre) < 3:
            raise ErrorApuntes("no has dicho qué hay que olvidar")
        with self._cerrojo:
            ls = self.lineas()
            quedan = [l for l in ls if sobre not in l.lower()]
            fuera = len(ls) - len(quedan)
            if not fuera:
                return "No encuentro nada apuntado sobre eso."
            self._escribir(quedan)
            self._log("apuntes: -%d (%d en total) — sobre '%s'"
                      % (fuera, len(quedan), sobre[:50]))
            return "Borrado (%d apunte%s)." % (fuera, "s" if fuera > 1 else "")

    def _escribir(self, lineas):
        texto = self._cabecera + "\n".join("- %s" % l for l in lineas) + "\n"
        if len(texto) > MAX_CARACTERES * 2:
            raise ErrorApuntes("el fichero se ha ido de tamaño; hay que podarlo a mano")
        tmp = self._ruta.with_suffix(".tmp")
        tmp.write_text(texto, encoding="utf-8")
        with open(tmp, "r+", encoding="utf-8") as f:
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self._ruta)


CABECERA = """<!--
MEMORY.md — lo que __VOCATIVO__ le ha pedido a __NOMBRE__ que se apunte.

Esto lo escribe ELLA cuando tú se lo pides ("apúntate esto", "acuérdate
de..."), y también puedes editarlo tú a mano: es un markdown normal.

Fuera de esos turnos ella NO puede tocarlo. Las herramientas de escribir y
borrar solo se le pasan al modelo cuando tu mensaje pide un apunte; el resto
del tiempo no existen.

Ojo con el tamaño: esto entra entero en su prompt en CADA mensaje. Por encima
de 60 líneas empieza a avisar en el log.

Todo lo que haya fuera de este comentario entra en su prompt tal cual.
-->
"""
