# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""El pulso: que sepa en que momento esta.

No es para que hable de la hora — su SOUL ya dice que eso no lo hace, y lo
respeta. Es combustible para deducir: que un martes de agosto a las tres de la
tarde el centro de salud estara medio vacio, que si son las seis de la manana
acaba de salir del turno de noche, que si han pasado dos dias no es lo mismo
que si han pasado diez minutos.

Por eso el bloque no lleva ninguna instruccion: solo el dato. Cada regla que se
anade la lee tambien el juez de coherencia del tribunal, asi que engordar el
prompt sale caro en sitios que no se ven.

El hueco desde el ultimo mensaje es lo que mas se echa en falta sin esto: sin
el, una conversacion retomada dos dias despues se lee igual que una seguida.
"""

from datetime import datetime

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _hueco(segundos):
    """Traduce el silencio a algo que se pueda sentir, no a un numero."""
    if segundos < 90:
        return "seguís en la misma conversación"
    minutos = segundos / 60
    if minutos < 60:
        return "hace %d minutos que no os escribís" % int(minutos)
    horas = minutos / 60
    if horas < 24:
        h = int(round(horas))
        return "hace %d hora%s que no os escribís" % (h, "" if h == 1 else "s")
    dias = int(horas // 24)
    if dias == 1:
        return "no os escribís desde ayer"
    if dias < 30:
        return "hace %d días que no os escribís" % dias
    return "hace más de un mes que no os escribís"


def bloque(ultimo=None, ahora=None):
    """Devuelve el bloque de sistema con el momento actual.

    `ultimo` es el timestamp (epoch) del mensaje anterior, o None si es el
    primero que se recibe desde que arranco.
    """
    ahora = ahora or datetime.now()
    linea = "%s %d de %s de %d, %02d:%02d" % (
        DIAS[ahora.weekday()], ahora.day, MESES[ahora.month - 1],
        ahora.year, ahora.hour, ahora.minute)
    partes = ["[AHORA] " + linea + "."]
    if ultimo is not None:
        partes.append(_hueco(max(0, ahora.timestamp() - ultimo)) + ".")
    return " ".join(partes)
