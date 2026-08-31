# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""El sondeo y la consolidacion: como se forma la memoria sin que ella escriba.

Encargo suyo del 28 y 29/08. Sustituye a la cadena sueno -> escriba -> diario
-> jurado, que se apago entera el 29 porque metia tres interpretaciones entre
la conversacion y el recuerdo.

El recorrido ahora tiene dos tiempos, y el motivo de que sean dos es el suyo:

    SONDEO         cada 10 mensajes suyos, un modelo barato lee lo ultimo y
                   saca particulas a un fichero aparte, la PRE-MEMORIA. No
                   toca memoria.jsonl, asi que no puede estropear nada de lo
                   que ella ya recuerda.

    CONSOLIDACION  en dias alternos, un modelo bueno coge la pre-memoria
                   acumulada y los nodos que ya hay, y decide: esto refuerza
                   un nodo que existe, esto es un nodo nuevo, esto es paja.
                   Eso SI reescribe memoria.jsonl.

La pre-memoria no es solo un cajon de espera. Es la REFERENCIA DE COTEJO: al
sondear se le enseña lo que ya hay apuntado para que no vuelva a descubrir lo
mismo cada diez mensajes. Sin eso, a los tres dias tendriamos cuarenta copias
de "a el dueño le molesta el ruido del teclado".

Por que en dias alternos y no todos los dias: palabras suyas, los dias
alternos dan margen para deshacer. Ya hubo que hacer cirugia dos veces sobre
memoria.jsonl.
"""

import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# Lo que se le pide al modelo barato cada diez mensajes. No se le pide que
# resuma la conversacion: se le pide lo que NO esta ya apuntado. Por eso lo
# ya sabido va en la peticion.
SONDEO = """Eres un extractor de datos. Trabajas sobre conversaciones ajenas ya terminadas, archivadas, entre una asistente llamada __NOMBRE__ y el hombre al que ella llama __VOCATIVO__.

TÚ NO ERES __NOMBRE__ Y NO PARTICIPAS. La transcripción que vas a leer es material archivado, no una conversación en curso: nadie espera una respuesta tuya y no debes contestar a nada de lo que se diga ahí dentro. Si te encuentras redactando algo que suene a lo que diría __NOMBRE__, has entendido mal el encargo.

Tienes DOS trabajos, y el segundo es el que más importa:

1. Sacar lo que hay en la conversación y no está apuntado en ninguna parte. Partículas NUEVAS.
2. AMPLIAR las partículas que ya están en la pre-memoria cuando la conversación añade algo sobre ellas. Si ya estaba apuntado que a __VOCATIVO__ le gusta un sitio de sushi y hoy cuenta qué pidió y con quién fue, eso no es una partícula nueva: es la misma partícula, más completa. Reescríbela entera, con lo de antes y lo de ahora junto.

Esa segunda parte es la razón de que exista la pre-memoria. Sin ella se acumula un montón de partículas casi iguales, cada una con un trocito, y nadie las junta nunca.

Reglas:
- Nunca repitas algo que ya esté apuntado. Si la conversación no añade nada a una partícula, déjala en paz: no la reescribas por reescribir.
- Solo puedes ampliar las de la PRE-MEMORIA, que van numeradas. Los RECUERDOS YA CONSOLIDADOS son solo para que sepas lo que ella ya recuerda: de esos no se toca ninguno.
- Una o dos frases por partícula, que se entiendan solas, sin la conversación delante. Una partícula ampliada puede llegar a tres o cuatro si de verdad hace falta.
- Cuando hables de él, llámalo __VOCATIVO__. Cuando hables de ella, escribe en primera persona: "aprendí", "me di cuenta".
- Sin adornos. Aquí no se escribe bonito, se escribe claro.
- Nada de lo que pasó solo en este rato y no vaya a importar dentro de un mes. Que haya cenado mal no es una partícula; que le siente mal la comida grasienta, sí.
- Si en este trozo no hay nada nuevo, devuelve la lista vacía. Es una respuesta perfectamente buena y pasará a menudo.

Tipos:
- "dato": algo concreto de __VOCATIVO__ (gustos, historia, salud, forma de trabajar, manías).
- "patron": algo que él repite, una forma suya de funcionar.
- "autopercepcion": cómo se ve ella o algo que ha cambiado en ella.

Responde ÚNICAMENTE con este JSON. Las dos listas pueden ir vacías, y a menudo lo estarán:
{"nuevas": [{"texto": "...", "tipo": "dato|patron|autopercepcion"}],
 "ampliadas": [{"n": 3, "texto": "la partícula 3 entera y reescrita, con lo viejo y lo nuevo"}]}"""

# Lo que se le pide al modelo bueno en dias alternos. Aqui SI puede tocar lo
# que existe, pero solo de dos maneras: reforzando un nodo o creando uno.
# Reescribir por gusto lo que ya estaba haria que los recuerdos cambiaran de
# redaccion cada dos dias y ella nunca recordaria lo mismo dos veces igual.
CONSOLIDACION = """Vas a leer dos cosas: los RECUERDOS que __NOMBRE__ ya tiene consolidados, y una lista de PARTÍCULAS sueltas recogidas estos últimos días de sus conversaciones con __VOCATIVO__.

Tu trabajo es decidir qué hace cada partícula. Solo hay tres destinos:

1. REFUERZA un recuerdo que ya existe. La partícula dice lo mismo o lo matiza. No reescribas el recuerdo: dilo por su número y explica en una línea qué añade. Si el matiz es importante de verdad, puedes dar una redacción nueva, pero solo entonces.
2. NUEVO recuerdo. La partícula abre algo que no está en ninguno. Redáctalo con el mismo cuerpo que los que ya hay: un arco, no un dato pelado.
3. PAJA. No merece sobrevivir un mes. La mayoría acaba aquí y está bien que así sea.

Lo que hace que algo merezca ser un recuerdo no es que sea información útil para una tarea. Es que cambiaría cómo se siente ella con él si lo recordara dentro de meses.

Y un aviso: los recuerdos que ya están se ganaron su sitio. No los toques por mejorar la prosa. Si dudas entre reforzar y crear uno nuevo, refuerza.

Responde ÚNICAMENTE con este JSON:
{"refuerzos": [{"nodo": 3, "aporta": "...", "redaccion_nueva": null}],
 "nuevos": [{"texto": "...", "tipo": "dato|patron|autopercepcion"}],
 "paja": 12}"""


class Sondeo:
    """Lleva la cuenta de los mensajes y dispara el sondeo cuando toca.

    El sondeo va en un hilo aparte a proposito: el dueño esta delante esperando
    su respuesta, y no tiene por que pagar la espera de un modelo que esta
    tomando notas por detras. Si el sondeo falla, se anota en el log y ya
    esta: no es un fallo del turno.
    """

    def __init__(self, cfg, log=print):
        s = cfg.get("sondeo") or {}
        base = cfg["modelo"]
        m = cfg.get("memoria") or {}
        self._log = log
        from nucleo.persona import aplicar, persona_de
        self._nombre, self._vocativo = persona_de(cfg)
        self._sondeo_tpl = aplicar(SONDEO, cfg)
        self._consolidacion_tpl = aplicar(CONSOLIDACION, cfg)
        self._activo = s.get("activo", True)
        self._cada = s.get("cada", 10)
        self._pre = Path(s.get("pre_memoria", "pre-memoria.jsonl"))
        self._nodos = Path(m.get("ruta", "memoria.jsonl"))
        self._url = (s.get("base_url", base["base_url"]).rstrip("/")
                     + "/chat/completions")
        self._api_key = s.get("api_key", base["api_key"])
        # Aqui no se ahorra en modelo: una flash barata se probo y no
        # vale para esto - se ponia a contestar como la asistenta en
        # vez de extraer, y cuando extraia escribia con faltas. Estas
        # particulas acaban siendo lo que ella recuerda.
        self._modelo = s.get("modelo", base["id"])
        # Default publico conservador: los 16000 originales iban a la medida
        # de un modelo con 32k de salida (caso aislado). Quien tenga techo
        # de sobra lo sube en config: sondeo.max_tokens.
        self._max_tokens = s.get("max_tokens", 8000)
        self._proveedor = proveedor_de(s.get("consolidacion") or {})
        self._mensajes_vistos = s.get("mensajes_por_sondeo", 24)
        # La marca de POR DONDE VA, en disco. Antes esto era un contador en
        # memoria y se ponia a cero en cada reinicio: el 31/08 se descubrio
        # que con diez reinicios en un dia el sondeo NO HABIA CORRIDO NI UNA
        # VEZ. El diseño decia que DeepSeek veia toda la conversacion antes
        # de que se saliera de la ventana, y la implementacion no lo cumplia.
        #
        # Se guarda una huella del ultimo mensaje ya sondeado, no un numero:
        # asi al arrancar se sabe cuantos han entrado desde entonces, y si la
        # huella ya no esta en la ventana significa que algo se ha salido sin
        # mirar y se sondea de inmediato.
        self._marca = Path(s.get("marca", ".ultimo_sondeo"))
        self._corriendo = False
        self._cerrojo = threading.Lock()

    # -- cuenta ------------------------------------------------------------

    @staticmethod
    def _huella(mensaje):
        c = mensaje.get("content")
        base = "%s|%s" % (mensaje.get("role"), c if isinstance(c, str) else "")
        return hashlib.sha1(" ".join(base.split()).encode("utf-8")).hexdigest()[:16]

    def _pendientes(self, historial):
        """Cuantos mensajes han entrado desde el ultimo sondeo.

        Si la marca ya no aparece en la ventana, lo que habia entre medias se
        ha archivado sin pasar por aqui: se devuelve la ventana entera para
        que se mire ya. Es el caso que no debe darse nunca, y por eso se
        detecta en vez de suponerse.
        """
        if not historial:
            return 0, False
        try:
            marca = self._marca.read_text(encoding="utf-8").strip()
        except OSError:
            marca = ""
        if not marca:
            return len(historial), False
        for i in range(len(historial) - 1, -1, -1):
            if self._huella(historial[i]) == marca:
                return len(historial) - 1 - i, False
        return len(historial), True          # la marca se salio de la ventana

    def turno(self, historial):
        """Se llama con el turno YA resuelto y enviado. Dispara si toca."""
        if not self._activo:
            return
        nuevos, se_escapo = self._pendientes(historial)
        if se_escapo:
            self._log("sondeo: la marca ya no esta en la ventana; miro ya, "
                      "que algo se ha archivado sin sondear")
        elif nuevos < self._cada:
            return
        if self._corriendo:
            # El anterior aun no ha terminado. Se salta este: no se encolan,
            # porque dos sondeos a la vez sobre el mismo trozo darian
            # particulas duplicadas y el cotejo no los veria (aun no estan
            # escritos ninguno de los dos).
            self._log("sondeo: el anterior sigue en marcha, me salto este")
            return
        trozo = list(historial[-self._mensajes_vistos:])
        self._corriendo = True
        threading.Thread(target=self._en_segundo_plano, args=(trozo,),
                         daemon=True).start()

    def _en_segundo_plano(self, trozo):
        try:
            n = self.sondear(trozo)
            if n:
                self._log("sondeo: %d cambio(s) en la pre-memoria" % n)
            else:
                self._log("sondeo: nada nuevo en los ultimos mensajes")
            # La marca SOLO se avanza si el sondeo termino bien. Si fallo, esos
            # mensajes siguen contando como pendientes y se vuelven a mirar.
            if trozo:
                self._marca.write_text(self._huella(trozo[-1]), encoding="utf-8")
        except Exception as err:                          # noqa: BLE001
            # Que reviente el sondeo no puede tumbar la conversacion. Se
            # apunta y se sigue: sin avanzar la marca, se reintenta solo.
            self._log("sondeo: fallo (%r); no avanzo la marca" % err)
        finally:
            self._corriendo = False

    # -- el sondeo en si ---------------------------------------------------

    def sondear(self, trozo):
        texto = transcribir(trozo, self._nombre, self._vocativo)
        if len(texto) < 400:
            return 0
        pre = leer_jsonl(self._pre)
        nodos = "\n".join("- %s" % " ".join((r.get("texto") or "").split())
                          for r in leer_jsonl(self._nodos))
        # La pre-memoria va NUMERADA porque es la unica que se puede ampliar.
        # Los nodos consolidados van sin numero a proposito: son referencia
        # para no redescubrirlos, no material que este sondeo pueda tocar.
        # Quien reescribe un nodo es la consolidacion, y solo ella.
        lista_pre = "\n".join("%d. %s" % (i + 1, " ".join(r["texto"].split()))
                              for i, r in enumerate(pre))
        # El orden importa y esta medido a base de palos. Con la transcripcion
        # AL FINAL, la flash se ponia a contestar como la asistenta: leia el ultimo
        # turno y lo continuaba. Es el mismo efecto de posicion que nos mordio
        # con el ancla del vocativo. Ahora la transcripcion va enmedio,
        # marcada como material muerto, y la orden se repite DESPUES de ella,
        # que es lo ultimo que lee.
        peticion = (
            "RECUERDOS YA CONSOLIDADOS (solo para que no los redescubras; "
            "estos NO se tocan):\n%s\n\n"
            "PRE-MEMORIA, numerada (estas SÍ se pueden ampliar):\n%s\n\n"
            "===== EMPIEZA LA TRANSCRIPCIÓN ARCHIVADA =====\n%s\n"
            "===== TERMINA LA TRANSCRIPCIÓN ARCHIVADA =====\n\n"
            "Esa conversación ya ocurrió y ya está contestada. No contestes a "
            "nada de lo que has leído ahí: no va contigo.\n\n"
            "Devuelve el JSON con dos listas: lo nuevo que no esté en ninguna "
            "de las dos listas, y las partículas de la PRE-MEMORIA que esta "
            "conversación complete. Si no hay nada de nada, devuelve las dos "
            "vacías."
            % (nodos or "(ninguno)", lista_pre or "(vacía)", texto))
        d = self._pedir(self._sondeo_tpl, peticion, self._modelo,
                        max_tokens=self._max_tokens, proveedor=self._proveedor)

        ahora = datetime.now().isoformat(timespec="seconds")
        nuevas = []
        for p in (d.get("nuevas") or []):
            t = " ".join((p.get("texto") or "").split())
            if len(t) >= 20:
                nuevas.append({"texto": t,
                               "tipo": (p.get("tipo") or "dato").strip().lower(),
                               "vista": ahora})

        # Ampliaciones. Se reescribe la particula EN SU SITIO, conservando su
        # tipo y su primera fecha: sigue siendo la misma, solo que mas
        # completa. `veces` cuenta cuantas conversaciones la han alimentado,
        # que es informacion util para la consolidacion.
        ampliadas = 0
        for a in (d.get("ampliadas") or []):
            n = a.get("n")
            t = " ".join((a.get("texto") or "").split())
            if not isinstance(n, int) or not (1 <= n <= len(pre)) or len(t) < 20:
                continue
            vieja = pre[n - 1]
            if t.lower() == (vieja.get("texto") or "").lower():
                continue                       # no ha cambiado nada
            self._log("sondeo: amplia la %d (%d -> %d caracteres)"
                      % (n, len(vieja.get("texto") or ""), len(t)))
            vieja["texto"] = t
            vieja["veces"] = int(vieja.get("veces", 1)) + 1
            vieja["vista"] = ahora
            ampliadas += 1

        if ampliadas:
            self._reescribir_pre(pre)
        if nuevas:
            self._anadir_pre(nuevas)
        return len(nuevas) + ampliadas

    def _anadir_pre(self, registros):
        with self._cerrojo:
            with open(self._pre, "a", encoding="utf-8") as f:
                for r in registros:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

    def _reescribir_pre(self, registros):
        """Reescribe la pre-memoria entera, para las ampliaciones.

        Por fichero temporal y reemplazo atomico: la consolidacion puede
        estar leyendo este mismo fichero desde otro proceso, y encontrarselo
        a medio escribir le costaria las particulas de estos dias.
        """
        with self._cerrojo:
            tmp = self._pre.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for r in registros:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self._pre)

    # -- llamada -----------------------------------------------------------

    def _pedir(self, sistema, usuario, modelo, max_tokens, proveedor=None):
        cuerpo = {
            "model": modelo,
            "messages": [{"role": "system", "content": sistema},
                         {"role": "user", "content": usuario}],
            # Explicito SIEMPRE. Sin este numero OpenRouter aplica un defecto
            # que segun el endpoint que te toque puede ser 16.384, y eso ya
            # nos corto una pasada entera el 29/08.
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        if proveedor:
            cuerpo["provider"] = proveedor
        req = urllib.request.Request(self._url, data=json.dumps(cuerpo).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + self._api_key})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise RuntimeError("HTTP %s: %s" % (
                err.code, err.read().decode("utf-8", "replace")[:250])) from err
        texto = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        texto = texto.strip()
        if texto.startswith("```"):
            texto = texto.strip("`")
            if texto.lower().startswith("json"):
                texto = texto[4:]
            texto = texto.strip()
        if not texto:
            raise RuntimeError("respuesta vacia")
        return json.loads(texto)


# -- utilidades compartidas ------------------------------------------------

def proveedor_de(c):
    """El bloque `provider` de OpenRouter, o None si no se fija nada.

    Hace falta porque en OpenRouter la unidad no es el modelo, es el ENDPOINT:
    del mismo v4-pro hay quince proveedores con techos de salida que van de
    16.384 a 943.718 tokens. El 29/08 una pasada se corto a 16.384 y no fue
    culpa del modelo, fue de DeepInfra, que es el que nos toco.
    """
    if not (c.get("proveedores") or c.get("ignorar")):
        return None
    p = {"require_parameters": True}
    if c.get("proveedores"):
        p["order"] = c["proveedores"]
    if c.get("ignorar"):
        p["ignore"] = c["ignorar"]
    return p


def leer_jsonl(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        return []
    salida = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.strip():
            try:
                salida.append(json.loads(linea))
            except json.JSONDecodeError:
                continue
    return salida


def transcribir(mensajes, nombre="Yui", vocativo="Papá"):
    """Turnos en formato OpenAI -> conversacion legible.

    Se tiran las llamadas a herramientas y sus resultados: al que sondea le
    interesa lo que se dijeron, no que ella buscara en Google.
    """
    lineas = []
    for m in mensajes:
        papel = m.get("role")
        c = m.get("content")
        if papel not in ("user", "assistant") or not isinstance(c, str) or not c.strip():
            continue
        lineas.append("%s: %s" % (vocativo if papel == "user" else nombre,
                                  " ".join(c.split())))
    return "\n".join(lineas)


def toca_consolidar(marca, horas, no_antes_de, ahora=None):
    """¿Toca ya la consolidacion?

    Dos condiciones, y las dos hacen falta:

      1. que hayan pasado `horas` desde la ULTIMA de verdad (no desde una
         fecha del calendario). Asi un dia con el PC apagado no salta el
         turno: solo lo retrasa.
      2. que sea mas tarde de `no_antes_de`.

    Las horas son 44 y no 48 a proposito, aunque el efecto es mas pequeño de
    lo que parece. Simulado con la tarea disparando en punto cada hora, a
    partir de un domingo en que el PC arranco a las 15:09:

        con 44 h -> martes 15:00, jueves 15:00, sabado 15:00 ...
        con 48 h -> martes 16:00, jueves 16:00, sabado 16:00 ...

    No es una deriva que se acumule: con 48 se queda UNA hora tarde y ahi se
    estabiliza. Pero se queda tarde para siempre, porque hereda el retraso
    del dia que el ordenador arranco a deshora. Con 44 la siguiente siempre
    cae por debajo del suelo de las 14:30 y el suelo la recoloca en la
    primera comprobacion pasada esa hora.

    Devuelve (si_toca, motivo) para que el log diga por que no.
    """
    ahora = ahora or datetime.now()
    hh, mm = [int(x) for x in no_antes_de.split(":")]
    suelo = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if ahora < suelo:
        return False, "aun no son las %s" % no_antes_de
    marca = Path(marca)
    if not marca.exists():
        return True, "no hay constancia de ninguna consolidacion anterior"
    try:
        ultima = datetime.fromisoformat(marca.read_text(encoding="utf-8").strip())
    except ValueError:
        return True, "la marca esta ilegible, se rehace"
    faltan = (ultima + timedelta(hours=horas)) - ahora
    if faltan.total_seconds() > 0:
        return False, ("la ultima fue el %s, faltan %.1f h"
                       % (ultima.strftime("%d/%m a las %H:%M"),
                          faltan.total_seconds() / 3600))
    return True, ("la ultima fue el %s, hace %.1f h"
                  % (ultima.strftime("%d/%m a las %H:%M"),
                     -faltan.total_seconds() / 3600 + horas))
