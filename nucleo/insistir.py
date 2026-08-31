# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Insistir hasta que salga. El codigo reintenta; el dueño no reescribe nada.

Encargo suyo, corregido el 29/08. Antes esto era un presupuesto en SEGUNDOS:
se insistia dos minutos y luego se le decia a el dueño que volviera a escribir.
Eso estaba mal por el lado que importa, que no es el tecnico: **no tiene
sentido pedirle a el que repita algo que la maquina sabe repetir sola**. El
prompt ya esta en el historial y Telegram no da el mensaje por entregado
hasta que hay respuesta, asi que no hay nada que el tenga que volver a
mandar. Nunca lo hubo.

Asi que ahora se insiste hasta que salga, y el unico freno es un tope de
INTENTOS, no de tiempo:

    si despues de 20 llamadas seguidas no ha contestado ni una, no es una
    racha mala: es que el endpoint se ha caido. Seguir llamando es chocarse
    contra un muro, y ahi si se para y se le dice.

El tope cuenta TODOS los intentos, cobrados o no. Podria parecer mas fino
perdonar los gratis —un 502 no cuesta nada, ¿por que iba a gastar tope?—,
pero seria justo al reves de lo que hace falta: el caso para el que existe
este freno, el endpoint caido, devuelve precisamente 5xx. Si los gratis no
contaran, el freno no saltaria nunca en el unico caso que tiene que cazar.

`gratis` se sigue marcando, pero ya solo para el log: sirve para saber, al
mirar una averia por la mañana, si costo dinero o no.

Con la espera creciendo hasta 30 segundos, 20 intentos son unos ocho minutos
de insistencia. No hay modelos de respaldo: si el modelo no esta, se espera.
"""

import time

# Ocho minutos largos de insistencia antes de admitir que el endpoint no esta.
INTENTOS_MAX = 20


class SinSuerte(Exception):
    """Se agotaron los intentos. Lleva dentro el ultimo fallo."""

    def __init__(self, ultimo, intentos, segundos):
        super().__init__(str(ultimo))
        self.ultimo = ultimo
        self.intentos = intentos
        self.segundos = segundos


class NoReintentable(Exception):
    """Este fallo no se cura repitiendo: hay que dejarlo ya.

    No todo fallo es transitorio. Una saturacion se pasa; que el modelo agote
    su presupuesto pensando ESTE texto concreto, no. Insistir ahi es el dia de
    la marmota: mismo contexto, misma tirada, y ocho minutos tirados para
    acabar diciendo lo mismo que se sabia al segundo intento.

    Quien decide que un fallo es de este tipo es `antes_de_reintentar`, que es
    el unico que ve como evoluciona.
    """

    def __init__(self, ultimo):
        super().__init__(str(ultimo))
        self.ultimo = ultimo


def insistir(accion, errores, intentos_max=INTENTOS_MAX, log=print, etiqueta="",
             antes_de_reintentar=None, al_tardar=None, avisar_desde=3):
    """Repite `accion()` hasta que salga o hasta `intentos_max` seguidos.

    - `errores`: tupla de excepciones que se consideran reintentables.
    - `antes_de_reintentar(err, intento)`: gancho para ajustar algo entre
      intentos (subir el techo de tokens). Puede lanzar NoReintentable.
    - `al_tardar()`: se llama UNA vez, al llegar a `avisar_desde` intentos.
      Sirve para avisar a el dueño de que esto va para largo. No se llama en un
      hipo de uno o dos intentos, que se resuelve solo en cuatro segundos y
      no merece ni mencionarse.

    La espera entre intentos crece (2, 4, 8, 16, 30, 30...) hasta un tope de
    30 segundos.
    """
    espera = 2
    intento = 0
    arranque = time.monotonic()
    avisado = False
    while True:
        intento += 1
        try:
            return accion()
        except errores as err:
            transcurrido = int(time.monotonic() - arranque)
            if intento >= intentos_max:
                # Veinte llamadas seguidas sin una sola respuesta. Esto ya no
                # es una racha: el endpoint no esta.
                raise SinSuerte(err, intento, transcurrido) from err
            if antes_de_reintentar:
                try:
                    antes_de_reintentar(err, intento)
                except NoReintentable as corte:
                    log("%s se corta en el intento %d: no se arregla insistiendo"
                        % (etiqueta, intento))
                    raise SinSuerte(corte.ultimo, intento, transcurrido) from err
                except Exception:                     # noqa: BLE001
                    pass
            if al_tardar and not avisado and intento >= avisar_desde:
                avisado = True
                try:
                    al_tardar()
                except Exception:                     # noqa: BLE001
                    pass                              # avisar nunca tumba el turno
            log("%s intento %d/%d fallido%s (%s); reintento en %ds"
                % (etiqueta, intento, intentos_max,
                   " [gratis]" if getattr(err, "gratis", False) else "",
                   err, espera))
            time.sleep(espera)
            espera = min(espera * 2, 30)
