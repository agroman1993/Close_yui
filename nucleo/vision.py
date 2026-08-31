# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Nodo extractor visual: convierte imagen o video en un JSON estructurado.

Si el modelo de charla es texto puro, esta es su unica forma de ver. El JSON que sale de
aqui se inyecta como percepcion antes de que hable.

Decisiones tomadas con el banco de pruebas (pruebas/comparar.py), no a ojo:

- Modelo: meta/muse-spark-1.2. Reconoce entidades ("Bachira Meguru de Blue
  Lock", "Airbus A380", la marca del jamon), transcribe la voz literalmente y
  no se inventa contenido. Gemini 3.7 Flash es 6x mas barato y mas rapido pero
  alucino una cronologia entera sobre una imagen fija.
- La instruccion va repetida en el turno del usuario ademas del system: con
  video, solo con system se ignora el formato y responde en prosa inglesa.
- response_format json_object.
- `tipo` NO se le pregunta: lo sabemos nosotros. Los dos modelos etiquetaron
  como "video" un fotograma fijo. No preguntes al modelo lo que ya sabes.
"""

import base64
import json
import time
import urllib.error
import urllib.request

CLAVES = ("tipo", "descripcion_general", "elementos_clave", "linea_temporal_o_detalles")

# Reintentos de red: un corte puntual no deberia dejar a la asistenta ciega de una foto.
REINTENTOS = 2

SISTEMA = """Eres un sensor de visión por computador y un analizador de contexto visual. Tu única función es analizar la imagen o vídeo recibido y generar una descripción exhaustiva estructurada estrictamente en formato JSON.

REGLAS OBLIGATORIAS:
1. Responde ÚNICAMENTE con el objeto JSON crudo. No incluyas intros, salidas, explicaciones ni bloques de código markdown (sin ```json).
2. Si es un vídeo, analiza la línea temporal (marcas de tiempo), acciones clave, texto en pantalla, ambiente y detalles visuales relevantes.
3. No interpretes sentimientos ni intentes hablar como un humano. Sé quirúrgico y preciso.

Estructura requerida:
{
  "tipo": "video|imagen",
  "descripcion_general": "Resumen conciso en 1 frase",
  "elementos_clave": ["elemento1", "elemento2"],
  "linea_temporal_o_detalles": "Descripción cronológica o espacial detallada del contenido visual y auditivo"
}"""

RECORDATORIO = ("Analiza este medio y responde ÚNICAMENTE con el objeto JSON crudo "
                "con las claves exactas: tipo, descripcion_general, elementos_clave, "
                "linea_temporal_o_detalles. En español. Sin markdown, sin ```, sin texto fuera del JSON.")


class ErrorVision(Exception):
    """El nodo no pudo extraer nada. Se propaga: no se inventa una descripcion."""


class Vision:
    def __init__(self, cfg, log=print):
        v = cfg["vision"]
        self._url = v["base_url"].rstrip("/") + "/chat/completions"
        self._api_key = v["api_key"]
        self._id = v["id"]
        self._max_tokens = v.get("max_tokens", 4000)
        # Cuanto puede gastar PENSANDO, del total de arriba. El resto queda
        # reservado para lo que escribe.
        self._max_razonamiento = v.get("max_razonamiento", 1500)
        self._razonamiento_ok = self._max_razonamiento > 0
        self._timeout = v.get("timeout", 300)
        self._log = log

    def recordar_mirando(self, datos, mime, tipo, pregunta):
        """Vuelve a mirar algo que ya se vio, pero con la pregunta de HOY.

        `mira()` extrae un JSON completo para archivar. Esto es otra cosa: la
        foto se guardo hace meses y la descripcion de entonces se escribio sin
        saber que se le iba a preguntar. Aqui se le pone delante lo que se esta
        hablando ahora y se le pide que conteste a ESO.

        Devuelve texto llano, no JSON: va directo al prompt de quien no ve.
        """
        if tipo not in ("imagen", "video"):
            raise ErrorVision("tipo no soportado: %s" % tipo)
        b64 = base64.b64encode(datos).decode()
        clave = "video_url" if tipo == "video" else "image_url"
        por_defecto = "video/mp4" if tipo == "video" else "image/jpeg"
        medio = {"type": clave,
                 clave: {"url": "data:%s;base64,%s" % (mime or por_defecto, b64)}}

        instruccion = (
            "Esta imagen o video se guardo hace tiempo. Ahora mismo se esta "
            "hablando de esto:\n\n"
            "%s\n\n"
            "Mira el medio y cuenta, en dos o tres frases, lo que tenga que ver "
            "con eso. Solo lo que se vea de verdad: si el medio no tiene nada "
            "que ver con lo que se esta hablando, dilo con esas palabras y no "
            "estires. No adivines nada que no este a la vista."
            % (pregunta or "").strip())

        peticion = {
            "model": self._id,
            "messages": [{"role": "user",
                          "content": [medio, {"type": "text", "text": instruccion}]}],
            # Muse razona antes de escribir. Con 600 se gastaba el
            # presupuesto entero pensando y devolvia content vacio. Lo que hay
            # que topar es el RAZONAMIENTO, no la salida: asi lo que sobra
            # queda reservado para lo que de verdad dice.
            "max_tokens": self._max_tokens,
            "temperature": 0,
        }
        if self._razonamiento_ok:
            peticion["reasoning"] = {"max_tokens": self._max_razonamiento}
        req = urllib.request.Request(self._url, data=json.dumps(peticion).encode(), headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._api_key,
        })
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detalle = err.read().decode("utf-8", "replace")[:200]
            if (err.code == 400 and self._razonamiento_ok
                    and "reasoning" in detalle.lower()):
                self._razonamiento_ok = False
                self._log("vision: el proveedor rechazo 'reasoning'; "
                          "se desactiva y se reintenta")
                return self.recordar_mirando(datos, mime, tipo, pregunta)
            raise ErrorVision("HTTP %s al volver a mirar: %s"
                              % (err.code, detalle)) from err
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            raise ErrorVision("no se pudo volver a mirar: %s" % err) from err
        op = (d.get("choices") or [{}])[0]
        msg = op.get("message") or {}
        texto = (msg.get("content") or "").strip()
        if texto:
            return texto
        # Vacio: se dice POR QUE, que si no llega como un fallo mudo y se
        # busca en el sitio equivocado.
        uso = d.get("usage") or {}
        raz = (uso.get("completion_tokens_details") or {}).get("reasoning_tokens")
        raise ErrorVision(
            "volvio vacio al mirar de nuevo (fin=%s, razono %s tokens de %s)"
            % (op.get("finish_reason"), raz, uso.get("completion_tokens")))


    def mira(self, datos, mime, tipo, enfoque=None):
        """datos (bytes), mime, tipo ('imagen'|'video') -> dict con el esquema.

        `tipo` lo impone el llamante segun lo que descargo de Telegram.

        `enfoque` es opcional y va pegado al final del recordatorio, junto a la
        imagen, que es donde mas pesa. No recorta ni modifica el medio: le dice
        que parte importa y el reparte su atencion ahi. Medido el 26/08: con
        enfoque el JSON entero cambia de sujeto y saca detalle que sin el ni
        mencionaba.

        Ojo: tambien le da ganas de contestar. En esa misma prueba se invento
        un dominio que en la foto salia cortado. Por eso esto es opcional y no
        la forma normal de mirar.
        """
        if tipo not in ("imagen", "video"):
            raise ErrorVision("tipo no soportado: %s" % tipo)

        b64 = base64.b64encode(datos).decode()
        if tipo == "video":
            medio = {"type": "video_url",
                     "video_url": {"url": "data:%s;base64,%s" % (mime or "video/mp4", b64)}}
        else:
            medio = {"type": "image_url",
                     "image_url": {"url": "data:%s;base64,%s" % (mime or "image/jpeg", b64)}}

        recordatorio = RECORDATORIO
        if enfoque:
            recordatorio = RECORDATORIO + "\n\nENFOQUE: " + enfoque

        peticion = {
            "model": self._id,
            "messages": [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": [medio, {"type": "text", "text": recordatorio}]},
            ],
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
        }
        # Muse razona. Sin este tope puede gastarse el presupuesto entero
        # pensando y devolver el JSON vacio. Si el proveedor no admite el
        # parametro, se desactiva solo al primer 400.
        if self._razonamiento_ok:
            peticion["reasoning"] = {"max_tokens": self._max_razonamiento}

        req = urllib.request.Request(self._url, data=json.dumps(peticion).encode(), headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._api_key,
        })
        # La red se reintenta; los HTTP no: esos son rechazos, no accidentes.
        espera = 3
        for intento in range(REINTENTOS + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    d = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as err:
                detalle = err.read().decode("utf-8", "replace")[:300]
                if (err.code == 400 and self._razonamiento_ok
                        and "reasoning" in detalle.lower()):
                    self._razonamiento_ok = False
                    self._log("vision: el proveedor rechazo 'reasoning'; "
                              "se desactiva y se reintenta")
                    return self.mira(datos, mime, tipo, enfoque)
                raise ErrorVision("HTTP %s del extractor: %s"
                                  % (err.code, detalle)) from err
            except (urllib.error.URLError, OSError, TimeoutError) as err:
                if intento == REINTENTOS:
                    raise ErrorVision("no se pudo contactar con el extractor: %s" % err) from err
                self._log("vision: fallo de red (%s), reintento en %ss" % (err, espera))
                time.sleep(espera)
                espera *= 2

        msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
        texto = (msg.get("content") or "").strip()
        if not texto:
            if (msg.get("reasoning") or "").strip():
                raise ErrorVision("el extractor agoto los %s tokens razonando" % self._max_tokens)
            raise ErrorVision("el extractor devolvio una respuesta vacia")

        obj = _parsear(texto)
        # `tipo` se impone aqui: los modelos lo fallan y nosotros lo sabemos.
        obj["tipo"] = tipo
        uso = d.get("usage") or {}
        self._log("vision: %s ok (%s tok entrada / %s salida)"
                  % (tipo, uso.get("prompt_tokens", "?"), uso.get("completion_tokens", "?")))
        return obj


def _parsear(texto):
    """Convierte la respuesta en dict, tolerando el markdown que a veces cuela."""
    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = limpio.strip("`")
        if limpio.lower().startswith("json"):
            limpio = limpio[4:]
        limpio = limpio.strip()
    try:
        obj = json.loads(limpio)
    except json.JSONDecodeError as err:
        raise ErrorVision("el extractor no devolvio JSON valido: %s" % err) from err
    if not isinstance(obj, dict):
        raise ErrorVision("el extractor devolvio %s en vez de un objeto" % type(obj).__name__)
    faltan = [k for k in CLAVES if k not in obj]
    if faltan:
        raise ErrorVision("al JSON del extractor le faltan claves: %s" % faltan)
    return obj


def como_percepcion(obj):
    """Formatea el JSON para inyectarlo en el contexto de la asistenta.

    Va como mensaje de sistema y en primera persona ('estas viendo') a
    proposito: asi lo trata como algo que percibe, no como algo que le han
    contado. Es lo que decide si dice "veo un helicoptero" o "me dicen que
    hay un helicoptero".
    """
    elementos = obj.get("elementos_clave") or []
    if isinstance(elementos, list):
        elementos = "\n".join("- %s" % e for e in elementos)
    return (
        "[PERCEPCION] Esto es lo que estas viendo ahora mismo. No es algo que te "
        "hayan contado: lo tienes delante. Descríbelo con tus palabras si hace "
        "falta, pero no menciones este bloque ni digas que has recibido un JSON.\n\n"
        "Tipo: %s\n"
        "Resumen: %s\n"
        "Elementos:\n%s\n"
        "Detalle:\n%s" % (
            obj.get("tipo", "?"),
            obj.get("descripcion_general", ""),
            elementos or "- (ninguno)",
            obj.get("linea_temporal_o_detalles", ""),
        )
    )
