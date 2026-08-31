# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Transporte de Telegram: recibir mensajes y enviar respuestas.

Esta capa solo sabe hablar con Telegram. No sabe QUIEN contesta ni QUE
contesta — de eso se encarga el enrutador. La separacion es a proposito:
permite cambiar el cerebro sin tocar el transporte, y probar el transporte
sin tener cerebro.

Sin dependencias externas: solo biblioteca estandar.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot%s/%s"
DESCARGA = "https://api.telegram.org/file/bot%s/%s"

# Limite duro de la API de bots: no se puede descargar nada mas grande.
# No es configurable ni se puede rodear.
MAX_DESCARGA = 20 * 1024 * 1024

# Telegram mantiene la peticion abierta hasta ESPERA_LARGA segundos si no hay
# mensajes nuevos. Es long polling: evita machacar la API cada segundo.
ESPERA_LARGA = 25
MARGEN_RED = 10

# Reintentos para operaciones de un solo golpe (envios, descargas). El long
# polling de getUpdates ya se reintenta solo desde el bucle principal.
REINTENTOS = 3


class ErrorTelegram(Exception):
    """Fallo devuelto por la API de Telegram (no de red)."""


# Telegram no interpreta markdown si no se le pide con parse_mode, y pedirselo
# es peor: un asterisco suelto y rechaza el mensaje entero con un 400. Asi que
# se le quitan las marcas y se manda texto plano.
_NEGRITA = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_CURSIVA = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_BAJO = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")


# Las etiquetas de cita son una marca para el codigo, no para el dueño: le dicen
# al traductor de CJK que ahi no toque. Cumplida su funcion, se quedan aqui.
# el dueño lee el kanji, no el andamio.
_CITA = re.compile(r"</?cita>", re.I)


def _sin_markdown(texto):
    """Deja el contenido y se lleva las marcas. El orden importa: primero los
    dobles, que si no el de cursiva parte un **texto** por la mitad."""
    texto = _NEGRITA.sub(r"\1", texto)
    texto = _CURSIVA.sub(r"\1", texto)
    texto = _BAJO.sub(r"\1", texto)
    return _CITA.sub("", texto)


class Telegram:
    def __init__(self, token, log=print):
        self._token = token
        self._log = log
        # id del ultimo update procesado + 1. Confirma a Telegram que ya los
        # tenemos para que no vuelva a mandarlos.
        self._offset = None
        self._pendiente = None

    # -- llamadas crudas ---------------------------------------------------

    def _pedir(self, metodo, params=None, timeout=30):
        """Llama a la API insistiendo: reintenta fallos de red y los 429
        (respetando el retry_after que pide Telegram). El resto de errores
        HTTP no son accidentes: se lanzan tal cual."""
        datos = urllib.parse.urlencode(params or {}).encode()
        req = urllib.request.Request(API % (self._token, metodo), data=datos)
        espera = 2
        for intento in range(REINTENTOS + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    respuesta = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as err:
                # Telegram explica el motivo en el cuerpo incluso al devolver
                # 4xx (token invalido, bot bloqueado, chat inexistente...).
                # Sin esto, un token malo parecia un problema de red.
                try:
                    cuerpo = json.loads(err.read().decode("utf-8"))
                except Exception:                 # noqa: BLE001
                    cuerpo = {}
                detalle = cuerpo.get("description")
                if err.code == 429 and intento < REINTENTOS:
                    calma = (cuerpo.get("parameters") or {}).get("retry_after") or espera
                    self._log("telegram: 429 en %s, esperando %ss" % (metodo, calma))
                    time.sleep(calma)
                    espera = min(espera * 2, 30)
                    continue
                raise ErrorTelegram(detalle or ("HTTP %s" % err.code)) from err
            except (urllib.error.URLError, OSError, TimeoutError) as err:
                if intento == REINTENTOS:
                    raise
                self._log("telegram: fallo de red en %s (%s), reintento en %ss"
                          % (metodo, err, espera))
                time.sleep(espera)
                espera = min(espera * 2, 30)
        if not respuesta.get("ok"):
            raise ErrorTelegram(respuesta.get("description", "error desconocido"))
        return respuesta.get("result")

    # -- interfaz ----------------------------------------------------------

    def quien_soy(self):
        """Comprueba que el token es valido y devuelve los datos del bot."""
        return self._pedir("getMe", timeout=15)

    def confirmar(self):
        """Da por entregado lo ultimo recibido. Se llama cuando el turno se
        ha resuelto, no antes: asi un mensaje que no se pudo contestar vuelve
        a llegar en vez de perderse."""
        if self._pendiente is not None:
            self._offset = self._pendiente

    def recibir(self):
        """Espera mensajes nuevos y los devuelve normalizados.

        Devuelve lista de dicts: {chat_id, usuario_id, nombre, texto, tipo}.
        `tipo` es 'texto' o el nombre del adjunto (foto, video, voz...), que
        de momento no procesamos pero conviene distinguir para no responder
        como si fuera texto vacio.
        """
        params = {"timeout": ESPERA_LARGA, "allowed_updates": json.dumps(["message"])}
        if self._offset is not None:
            params["offset"] = self._offset
        crudos = self._pedir("getUpdates", params, timeout=ESPERA_LARGA + MARGEN_RED)

        mensajes = []
        for upd in crudos or []:
            # OJO: el offset NO se avanza aqui. Se avanza en confirmar(), una
            # vez el turno esta resuelto. Si el proceso muere insistiendo, o
            # si se rinde, Telegram vuelve a entregar el mensaje y no hay que
            # reescribirlo. Antes se avanzaba al recibir y el mensaje se
            # perdia (28/08).
            self._pendiente = upd["update_id"] + 1
            msg = upd.get("message")
            if not msg:
                continue
            emisor = msg.get("from") or {}
            tipo, medio = _medio_de(msg)
            mensajes.append({
                "chat_id": msg["chat"]["id"],
                "usuario_id": emisor.get("id"),
                "nombre": emisor.get("first_name") or emisor.get("username") or "?",
                "texto": msg.get("text") or msg.get("caption") or "",
                "tipo": tipo,
                "medio": medio,          # None si es texto
            })
        return mensajes

    def descargar(self, file_id):
        """Baja un adjunto y devuelve (bytes, mime). Lanza ErrorTelegram si
        excede el limite de la API o si Telegram lo rechaza."""
        info = self._pedir("getFile", {"file_id": file_id}, timeout=30)
        ruta = info.get("file_path")
        if not ruta:
            raise ErrorTelegram("Telegram no devolvio ruta para el archivo")
        tam = info.get("file_size") or 0
        if tam > MAX_DESCARGA:
            raise ErrorTelegram(
                "el archivo pesa %.1f MB y la API de bots solo permite bajar hasta %d MB"
                % (tam / 1048576, MAX_DESCARGA // 1048576))
        url = DESCARGA % (self._token, ruta)
        espera = 2
        for intento in range(REINTENTOS + 1):
            try:
                with urllib.request.urlopen(url, timeout=180) as r:
                    datos = r.read(MAX_DESCARGA + 1)
                    mime = r.headers.get("Content-Type")
                break
            except (urllib.error.URLError, OSError, TimeoutError) as err:
                if intento == REINTENTOS:
                    raise ErrorTelegram("no se pudo descargar el archivo: %s" % err) from err
                self._log("telegram: fallo descargando (%s), reintento en %ss" % (err, espera))
                time.sleep(espera)
                espera *= 3
        if len(datos) > MAX_DESCARGA:
            raise ErrorTelegram("el archivo supera los %d MB permitidos" % (MAX_DESCARGA // 1048576))
        return datos, mime

    def enviar(self, chat_id, texto):
        """Envia texto, troceando si excede el limite de Telegram.

        Devuelve la lista de message_id enviados, que es lo que hace falta
        para poder borrarlos luego. Los avisos de "esto va para largo" se
        borran solos en cuanto llega la respuesta de verdad.
        """
        texto = _sin_markdown(texto or "")
        ids = []
        for trozo in _trocear(texto, 4096):
            r = self._pedir("sendMessage", {"chat_id": chat_id, "text": trozo})
            mid = ((r or {}).get("result") or {}).get("message_id")
            if mid:
                ids.append(mid)
        return ids

    def borrar(self, chat_id, ids):
        """Borra mensajes ya enviados. Si no se puede, no pasa nada.

        Telegram solo deja borrar mensajes propios de menos de 48 horas, y un
        aviso que se queda sin borrar es feo pero inofensivo: por eso esto no
        levanta nunca una excepcion hacia arriba.
        """
        for mid in (ids or []):
            try:
                self._pedir("deleteMessage",
                            {"chat_id": chat_id, "message_id": mid}, timeout=10)
            except (urllib.error.URLError, ErrorTelegram, OSError):
                pass

    def escribiendo(self, chat_id):
        """Muestra el indicador 'escribiendo...'. Si falla, da igual."""
        try:
            self._pedir("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
        except (urllib.error.URLError, ErrorTelegram, OSError):
            pass


# -- ayudas ----------------------------------------------------------------

_ADJUNTOS = ("photo", "video", "voice", "audio", "document", "sticker",
             "animation", "video_note")

# Telegram no manda mime para las fotos: siempre son JPEG.
_MIME_POR_DEFECTO = {"photo": "image/jpeg", "sticker": "image/webp",
                     "video_note": "video/mp4", "animation": "video/mp4"}


def _medio_de(msg):
    """Devuelve (tipo, medio). `medio` es None para texto, o un dict con
    file_id, mime, tam y nombre para los adjuntos."""
    for clave in _ADJUNTOS:
        dato = msg.get(clave)
        # `is not None` y no truthiness: un adjunto podria llegar como dict
        # vacio y colarse como texto.
        if dato is None:
            continue
        # Las fotos llegan en varias resoluciones; la ultima es la mayor.
        if clave == "photo":
            dato = dato[-1] if isinstance(dato, list) and dato else {}
        return clave, {
            "file_id": dato.get("file_id"),
            "mime": dato.get("mime_type") or _MIME_POR_DEFECTO.get(clave),
            "tam": dato.get("file_size"),
            "nombre": dato.get("file_name"),
        }
    return "texto", None


def _trocear(texto, limite):
    """Parte por lineas para no cortar a mitad de palabra."""
    texto = texto or ""
    if len(texto) <= limite:
        return [texto]
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite:
            if actual:
                trozos.append(actual)
            # una sola linea gigante: no queda mas remedio que cortarla
            while len(linea) > limite:
                trozos.append(linea[:limite])
                linea = linea[limite:]
            actual = linea
        else:
            actual = (actual + "\n" + linea) if actual else linea
    if actual:
        trozos.append(actual)
    return trozos
