# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""El segundo indice: lo que la asistenta ha VISTO, vectorizado como imagen.

Esto no amplia la memoria de texto: es otra memoria, aparte, y por un motivo
medido el 23/08 y no por gusto.

Con `google/gemini-embedding-2`, que si es multimodal de verdad:

    la misma foto, fichero distinto ..... 0.982
    fotos distintas ..................... 0.751     margen 0.23, comodo
    texto -> su propia imagen ........... 0.480
    texto -> otra imagen ................ 0.394     margen 0.09, estrecho

Los dos margenes son correctos, pero **viven en escalas distintas**. Un coseno
texto-texto y uno texto-imagen no se pueden ordenar en la misma lista: los de
texto ganarian siempre por escala, no por venir mas al caso. De ahi que sean
dos indices con dos umbrales, y no un cajon con todo dentro.

El indice de texto (`memoria.py`, el modelo de embeddings de la config) sigue
exactamente igual. Aqui son otras dimensiones y otro modelo: no se mezclan ni
se convierten.

Y una cosa que decide todo lo demas: **aqui no se devuelve texto**. Si este
indice devolviera una descripcion, sobraria — de texto ya se encarga el
indice de memoria. Lo que devuelve es el MEDIO, para que el modelo de vision
vuelva a mirarlo con la pregunta de hoy delante. La descripcion que se escribio
el dia que llego la foto se escribio sin saber que se le iba a preguntar
despues.

Por eso se guarda el fichero. Ocupa, y es una copia mas en el disco: eso se
decidio a sabiendas el 23/08.

Sin dependencias fuera de PIL, que ya estaba.
"""

import base64
import io
import json
import math
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

MODELO = "google/gemini-embedding-2"
# Medido el 23/08 con 12 frases como las escribe el de verdad (con erratas,
# sin nombrar las cosas por su nombre) contra dos documentos parecidos:
#     0.34 -> entran 7/7, cuelan 6/17
#     0.38 -> entran 6/7, cuelan 3/17
#     0.40 -> entran 5/7, cuelan 0/17   <-- aqui
#     0.42 -> entran 3/7, cuelan 0/17
# La charla normal ("tengo hambre", "ponme musica") vive en 0.25-0.32, lejos.
# El solape viene solo del OTRO documento: dos tablas de papeleo se parecen
# entre si, y ese es el peor caso posible para esto.
UMBRAL = 0.40
# Uno como maximo, y no por el prompt: cada acierto obliga a una llamada a
# Muse ANTES de poder contestar, y eso son 10-15 segundos de espera real.
MAX_RECUERDOS = 1
LADO_MAXIMO = 1024     # el contexto son 8K tokens; una foto de movil no cabe entera
CALIDAD = 82


class ErrorAlbum(Exception):
    """Fallo al indexar o consultar. Se dice; no se disimula."""


class Album:
    def __init__(self, cfg, log=print):
        base = cfg["modelo"]
        a = cfg.get("album") or {}
        self._url = a.get("base_url", base["base_url"]).rstrip("/") + "/embeddings"
        self._api = a.get("api_key", base["api_key"])
        self._modelo = a.get("modelo", MODELO)
        self._umbral = a.get("umbral", UMBRAL)
        self._max = a.get("max_recuerdos", MAX_RECUERDOS)
        self._ruta = Path(a.get("ruta", "album.jsonl"))
        if not self._ruta.is_absolute():
            self._ruta = Path(__file__).resolve().parent.parent / self._ruta
        self._medios = self._ruta.parent / a.get("medios", "album")
        self._medios.mkdir(parents=True, exist_ok=True)
        self._log = log
        self._lock = threading.Lock()
        self._vistos = self._cargar()
        self._log("album: %d recuerdo(s) visuales" % len(self._vistos))

    # -- almacen ----------------------------------------------------------

    def _cargar(self):
        if not self._ruta.exists():
            return []
        fuera = []
        for linea in self._ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                fuera.append(json.loads(linea))
            except json.JSONDecodeError:
                self._log("album: una linea ilegible, se salta")
        return fuera

    def _anadir(self, entrada):
        with self._lock:
            with self._ruta.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
            self._vistos.append(entrada)

    # -- vectores ---------------------------------------------------------

    def _pedir(self, contenido):
        cuerpo = {"model": self._modelo, "input": [{"content": contenido}]}
        req = urllib.request.Request(self._url, data=json.dumps(cuerpo).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + self._api})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise ErrorAlbum("HTTP %s: %s" % (err.code, err.read().decode("utf-8", "replace")[:200])) from err
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            raise ErrorAlbum("no se pudo contactar: %s" % err) from err
        v = (d.get("data") or [{}])[0].get("embedding") or []
        if not v:
            raise ErrorAlbum("el proveedor no devolvio vector")
        return v

    @staticmethod
    def _encoger(datos, mime):
        """Una foto de movil no cabe en 8K tokens. Se reduce antes de mandarla.

        Reducir no le quita significado a la escena: lo que importa aqui es de
        que va la imagen, no si se lee la letra pequena. Para leer letra ya
        esta Muse.
        """
        try:
            from PIL import Image
        except ImportError:
            return datos, mime
        try:
            im = Image.open(io.BytesIO(datos))
        except Exception:
            return datos, mime
        if max(im.size) <= LADO_MAXIMO:
            return datos, mime
        f = LADO_MAXIMO / max(im.size)
        im = im.convert("RGB").resize((int(im.width * f), int(im.height * f)))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=CALIDAD)
        return buf.getvalue(), "image/jpeg"

    def _de_medio(self, datos, mime):
        if (mime or "").startswith("image/"):
            datos, mime = self._encoger(datos, mime)
            clave = "image_url"
            trozo = {"type": "image_url",
                     "image_url": {"url": "data:%s;base64,%s"
                                   % (mime, base64.b64encode(datos).decode())}}
        elif (mime or "").startswith("video/"):
            trozo = {"type": "input_video",
                     "input_video": {"data": base64.b64encode(datos).decode(),
                                     "format": (mime or "video/mp4").split("/")[-1]}}
        elif (mime or "").startswith("audio/"):
            trozo = {"type": "input_audio",
                     "input_audio": {"data": base64.b64encode(datos).decode(),
                                     "format": (mime or "audio/ogg").split("/")[-1]}}
        else:
            raise ErrorAlbum("no se como vectorizar un %s" % mime)
        return self._pedir([trozo])

    # -- lo que usa el resto ----------------------------------------------

    def guardar(self, datos, mime, sobre, tipo="imagen", chat_id=None):
        """Indexa un medio recien llegado y CONSERVA el fichero.

        `sobre` es lo que dijo el nodo visual ese dia. Se guarda como etiqueta
        —sirve para el registro y como recambio si un dia Muse no contesta—
        pero NO es lo que se entrega: lo que se entrega es el medio.
        """
        v = self._de_medio(datos, mime)
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
               "video/mp4": ".mp4", "video/quicktime": ".mov"}.get(
                   mime, ".jpg" if (mime or "").startswith("image/") else ".bin")
        nombre = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ext
        (self._medios / nombre).write_bytes(datos)
        self._anadir({
            "fichero": nombre,
            "tipo": tipo,
            "sobre": (sobre or "").strip(),
            "mime": mime,
            "bytes": len(datos),
            "creada": datetime.now().isoformat(timespec="seconds"),
            "chat_id": chat_id,
            "vector": v,
        })
        self._log("album: guardado %s (%d KB) -> %d visuales"
                  % (nombre, len(datos) // 1024, len(self._vistos)))

    def leer(self, entrada):
        """Los bytes de un recuerdo visual, para volver a mirarlo."""
        ruta = self._medios / (entrada.get("fichero") or "")
        if not ruta.exists():
            raise ErrorAlbum("el medio ya no esta en el disco: %s" % entrada.get("fichero"))
        return ruta.read_bytes()

    def olvidar(self, entrada):
        """Borra un recuerdo visual: la linea y el fichero."""
        ruta = self._medios / (entrada.get("fichero") or "")
        with self._lock:
            self._vistos = [x for x in self._vistos
                            if x.get("fichero") != entrada.get("fichero")]
            self._ruta.write_text(
                "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in self._vistos),
                encoding="utf-8")
        if ruta.exists():
            ruta.unlink()
        self._log("album: olvidado %s" % entrada.get("fichero"))

    def recordar(self, texto):
        """Busca en lo visto, con una consulta en TEXTO. Espacio cruzado."""
        texto = (texto or "").strip()
        if not texto or not self._vistos:
            return []
        try:
            v = self._pedir([{"type": "text", "text": texto}])
        except ErrorAlbum as err:
            self._log("album: no se pudo consultar (%s)" % err)
            return []
        puntuados = []
        for r in self._vistos:
            s = self._coseno(v, r.get("vector") or [])
            if s >= self._umbral:
                puntuados.append((s, r))
        puntuados.sort(key=lambda x: -x[0])
        elegidos = puntuados[:self._max]
        if elegidos:
            self._log("album: %d visual(es) (sim %.2f-%.2f)"
                      % (len(elegidos), elegidos[-1][0], elegidos[0][0]))
        return [r for _, r in elegidos]

    @staticmethod
    def _coseno(u, v):
        if not u or not v or len(u) != len(v):
            return -1.0
        p = na = nb = 0.0
        for a, b in zip(u, v):
            p += a * b
            na += a * a
            nb += b * b
        if na <= 0 or nb <= 0:
            return -1.0
        return p / math.sqrt(na * nb)

    @staticmethod
    def como_bloque(lecturas):
        """`lecturas` son las miradas FRESCAS de Muse, no las descripciones
        viejas. Bloque propio, separado del de texto: son otra cosa."""
        lecturas = [x for x in lecturas if (x or "").strip()]
        if not lecturas:
            return None
        return ("[COSAS QUE HAS VISTO] Te vienen a la cabeza por parecido con lo "
                "que se esta hablando, no porque el las haya mencionado. Esto es "
                "lo que hay en ellas:\n\n"
                + "\n".join("- %s" % t.strip() for t in lecturas))
