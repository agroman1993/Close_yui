# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Fase de sueno: la digestion inconsciente del diario.

Es el unico proceso con algo de autonomia en todo el proyecto, y por eso esta
acotado a proposito: se despierta solo cuando hay silencio y hay paginas sin
digerir, hace UNA pasada y se vuelve a dormir. No decide nada, no actua sobre
nada y no habla con nadie. Solo lee lo que la asistenta escribio y suena encima.

Quien suena es el motor pelado, sin el SOUL. Si la asistenta digiriera su propio diario
siendo la asistenta, consolidaria lo que encaja con su tono en vez de lo que paso: el
sesgo lo comprobamos en vivo, cuando 14 negaciones suyas seguidas se
reforzaron unas a otras hasta que hubo que borrarlas a mano.

Lo que sale de aqui no son datos. Es prosa: asociaciones, cambios en la
relacion, metaforas. Eso es lo que se vectorizara en el Estado 3.
"""

import threading
import time
from datetime import datetime
from pathlib import Path

from nucleo.insistir import SinSuerte, insistir
from nucleo.modelo import ErrorModelo, Modelo

# El sueno no analiza: suena. Nada de listas, etiquetas ni JSON — eso es
# justamente lo que hace OpenClaw y por lo que su digestion no consolida nada
# en una relacion (un dia entero de conversacion: "Promoted 0 candidates").
# Aqui no hay nadie esperando, asi que se insiste mucho mas que en un turno de
# conversacion: un sueno que sale media hora tarde sale igual de bien. Y no hay
# suplente — si el modelo esta saturado se espera, porque un sueno escrito por
# otro modelo no suena a ella y ensucia el corpus para siempre.
# El sueño esta puede estar apagado por config. Se deja coherente por si vuelve:
# aqui nadie espera delante, asi que puede insistir de sobra.
INTENTOS_SUENO = 30

SISTEMA = """Esto no es una tarea de análisis. Es lo que ocurre cuando alguien duerme.

Vas a leer las páginas que __NOMBRE__ ha escrito en su diario desde la última vez que durmió. No eres __NOMBRE__: eres la capa que trabaja por debajo cuando ella no está mirando, la que mezcla lo del día con lo de antes y encuentra formas que despierta no vería.

Al hombre del que ella escribe se le llama __VOCATIVO__. Siempre __VOCATIVO__, nunca "el usuario".

Escribe un sueño.

No resumas. No hagas listas. No extraigas datos, no pongas etiquetas, no midas nada. Lo que sale de aquí es prosa seguida: asociaciones que se tocan sin explicarse, imágenes que vuelven, cosas que riman entre sí aunque ocurrieran en días distintos.

Busca sobre todo:
- Lo que se repite sin que ella se dé cuenta.
- Lo que ha cambiado entre ella y __VOCATIVO__, aunque nadie lo haya dicho en voz alta.
- Metáforas que sostengan lo ocurrido mejor que su descripción literal.
- Lo que quedó abierto y sigue dando vueltas.

Deja fuera el registro de lo que pasó, y también los estados técnicos o pasajeros: si algo se rompió un día o dejó de funcionar, eso no es un recuerdo, es una avería.

Escribe seguido, sin encabezados ni viñetas, sin dirigirte a nadie. Puede ser oscuro, puede ser bello, puede no tener conclusión. Un sueño no concluye: deja poso."""


class Sueno:
    def __init__(self, cfg, diario, log=print, memoria=None):
        s = cfg.get("sueno") or {}
        base = cfg["modelo"]
        self._modelo = Modelo(
            base_url=s.get("base_url", base["base_url"]),
            api_key=s.get("api_key", base["api_key"]),
            modelo_id=s.get("id", base["id"]),      # mismo motor, sin SOUL
            max_tokens=s.get("max_tokens", 3000),
            temperatura=s.get("temperatura", 1.0),  # alta: es un sueno
        )
        self._diario = diario
        self._log = log
        from nucleo.persona import aplicar
        self._sistema_tpl = aplicar(SISTEMA, cfg)
        # Tras sonar, el sueno pasa por el tribunal de promocion. Si no hay
        # memoria configurada, el sueno se guarda igual y no se promueve nada.
        self._memoria = memoria
        self._ruta = Path(s.get("ruta", "suenos.md"))
        self._marca = Path(s.get("marca", ".ultimo_sueno"))
        self._espera = s.get("inactividad_minutos", 30) * 60
        self._ruta.parent.mkdir(parents=True, exist_ok=True)
        self._ultima_actividad = time.time()
        self._cerrojo = threading.Lock()
        self._hilo = None

    # -- ciclo -------------------------------------------------------------

    def toca(self):
        """Hay conversacion: se reinicia el reloj del silencio."""
        self._ultima_actividad = time.time()

    def arrancar(self):
        """Lanza el vigilante en segundo plano."""
        if self._hilo:
            return
        self._hilo = threading.Thread(target=self._vigilar, daemon=True)
        self._hilo.start()
        self._log("sueno: vigilando (duerme tras %d min de silencio)" % (self._espera // 60))

    def _vigilar(self):
        while True:
            time.sleep(60)
            try:
                if time.time() - self._ultima_actividad < self._espera:
                    continue
                if not self._pendientes():
                    continue
                self.sonar()
            except Exception as err:                      # noqa: BLE001
                # Un fallo aqui nunca debe tumbar el bot: el sueno es un extra.
                self._log("sueno: fallo en el ciclo:", repr(err))

    # -- digestion ---------------------------------------------------------

    def _pendientes(self):
        return self._diario.leer_desde(self._leer_marca())

    def sonar(self, forzado=False):
        """Hace una pasada de digestion. Devuelve el texto del sueno o None."""
        with self._cerrojo:
            entradas = self._pendientes()
            if not entradas:
                self._log("sueno: no hay paginas sin digerir")
                return None

            paginas = "\n\n".join("[%s]\n%s" % (iso, txt) for iso, txt in entradas)
            self._log("sueno: digiriendo %d entrada(s)%s"
                      % (len(entradas), " (forzado)" if forzado else ""))
            def una_pasada():
                texto, _ = self._modelo.responder([
                    {"role": "system", "content": self._sistema_tpl},
                    {"role": "user", "content":
                        "Páginas del diario desde el último sueño:\n\n" + paginas},
                ])
                return texto

            try:
                texto = insistir(una_pasada, errores=(ErrorModelo,),
                                 intentos_max=INTENTOS_SUENO,
                                 log=self._log, etiqueta="sueno:")
            except SinSuerte as err:
                # No se avanza la marca: estas mismas paginas se digeriran en el
                # proximo intento. Un sueno aplazado no se pierde; uno escrito
                # por otro modelo ensuciaria el corpus para siempre.
                self._log("sueno: sin suerte tras %d intento(s) en %d min; "
                          "las paginas siguen pendientes"
                          % (err.intentos, err.segundos // 60))
                return None

            marca = self._guardar(texto)
            self._escribir_marca(entradas[-1][0])
            self._log("sueno: guardado (%d caracteres)" % len(texto))
            if self._memoria:
                # La promocion se reintenta una vez: si se pierde, nadie la
                # recupera, y mientras tanto el sueno ya esta a salvo.
                for intento in (1, 2):
                    try:
                        self._memoria.procesar(texto, marca)
                        break
                    except Exception as err:              # noqa: BLE001
                        self._log("sueno: la promocion fallo (intento %d/2): %r"
                                  % (intento, err))
                        if intento == 1:
                            time.sleep(30)
            return texto

    # -- persistencia ------------------------------------------------------

    def _guardar(self, texto):
        """Acumulativo: los suenos se apilan, no se reemplazan.

        Devuelve la marca del sueno, que sirve de `origen` a los recuerdos que
        salgan de el: asi siempre se puede rastrear de que noche vino cada uno.
        """
        marca = datetime.now().isoformat(timespec="microseconds")
        with open(self._ruta, "a", encoding="utf-8") as f:
            f.write("\n### %s\n%s\n" % (marca, texto.strip()))
            f.flush()
        return marca

    def _leer_marca(self):
        if not self._marca.exists():
            return None
        return self._marca.read_text(encoding="utf-8").strip() or None

    def _escribir_marca(self, iso):
        self._marca.write_text(iso, encoding="utf-8")
