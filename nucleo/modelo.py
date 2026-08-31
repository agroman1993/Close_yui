# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Cliente minimo para cualquier endpoint compatible con OpenAI.

Aqui NO hay reintentos silenciosos ni suplentes. Si algo falla, se lanza el
error hacia arriba para que se vea. Una capa que disimula fallos solo consigue
que no sepas cuando se rompio.

Sin dependencias externas: solo biblioteca estandar.
"""

import json
import urllib.error
import urllib.request


class ErrorModelo(Exception):
    """Fallo al hablar con el proveedor.

    `gratis` = el proveedor no llego a generar nada, asi que no lo cobro:
    5xx, fallo de red, JSON roto. Insistir ahi no cuesta.
    `gratis=False` = si genero y si lo cobro (respuesta vacia, presupuesto
    agotado razonando). Insistir ahi es dinero.
    """

    def __init__(self, mensaje, gratis=False):
        super().__init__(mensaje)
        self.gratis = gratis

    """Fallo al llamar al modelo, con el motivo ya legible."""


class Modelo:
    def __init__(self, base_url, api_key, modelo_id, max_tokens=2000,
                 temperatura=0.9, timeout=180, razonamiento=None, log=print):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._id = modelo_id
        self._max_tokens = max_tokens
        self._temperatura = temperatura
        self._timeout = timeout
        # Tope para los tokens de razonamiento (parametro "reasoning" de
        # algunos proveedores): el modelo puede gastarse el presupuesto entero de
        # salida pensando y no escribir nada. Con el tope, el resto del
        # presupuesto queda reservado para la respuesta visible. Si el
        # proveedor rechaza el parametro, se desactiva solo al primer 400.
        self._razonamiento = razonamiento
        # Ultimo consumo, para saber cuanto penso en un turno que SALIO
        # bien. Antes solo se sabia de los que fallaban.
        self.ultimo_uso = {}
        self._log = log

    def responder(self, mensajes, herramientas=None):
        """mensajes (formato OpenAI) -> (texto, llamadas).

        `llamadas` es la lista de tool_calls que pide el modelo, o [] si no
        pide ninguna. Lanza ErrorModelo si falla.
        """
        peticion = {
            "model": self._id,
            "messages": mensajes,
            "max_tokens": self._max_tokens,
            "temperature": self._temperatura,
        }
        if herramientas:
            peticion["tools"] = herramientas
            peticion["tool_choice"] = "auto"
        if self._razonamiento:
            # Nota (21/08): la respuesta puede traer reasoning_details; aqui
            # no se leen ni se guardan. El hilo solo conserva content y
            # tool_calls, que el enrutador reconstruye a mano, asi que la
            # cadena de pensamiento no puede colarse en el historial.
            peticion["reasoning"] = self._razonamiento
        cuerpo = json.dumps(peticion).encode("utf-8")

        req = urllib.request.Request(self._url, data=cuerpo, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._api_key,
        })

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                datos = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detalle = err.read().decode("utf-8", "replace")[:400]
            # Autosaneado (21/08): si el proveedor rechaza el parametro
            # "reasoning", se desactiva y se repite la misma llamada sin el.
            # Solo puede pasar una vez: desactivado, la peticion ya no lo
            # lleva nunca mas en este proceso.
            if (peticion.get("reasoning") is not None and err.code == 400
                    and "reasoning" in detalle.lower()):
                self._razonamiento = None
                self._log("modelo: el proveedor rechazo 'reasoning'; "
                          "se desactiva y se reintenta sin el (%s)" % detalle)
                return self.responder(mensajes, herramientas)
            raise ErrorModelo("HTTP %s del proveedor: %s" % (err.code, detalle),
                              gratis=err.code >= 500 or err.code == 429) from err
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            raise ErrorModelo("no se pudo contactar con el proveedor: %s" % err,
                              gratis=True) from err
        except json.JSONDecodeError as err:
            raise ErrorModelo("el proveedor devolvio algo que no es JSON",
                              gratis=True) from err

        opciones = datos.get("choices") or []
        if not opciones:
            raise ErrorModelo("el proveedor no devolvio ninguna respuesta: %s"
                              % json.dumps(datos)[:300])

        msg = opciones[0].get("message") or {}
        texto = (msg.get("content") or "").strip()
        llamadas = msg.get("tool_calls") or []
        if texto or llamadas:
            u = datos.get("usage") or {}
            self.ultimo_uso = {
                "entrada": u.get("prompt_tokens"),
                "salida": u.get("completion_tokens"),
                "razonando": (u.get("completion_tokens_details") or {})
                              .get("reasoning_tokens"),
            }
            return texto, llamadas

        # Vacia. Hay dos motivos distintos y confundirlos sale caro:
        #
        #   "length"  se quedo sin presupuesto de verdad -> subir el techo o
        #             topar el razonamiento tiene sentido.
        #   "stop"    dijo que habia terminado y no escribio nada. Medido el
        #             24/08 sobre el camino real: pasa en 2 de cada 10 y la
        #             siguiente llamada, 25 s despues, va bien. Es del
        #             proveedor y se arregla volviendo a llamar.
        #
        # Antes esto se reportaba siempre como "agoto los 8000 razonando", con
        # un uso real de 182 tokens. La cifra desmentia el mensaje y aun asi el
        # reintento se guiaba por el mensaje.
        uso = datos.get("usage") or {}
        gastado = uso.get("completion_tokens")
        razonado = (uso.get("completion_tokens_details") or {}).get("reasoning_tokens")
        motivo = opciones[0].get("finish_reason")

        if motivo == "length":
            raise ErrorModelo(
                "AGOTADO: se quedo sin presupuesto (%s de %s tokens, %s razonando). "
                "Aqui si toca topar el razonamiento."
                % (gastado, self._max_tokens, razonado))

        raise ErrorModelo(
            "VACIA: dijo que terminaba sin escribir nada (fin=%s, %s tokens, "
            "%s razonando). Del proveedor, no del prompt: se reintenta."
            % (motivo, gastado, razonado))

    # Techo para las subidas automaticas. Subirlo no cuesta nada por si mismo:
    # solo se paga lo que el modelo llega a generar.
    #
    # Nota de campo: estaba en 8000 y el max_tokens de config subio a 12000 ese mismo
    # dia, con lo que subir_max_tokens() no podia subir NADA —devolvia None a
    # la primera—. Se sube a 20000, por debajo de los 32.768 que admite el
    # endpoint que se usaba originalmente.
    MAX_TOKENS_TOPE = 20000

    def subir_max_tokens(self, delta):
        """Sube el techo de salida. Devuelve el techo nuevo, o None si ya
        estaba en el tope. Para usar tras agotar el presupuesto razonando."""
        nuevo = min(self._max_tokens + delta, self.MAX_TOKENS_TOPE)
        if nuevo == self._max_tokens:
            return None
        self._max_tokens = nuevo
        return nuevo
