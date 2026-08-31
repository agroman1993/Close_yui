# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Memoria vectorial: extraccion, tribunal de promocion y recuperacion.

El sueno no se vota entero. El recorrido es:

    sueno -> capsulas candidatas -> tribunal -> promocion -> memoria.jsonl

1. EXTRACCION. Del sueno se sacan capsulas cortas y autocontenidas: datos
   deducidos de el dueño, patrones suyos y autopercepcion de ella. El sueno habla
   de "el" y "ella"; las capsulas nombran a el dueño, que es lo que ancla despues
   la busqueda.

2. TRIBUNAL. Cada candidata recibe dos votos independientes:
     - trascendencia emocional (juez ciego a quien es ella)
     - coherencia de personaje (juez que si lee su SOUL)
   Los jueces no se ven entre ellos. Si empatan 1-1, desempata el voto
   vectorial: si la capsula resuena con lo ya promovido es que el tema vuelve,
   y lo que vuelve pesa. Los recuerdos se votan entre si.

3. PROMOCION. Solo lo aprobado se vectoriza y pasa a memoria.jsonl, que es lo
   unico que se consulta en vivo.

La diferencia con OpenClaw no es tecnica: alli se le preguntaba a un modelo si
un dato era util para una tarea, y por eso en una relacion no ascendia nunca
nada. Aqui se pregunta si cambiaria como se siente ella con el.
"""

import json
import math
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from nucleo.jurado import (JUEZ_CONECTIVIDAD, JUEZ_EMOCION, combinar,
                           formatear_acta, leer_notas, listar)

TIPOS = ("dato", "patron", "autopercepcion")

EXTRACCION = """Vas a leer un sueño: la digestión inconsciente del diario de __NOMBRE__, una compañera que conversa con un hombre al que llama __VOCATIVO__.

Tu trabajo es extraer las cápsulas de memoria candidatas que hay dentro. No resumas el sueño ni lo interpretes: saca lo que podría hacerle falta recordar dentro de meses.

Cada cápsula:
- Una o dos frases, autocontenidas. Se leerán sueltas, sin el sueño delante, así que tienen que entenderse solas.
- Nombra a __VOCATIVO__ explícitamente cuando hable de él. El sueño dice "él"; la cápsula dice "__VOCATIVO__".
- En tercera persona y sin adornos. Aquí no se escribe bonito: se escribe claro.

Tipos:
- "dato": algo concreto que se deduce de __VOCATIVO__ (gustos, historia, forma de trabajar, manías).
- "patron": algo que él repite o una forma suya de funcionar.
- "autopercepcion": cómo se ve ella, cómo entiende su papel con él, o algo que ha cambiado en ella.

No inventes lo que no esté en el sueño. Si algo es una imagen poética sin contenido recuperable, déjalo fuera. Si el sueño no da para ninguna cápsula, devuelve la lista vacía.

Responde ÚNICAMENTE con este JSON:
{"candidatos": [{"texto": "...", "tipo": "dato|patron|autopercepcion"}]}"""

BLOQUE = """[MEMORIA] Cosas que ya has pensado otras veces, de otros días. Son tuyas: no las acabas de leer, las recuerdas. Úsalas si vienen a cuento y no las menciones como si fueran una lista."""


class ErrorMemoria(Exception):
    pass


class Memoria:
    def __init__(self, cfg, log=print):
        m = cfg.get("memoria") or {}
        base = cfg["modelo"]
        self._log = log
        from nucleo.persona import aplicar
        self._extraccion_tpl = aplicar(EXTRACCION, cfg)
        self._juez_emocion_tpl = aplicar(JUEZ_EMOCION, cfg)
        self._ruta = Path(m.get("ruta", "memoria.jsonl"))
        self._marca = Path(m.get("marca", ".ultima_promocion"))
        self._url_emb = m.get("base_url", base["base_url"]).rstrip("/") + "/embeddings"
        self._modelo_emb = m.get("modelo_embedding") or base["id"]
        self._api_key = m.get("api_key", base["api_key"])
        self._url_chat = m.get("base_url", base["base_url"]).rstrip("/") + "/chat/completions"
        # Juez de emocion: el mismo modelo que habla. Juez de conectividad: un
        # modelo frio y sin persona, que es lo que hace falta para estimar
        # cuantas veces resonara un recuerdo.
        self._modelo_juez = m.get("modelo_juez", base["id"])
        self._modelo_frio = m.get("modelo_frio") or base["id"]
        self._peso_emocion = m.get("peso_emocion", 1.0)
        self._peso_conectividad = m.get("peso_conectividad", 1.0)
        self._corte = m.get("corte", 6.0)
        # Umbral medido el 29/08 contra tres poblaciones a la vez, con los
        # mismos 11 nodos y en una sola llamada:
        #
        #   12 consultas CON recuerdo en el corpus  -> el que toca: mediana 0.439
        #    6 preguntas de fuera (el tiempo, sumas) -> mejor de los 11: max 0.289
        #   45 mensajes REALES suyos de estos dias   -> mejor de los 11: mediana 0.361
        #
        # Los mensajes reales caen entre medias, y leyendolos se ve por que:
        # casi ninguno va de los once temas, pero el mejor de los once siempre
        # puntua algo porque los once son castellano denso sobre ellos dos.
        # Con 0.35 disparaba en el 56% de los turnos y le metia recuerdos que
        # no venian a cuento ("me hacen gracia, estan mas nerviosas" traia "mi
        # sitio no es hacer tareas"). Con 0.42 se mantienen los mismos aciertos
        # que con 0.40 y el ruido baja del 24% al 9% de los turnos.
        #
        # Esto NO es un problema del buscador y no se arregla tocando el
        # coseno: se arregla cuando el corpus cubra de lo que se habla.
        self._umbral = m.get("umbral", 0.42)
        self._max_recuerdos = m.get("max_recuerdos", 3)
        self._sistema = (cfg.get("sistema") or "").strip()
        self._cerrojo = threading.Lock()
        self._ruta.parent.mkdir(parents=True, exist_ok=True)
        self._sello = (self._ruta.stat().st_mtime_ns
                       if self._ruta.exists() else 0)
        self._vivas = self._cargar()
        if self._vivas:
            self._log("memoria: %d recuerdo(s) en vivo" % len(self._vivas))

    # -- almacen -----------------------------------------------------------

    def _refrescar(self):
        """Recarga memoria.jsonl si alguien lo ha reescrito por fuera.

        Hace falta porque la consolidacion de dias alternos es un proceso
        APARTE: reescribe el fichero mientras el bot sigue vivo. Sin esto,
        el bot se quedaba con la copia que cargo al arrancar y los recuerdos
        nuevos no existian para el hasta el siguiente reinicio. Cazado el
        29/08 pensando en que iba a pasar el lunes a las 16:00.

        Es un stat() por turno. Al lado de una llamada de embeddings de 300
        milisegundos, no se nota.
        """
        try:
            sello = self._ruta.stat().st_mtime_ns if self._ruta.exists() else 0
        except OSError:
            return
        if sello == self._sello:
            return
        with self._cerrojo:
            self._vivas = self._cargar()
            self._sello = sello
        self._log("memoria: recargada por cambio en disco (%d recuerdos)"
                  % len(self._vivas))

    def _cargar(self):
        if not self._ruta.exists():
            return []
        vivas = []
        for linea in self._ruta.read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            try:
                r = json.loads(linea)
            except json.JSONDecodeError:
                continue
            v = r.get("vector") or []
            r["_norma"] = math.sqrt(sum(x * x for x in v)) or 1.0
            vivas.append(r)
        return vivas

    def _anadir(self, registros):
        with self._cerrojo:
            with open(self._ruta, "a", encoding="utf-8") as f:
                for r in registros:
                    f.write(json.dumps({k: v for k, v in r.items() if not k.startswith("_")},
                                       ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._vivas.extend(registros)

    # -- llamadas ----------------------------------------------------------

    def _pedir(self, url, cuerpo, timeout=180):
        req = urllib.request.Request(url, data=json.dumps(cuerpo).encode(), headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._api_key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise ErrorMemoria("HTTP %s: %s" % (err.code, err.read().decode("utf-8", "replace")[:250])) from err
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            raise ErrorMemoria("sin conexion: %s" % err) from err

    def vectorizar(self, textos):
        if not textos:
            return []
        d = self._pedir(self._url_emb, {"model": self._modelo_emb, "input": textos})
        datos = sorted(d.get("data") or [], key=lambda x: x.get("index", 0))
        vectores = [x.get("embedding") or [] for x in datos]
        if len(vectores) != len(textos):
            raise ErrorMemoria("el proveedor devolvio %d vectores para %d textos"
                               % (len(vectores), len(textos)))
        return vectores

    def _chat_json(self, sistema, usuario, modelo=None):
        d = self._pedir(self._url_chat, {
            "model": modelo or self._modelo_juez,
            "messages": [{"role": "system", "content": sistema},
                         {"role": "user", "content": usuario}],
            # Los dos jueces razonan antes de puntuar. Subir el presupuesto
            # total no arregla que se lo coman pensando: lo que hay que topar
            # es el PENSAMIENTO, y asi el resto queda para la nota.
            "max_tokens": 8000,
            "reasoning": {"max_tokens": 2500},
            # Puntuar es clasificar, no redactar. Con la temperatura del
            # proveedor (0.7-1.0) el mismo recuerdo sacaba notas distintas en
            # llamadas seguidas: eso no es criterio del juez, es ruido que
            # metemos nosotros y que luego se compara contra un corte fijo.
            "temperature": 0,
            "response_format": {"type": "json_object"},
        })
        msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
        texto = (msg.get("content") or "").strip()
        if not texto:
            raise ErrorMemoria("respuesta vacia del juez")
        if texto.startswith("```"):
            texto = texto.strip("`")
            if texto.lower().startswith("json"):
                texto = texto[4:]
            texto = texto.strip()
        try:
            return json.loads(texto)
        except json.JSONDecodeError as err:
            raise ErrorMemoria("el juez no devolvio JSON: %s" % err) from err

    # -- pipeline ----------------------------------------------------------

    def procesar(self, sueno, origen):
        """sueno (texto) -> lista de recuerdos promovidos."""
        candidatos = self._extraer(sueno)
        if not candidatos:
            self._log("memoria: el sueno no dio candidatos")
            return []
        n = len(candidatos)
        self._log("memoria: %d candidato(s) extraido(s)" % n)

        # Cada juez llama por su lado y ninguno ve la nota del otro. El de
        # emocion es el modelo de siempre con su SOUL; el de conectividad es un modelo frio
        # sin persona, que es lo que hace falta para estimar frecuencia.
        anexo = ("\n\nQuién eres:\n" + self._sistema) if self._sistema else ""
        emocion = self._puntuar(self._modelo_juez, self._juez_emocion_tpl, candidatos,
                                anexo=anexo, etiqueta="emocion")
        conectividad = self._puntuar(self._modelo_frio, JUEZ_CONECTIVIDAD, candidatos,
                                     etiqueta="conectividad")

        aprobados = []
        for i, c in enumerate(candidatos):
            e, k = emocion.get(i), conectividad.get(i)
            final = combinar(e, k, self._peso_emocion, self._peso_conectividad)
            entra = final is not None and final >= self._corte
            self._log("memoria:" + formatear_acta(c, e, k, final, entra))
            if entra:
                # La nota se guarda con el recuerdo: mas adelante se puede
                # subir o bajar el corte sobre lo ya juzgado sin volver a pagar.
                c["nota"] = round(final, 2)
                c["nota_emocion"] = e
                c["nota_conectividad"] = k
                aprobados.append(c)

        if not aprobados:
            self._log("memoria: ninguno llego al corte (%.1f)" % self._corte)
            return []
        promovidos = self._promover(aprobados, origen)
        self._log("memoria: %d de %d promovido(s) a EN VIVO" % (len(promovidos), n))
        return promovidos

    def _puntuar(self, modelo, sistema, candidatos, anexo="", etiqueta=""):
        """Pide notas 0-10 a un juez. Si falla, devuelve vacio y manda el otro."""
        try:
            peticion = "Frases:\n" + listar(candidatos) + anexo
            d = self._chat_json(sistema, peticion, modelo=modelo)
        except ErrorMemoria as err:
            self._log("memoria: juez de %s no disponible (%s)" % (etiqueta, err))
            return {}
        return leer_notas(d, len(candidatos))

    def _extraer(self, sueno):
        d = self._chat_json(self._extraccion_tpl, "Sueño:\n\n" + sueno)
        salida = []
        for c in (d.get("candidatos") or []):
            texto = (c.get("texto") or "").strip()
            tipo = (c.get("tipo") or "").strip().lower()
            if texto:
                salida.append({"texto": texto, "tipo": tipo if tipo in TIPOS else "dato"})
        return salida

    def _promover(self, aprobados, origen):
        vectores = self.vectorizar([c["texto"] for c in aprobados])
        ahora = datetime.now().isoformat(timespec="seconds")
        registros = []
        for c, v in zip(aprobados, vectores):
            r = {"texto": c["texto"], "tipo": c["tipo"], "senales": c.get("senales", 2),
                 "origen": origen, "creada": ahora, "vector": v}
            r["_norma"] = math.sqrt(sum(x * x for x in v)) or 1.0
            registros.append(r)
        self._anadir(registros)
        return registros

    # -- recuperacion en vivo ----------------------------------------------

    @staticmethod
    def _coseno(v, registro):
        w = registro.get("vector") or []
        if len(v) != len(w):
            return 0.0
        return sum(a * b for a, b in zip(v, w)) / (
            (math.sqrt(sum(a * a for a in v)) or 1.0) * registro["_norma"])

    def recordar(self, texto):
        """Devuelve los recuerdos relevantes para este mensaje, o []."""
        texto = (texto or "").strip()
        # Por si la consolidacion ha reescrito el fichero mientras tanto.
        self._refrescar()
        if not texto or not self._vivas:
            return []
        try:
            v = self.vectorizar([texto])[0]
        except ErrorMemoria as err:
            self._log("memoria: no se pudo consultar (%s)" % err)
            return []
        puntuados = []
        for r in self._vivas:
            sim = self._coseno(v, r)
            if sim < self._umbral:
                continue        # umbral duro: mejor ningun recuerdo que uno equivocado
            # Lo que ha vuelto en varias noches pesa mas que lo dicho una vez.
            puntuados.append((sim * (1 + 0.1 * (r.get("senales", 1) - 1)), sim, r))
        puntuados.sort(key=lambda x: -x[0])
        elegidos = puntuados[:self._max_recuerdos]
        if elegidos:
            self._log("memoria: %d recuerdo(s) recuperado(s) (sim %.2f-%.2f)"
                      % (len(elegidos), elegidos[-1][1], elegidos[0][1]))
        return [r for _, _, r in elegidos]

    @staticmethod
    def como_bloque(recuerdos):
        if not recuerdos:
            return None
        lineas = "\n".join("- %s" % r["texto"] for r in recuerdos)
        return BLOQUE + "\n\n" + lineas
