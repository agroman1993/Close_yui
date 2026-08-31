# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Persiste el hilo reciente de conversacion entre reinicios.

Esto NO es el sistema de memoria: es exactamente la misma ventana de turnos que
ya vivia en RAM, pero que sobrevive a un reinicio. No resume, no consolida y no
crece: cuando la ventana se recorta, lo que sale se pierde igual que antes.

Existe por una razon practica: mientras se construye el proyecto se reinicia
cada poco, y sin esto cada reinicio deja una conversacion rota que parece un
fallo de ella. La memoria de verdad llega en el Estado 3.
"""

import json
import os
import threading
from pathlib import Path


class Hilos:
    def __init__(self, ruta, log=print):
        self._ruta = Path(ruta)
        self._log = log
        self._cerrojo = threading.Lock()
        self._ruta.parent.mkdir(parents=True, exist_ok=True)

    def cargar(self):
        """Devuelve {chat_id: [mensajes]}. Si algo falla, se empieza limpio."""
        if not self._ruta.exists():
            return {}
        try:
            crudo = json.loads(self._ruta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            self._log("hilos: no se pudo leer (%s); se empieza en blanco" % err)
            return {}
        # Las claves de JSON son texto; los chat_id de Telegram son enteros.
        hilos = {}
        for clave, mensajes in (crudo or {}).items():
            try:
                hilos[int(clave)] = mensajes
            except (TypeError, ValueError):
                continue
        if hilos:
            self._log("hilos: recuperados %d hilo(s), %d mensaje(s)"
                      % (len(hilos), sum(len(v) for v in hilos.values())))
        return hilos

    def guardar(self, hilos):
        """Escribe de forma atomica: primero a un temporal, luego se sustituye.

        Asi un corte a media escritura no deja el fichero a medias, que seria
        peor que no tenerlo.
        """
        with self._cerrojo:
            tmp = self._ruta.with_suffix(self._ruta.suffix + ".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({str(k): v for k, v in hilos.items()}, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self._ruta)
            except OSError as err:
                self._log("hilos: no se pudo guardar:", err)
