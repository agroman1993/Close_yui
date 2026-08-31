# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Decide que se responde a un mensaje.

Esta es LA costura del proyecto. El transporte no sabe quien contesta y este
modulo no sabe que existe Telegram: recibe un dict plano y devuelve texto.

Contencion por topologia: el modelo aqui SOLO produce texto. No tiene
herramientas, no elige rutas de fichero, no ejecuta nada. Si algun dia hace
falta que escriba algo, la ruta se fija en Python y el modelo solo aporta el
contenido. No es una regla que se le pide: es que la funcion no existe.
"""

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from nucleo.diario import HERRAMIENTA, INSTRUCCION, MAX_NOTAS_POR_TURNO, Diario
from nucleo.album import Album, ErrorAlbum
from nucleo.apuntes import (APUNTAR, OLVIDAR, Apuntes, ErrorApuntes,
                            pide_apunte)
from nucleo.web import ABRIR, BUSCAR, ErrorWeb, Web
from nucleo.web import INSTRUCCION as INSTRUCCION_WEB
from nucleo.escriba import Escriba
from nucleo.hilos import Hilos
from nucleo.insistir import NoReintentable, SinSuerte, insistir
from nucleo.memoria import Memoria
from nucleo.modelo import ErrorModelo, Modelo
from nucleo.reloj import bloque as bloque_reloj
from nucleo.vision import ErrorVision, Vision, como_percepcion

# Tope duro de rondas modelo->herramienta->modelo. El tope existe para que
# esto no pueda convertirse nunca en un bucle de agente, que es de las cosas
# que este proyecto no quiere tener. 21/08: con 2 rondas, un turno con dos
# notas de diario sin texto dejaba a la asistenta sin ronda para hablar y caia al
# fallback; con 3 queda siempre una ultima llamada para decir algo.
#
# 29/08: lo de "queda siempre una ultima llamada" no era verdad. Se penso por
# las notas del diario, pero CUALQUIER herramienta gasta ronda, y las busquedas
# encadenadas son el caso normal, no el raro. El tope se queda en 3 —no se
# sube, que es lo que lo convertiria en un bucle de agente—: lo que cambia es
# que al agotarlo se le hace una llamada final sin herramientas. Ver el final
# de _ciclo().
MAX_RONDAS = 3

# 29/08: aqui habia un FALLBACK_SIN_TEXTO, "Me he quedado pensando y no me ha
# salido la respuesta; ¿me repites eso, <vocativo>?". Se ha quitado, y el motivo es
# suyo: era un mensaje prefijado disfrazado de diagnostico. No decia la verdad
# —no se habia quedado pensando— y encima le pedia a el dueño que reintentara a
# mano algo que el codigo sabe reintentar solo.
#
# El fallo real era de arquitectura: _conversar() DEVOLVIA ese texto como si
# fuera una respuesta buena, asi que insistir() no lo veia nunca y el bucle de
# reintentos se lo saltaba entero. Ahora esa ruta lanza ErrorModelo y entra
# por el mismo sitio que los demas fallos: se reintenta con su presupuesto, y
# si de verdad se agota, sale el unico aviso que si es cierto, el de "llevo un
# par de minutos intentandolo".
#
# Un mensaje menos que mantener y una ruta menos por la que escaparse.

# No es tiempo, son intentos, y el motivo es suyo (29/08): el codigo sabe
# reintentar solo, asi que rendirse a los dos minutos para pedirle a el dueño que
# reescriba no arregla nada, solo le pasa a el un trabajo que es de la
# maquina. Se insiste hasta que salga. El unico freno es el muro: veinte
# llamadas seguidas sin una sola respuesta significa que el endpoint no esta.
# Con la espera creciendo hasta 30s, son unos ocho minutos.
INTENTOS_TURNO = 20
# A partir de cuantos fallos se le avisa de que la cosa va para largo. Con 3
# no se le molesta por un hipo, que se resuelve en cuatro segundos. Y ese
# aviso se BORRA de Telegram en cuanto llega la respuesta buena.
AVISAR_DESDE = 3
# Medido el 26/08 contra el contador del proveedor, sobre este mismo prompt en
# castellano y dentro de JSON: 23.444 caracteres daban ~8.400 tokens.
CARACTERES_POR_TOKEN = 2.79

# El album esta apagado desde el 26/08 (0 aciertos en 11 dias). Se deja
# coherente por si alguna vez vuelve: aqui nadie espera delante, asi que
# puede insistir mas que una conversacion.
INTENTOS_ALBUM = 30

# Cuantas veces se le sube el techo de salida cuando agota el presupuesto
# razonando. A partir de ahi, mas espacio solo es mas sitio para seguir
# pensando: el 20/08 se hicieron 7 intentos en 120s subiendo hasta 8000 tokens
# y no escribio ni una palabra. Si a la tercera no ha salido, se corta.
MAX_SUBIDAS_TECHO = 2

# A veces el proveedor devuelve la llamada a herramienta DOS veces: como tool_calls en
# regla y ademas escupida como texto crudo dentro del contenido. Si esa etiqueta
# se queda en el historial, la ve en el turno siguiente, aprende que el formato
# vale y lo repite hasta que acaba colandose en la respuesta al usuario. Se
# limpia antes de guardar nada.
FUGA_ETIQUETA = re.compile(
    r"<\s*(uncensored_)?tool_call\s*>.*?(?:</\s*(uncensored_)?tool_call\s*>|$)",
    re.S | re.I)
FUGA_SUELTA = re.compile(r"</?\s*(?:arg_key|arg_value|tool_call|uncensored_tool_call)\s*>", re.I)


# Nota de campo (modelo original, base GLM): de vez en cuando se le cuela una palabra en chino
# ("me好奇", "特别especial"). En una conversacion en español no hay ni un
# caracter CJK legitimo, asi que detectarlo no puede dar falso positivo. Se
# reintenta una vez; si vuelve a salir se envia igual, porque un mensaje con
# una palabra rara es mejor que ningun mensaje.
CJK = re.compile(r"[一-鿿㐀-䶿぀-ヿ가-힯]")

# Lo que va entre <cita> y </cita> NO se traduce. Es contencion por topologia,
# no una regla que ella tenga que cumplir: el filtro de arriba no distingue
# —y no puede— entre una fuga del motor GLM y una cita deliberada, porque las
# dos son exactamente los mismos caracteres. La marca es lo unico que las
# separa: lo marcado es a proposito, lo suelto es una fuga.
#
# Sin esto, si escribia 木漏れ日 queriendo, el traductor se lo convertia en
# "luz filtrada entre las hojas" y la cita se perdia. Lo pidio el 29/08 y
# eligio la sintaxis: la de HTML, que es la que se reconoce a simple vista.
#
# Las etiquetas no viajan a Telegram: las quita el transporte, igual que los
# asteriscos del markdown. Si se quedan en su historial es a proposito, para
# que vea su propia convencion y la siga usando.
CITA = re.compile(r"<cita>.*?</cita>", re.S | re.I)


def _huecos_citados(texto):
    """Los tramos (inicio, fin) que estan dentro de una <cita>."""
    return [(m.start(), m.end()) for m in CITA.finditer(texto or "")]


def _esta_citado(pos, huecos):
    return any(a <= pos < b for a, b in huecos)


def tiene_chino(texto):
    """¿Hay CJK que NO sea una cita deliberada?"""
    if not texto:
        return False
    huecos = _huecos_citados(texto)
    return any(not _esta_citado(m.start(), huecos) for m in CJK.finditer(texto))


def traducir_chino(texto, modelo, log=print):
    """Sustituye los fragmentos CJK por su traduccion, sin tocar el resto.

    Reintentar el turno entero era el dia de la marmota: mismo contexto, misma
    tirada, una generacion completa quemada por dos caracteres. Aqui se traducen
    SOLO los fragmentos —normalmente una palabra— y se sustituyen en su sitio.
    Una llamada minuscula, sin bucle, y el resto del mensaje intacto.
    """
    huecos = _huecos_citados(texto)
    # Solo los de fuera de una <cita>. Se guardan las POSICIONES, no solo el
    # texto: replace() es global y habria pisado tambien lo citado si la misma
    # palabra aparecia dentro y fuera.
    sitios = [m for m in re.finditer(r"[一-鿿㐀-䶿぀-ヿ가-힯]+", texto)
              if not _esta_citado(m.start(), huecos)]
    if not sitios:
        return texto
    fragmentos = []
    for m in sitios:
        if m.group(0) not in fragmentos:
            fragmentos.append(m.group(0))
    try:
        crudo, _ = modelo.responder([
            {"role": "system", "content":
             "Traduce al español cada fragmento. Devuelve SOLO las traducciones, "
             "una por línea, en el mismo orden y sin numerar ni explicar nada."},
            {"role": "user", "content": "\n".join(fragmentos)},
        ])
    except ErrorModelo as err:
        log("chino: no se pudo traducir (%s); se deja como esta" % err)
        return texto
    lineas = [l.strip() for l in (crudo or "").splitlines() if l.strip()]
    if len(lineas) != len(fragmentos):
        log("chino: traduccion descuadrada (%d/%d); se deja como esta"
            % (len(lineas), len(fragmentos)))
        return texto
    traduccion = {o: es for o, es in zip(fragmentos, lineas) if not CJK.search(es)}
    # De atras hacia adelante, para que las posiciones ya calculadas sigan
    # siendo validas mientras se sustituye.
    for m in reversed(sitios):
        es = traduccion.get(m.group(0))
        if es:
            texto = texto[:m.start()] + es + texto[m.end():]
    log("chino: %d fragmento(s) traducido(s): %s" % (len(traduccion), ", ".join(traduccion)))
    return texto


# Prefijo basura: un token suelto de letras/digitos terminado en ">" al inicio
# de la respuesta, sin etiqueta de apertura que lo ampare (las de verdad las
# cazan FUGA_*). Es el fallo del "grosorode>": un resto de mala generacion
# antes del texto bueno. Exige 2+ caracteres y un salto detras para no
# llevarse por delante cosas legitimas como "5>3", "->" o "<3". Familia 1:
# se limpia y ya, sin reintento.
PREFIJO_BASURA = re.compile(r"^\s*(?:</?)?[A-Za-z0-9_áéíóúñüÁÉÍÓÚÑÜ]{2,24}>\s+")


def limpiar_fuga(texto):
    """Quita etiquetas de herramienta coladas en el texto y prefijos basura.

    Si la limpieza dejara la respuesta vacia (todo era basura), se conserva
    el original: algo raro es mejor que nada.
    """
    if not texto:
        return texto
    limpio = FUGA_ETIQUETA.sub("", texto)
    limpio = FUGA_SUELTA.sub("", limpio)
    sin_prefijo = PREFIJO_BASURA.sub("", limpio, count=1)
    if sin_prefijo.strip():
        limpio = sin_prefijo
    return limpio.strip()

def _pesa(mensaje):
    """Lo que ocupa un mensaje en el prompt, en caracteres.

    Se mide sobre el JSON serializado porque es lo que viaja de verdad: las
    claves "role" y "content" tambien ocupan sitio en el contexto.
    """
    return len(json.dumps(mensaje, ensure_ascii=False))


# Raiz del proyecto vista desde nucleo/: donde viven IDENTITY.md y compania.
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

# Los comentarios HTML de los .md de personalidad son para el dueño, no para el
# modelo: se quitan antes de inyectar (igual que hace main.py al arrancar).
_COMENTARIO = re.compile(r"<!--.*?-->", re.S)

# Historial en memoria, no en disco. No es un "sistema de memoria": es el
# minimo para que una conversacion tenga hilo. Se pierde al reiniciar, y de
# momento eso es exactamente lo que queremos.
TURNOS_POR_DEFECTO = 12

# Ancla de cierre: la regla del vocativo ("llamar al dueño por su vocativo, siempre") se
# repite al FINAL del contexto de cada turno, pegada a la generacion. El SOUL
# ya la lleva arriba, pero el modelo pesa mas lo ultimo que lee: si el
# historial reciente encadena respuestas sin "el dueño", la inercia gana (pasó el
# 20/08: seis respuestas seguidas sin el vocativo con el SOUL ya releyendose).
# No es memoria falsa: es la misma regla del SOUL en el sitio de mas peso.
# Verificado el 20/08 que el proveedor acepta un system de cierre.
ANCLA_VOCATIVO = "Ancla: a __VOCATIVO__ lo llamas __VOCATIVO__. Siempre; nunca su nombre."

# Que tipos de Telegram sabe mirar el nodo visual y como se traducen.
VISUALES = {
    "photo": "imagen", "sticker": "imagen",
    "video": "video", "animation": "video", "video_note": "video",
}
# Muse no procesa input_audio como sonido (comprobado en el banco: dice que
# solo percibe texto). Un audio suelto no tiene ruta todavia.
SIN_RUTA = {
    "voice": "una nota de voz",
    "audio": "un audio",
    "document": "un documento",
}


class Enrutador:
    def __init__(self, cfg, log=print):
        m = cfg["modelo"]
        self._modelo = Modelo(
            base_url=m["base_url"],
            api_key=m["api_key"],
            modelo_id=m["id"],
            max_tokens=m.get("max_tokens", 2000),
            temperatura=m.get("temperatura", 0.9),
            razonamiento=m.get("razonamiento"),
            log=log,
        )
        self._cfg = cfg
        from nucleo.persona import aplicar, persona_de
        self._ancla = aplicar(ANCLA_VOCATIVO, cfg)
        self._sistema = (cfg.get("sistema") or "").strip()
        # Ancla fresca por turno: la personalidad se relee del disco en cada
        # mensaje. Si el dueño edita un .md entra en caliente, sin reiniciar.
        self._rutas_sistema = []
        for nombre in (cfg.get("sistema_ficheros") or []):
            ruta = Path(nombre)
            if not ruta.is_absolute():
                ruta = RAIZ_PROYECTO / ruta
            self._rutas_sistema.append(ruta)
        # La ventana se mide en TOKENS, no en turnos: treinta turnos pueden ser
        # 8k o 40k segun lo que se haya hablado, y el limite del modelo es en
        # tokens. `turnos_memoria` se sigue admitiendo por compatibilidad.
        self._max_tokens_hilo = cfg.get("contexto_tokens")
        self._max_turnos = cfg.get("turnos_memoria", TURNOS_POR_DEFECTO)
        self._log = log
        # El hilo sobrevive a los reinicios: misma ventana, pero en disco.
        h = cfg.get("hilos") or {}
        self._hilos = Hilos(h["ruta"], log=log) if h.get("ruta") else None
        self._historial = self._hilos.cargar() if self._hilos else {}
        self._vision = Vision(cfg, log=log) if cfg.get("vision") else None

        d = cfg.get("diario") or {}
        self._diario = Diario(d["ruta"], d.get("max_caracteres", 4000), log=log) if d.get("ruta") else None
        # La web es opcional y solo existe si esta en el config. Si no
        # esta, no es que se le prohiba buscar: es que no hay funcion que
        # llamar. Lo mismo que el diario.
        self._web = Web(cfg.get("web"), log=log) if cfg.get("web") else None
        self._herramientas = ([HERRAMIENTA] if self._diario else []) + \
                             ([BUSCAR, ABRIR] if self._web else [])
        self._herramientas = self._herramientas or None
        # MEMORY.md. Las herramientas de escribir y borrar NO van en la
        # lista de arriba a proposito: se anaden solo en el turno en que
        # el dueño lo ha pedido. Ver _extra_del_turno().
        self._apuntes = Apuntes(cfg.get("apuntes", "MEMORY.md"), log=log, persona=persona_de(cfg))
        self._extra = []
        # Redacta las entradas por detras, fuera del turno: la intencion nace en
        # la conversacion, la escritura ocurre a solas.
        self._escriba = Escriba(cfg, self._diario, log=log) if self._diario else None
        self._memoria = Memoria(cfg, log=log) if cfg.get("memoria") else None
        # Segundo indice, el de lo que ha visto. Separado del de texto a
        # proposito: sus cosenos viven en otra escala y no se pueden
        # ordenar en la misma lista.
        self._album = Album(cfg, log=log) if cfg.get("album") else None
        self._vistos = None
        # Ultimo medio recibido por chat, para /muse. Un fichero por chat,
        # sobrescrito; no crece con el uso.
        self._ultimo_medio = Path(__file__).resolve().parent.parent / "ultima_vista"
        # La conversacion entera, tal cual, segun va saliendo de la ventana.
        self._archivo = Path(__file__).resolve().parent.parent / "archivo"
        # Recuerdos del turno en curso: se recalculan cada vez y NO se guardan
        # en el historial. Si se acumularan, los de hace veinte mensajes
        # empujarian fuera a los que vienen al caso ahora.
        self._recuerdos = None
        # Cuando se recibio el ultimo mensaje de cada chat, para poder decirle
        # cuanto silencio ha habido. Se pierde al reiniciar y no pasa nada: el
        # primer mensaje tras arrancar simplemente no lleva hueco.
        self._ultimo_visto = {}
        self._ahora = None
        # El sueno se cuelga aqui desde fuera (main.py) para que el enrutador
        # no tenga que saber nada de hilos ni de temporizadores.
        self.sueno = None

    def responder(self, mensaje):
        """mensaje -> texto de respuesta (o None para no contestar).

        Si trae medio, `mensaje['datos']` son los bytes ya descargados por
        quien conoce el transporte. Aqui no se sabe de donde vienen.
        """
        self._refrescar_sistema()
        tipo = mensaje["tipo"]
        historial = self._historial.setdefault(mensaje["chat_id"], [])

        # /muse trae la percepcion ya hecha: se vuelve a mirar la foto de antes
        # y lo que sale entra por aqui, sin pasar otra vez por el nodo visual.
        percepcion = mensaje.get("percepcion")
        if percepcion is None and tipo != "texto":
            percepcion, aviso = self._mirar(mensaje)
            if aviso:
                return aviso
            self._guardar_visto(mensaje, percepcion)
            if mensaje.get("datos"):
                self._recordar_medio(mensaje["chat_id"], mensaje["datos"],
                                     mensaje.get("mime"), VISUALES.get(tipo, "imagen"))

        texto = (mensaje["texto"] or "").strip()
        if not texto and percepcion is None:
            return None
        if not texto:
            # Medio sin pie de foto: se deja constancia del acto, no del vacio.
            texto = "(te envio esto sin decir nada)"

        # La puerta de MEMORY.md. Se abre mirando SOLO lo que ha escrito el dueño,
        # nunca lo que escribe ella: si esto mirase la respuesta del modelo,
        # el modelo podria abrirse la puerta solo escribiendo "apúntate esto".
        # Cerrada, las dos herramientas ni siquiera viajan en la peticion.
        self._extra = [APUNTAR, OLVIDAR] if pide_apunte(texto) else []
        if self._extra:
            self._log("apuntes: el dueño ha pedido un apunte; herramientas abiertas")

        # Marca para poder deshacer el turno entero si el modelo falla: con
        # tool_calls de por medio ya no basta con quitar uno o dos mensajes.
        marca = len(historial)
        if percepcion:
            historial.append({"role": "system", "content": percepcion})
        historial.append({"role": "user", "content": texto})
        self._recortar(historial, mensaje["chat_id"])
        marca = min(marca, len(historial))

        self._recuerdos = self._memoria.recordar(texto) if self._memoria else None
        self._vistos = self._mirar_recuerdos(texto)
        self._ahora = bloque_reloj(self._ultimo_visto.get(mensaje["chat_id"]))
        self._ultimo_visto[mensaje["chat_id"]] = time.time()
        # El turno insiste mientras quede presupuesto: un fallo de saturacion
        # o de red no debe leerse como un silencio de ella. No hay suplente:
        # si el modelo no esta, se espera.
        def subir_techo(err, intento):
            """Solo sube el techo cuando de verdad se agoto el presupuesto.

            Una respuesta vacia con fin="stop" NO es falta de sitio: es el
            proveedor fallando un momento. Medido el 24/08: pasa en 2 de cada
            10 llamadas y la siguiente va bien. Subir max_tokens ahi no arregla
            nada, y rendirse a los 19 segundos convierte un tropiezo de un
            momento en un turno perdido.
            """
            if "AGOTADO" not in str(err):
                return          # vacia transitoria: se reintenta tal cual
            if intento > MAX_SUBIDAS_TECHO:
                raise NoReintentable(err)
            techo = self._modelo.subir_max_tokens(1500)
            if techo:
                self._log("modelo: techo de salida subido a %d tokens" % techo)

        try:
            respuesta = insistir(lambda: self._conversar(historial),
                                 errores=(ErrorModelo,),
                                 intentos_max=INTENTOS_TURNO,
                                 log=self._log, etiqueta="modelo:",
                                 antes_de_reintentar=subir_techo,
                                 al_tardar=self._al_tardar,
                                 avisar_desde=AVISAR_DESDE)
        except SinSuerte as err:
            self._log("modelo: sin suerte tras %d intento(s) en %ds"
                      % (err.intentos, err.segundos))
            respuesta = self._con_suplente(historial)
            if respuesta is None:
                # Se deshace el turno completo: si queda suelto sin respuesta,
                # el historial acumula preguntas sin contestar y desvia al
                # modelo.
                del historial[marca:]
                # Este mensaje SI dice la verdad, y por eso se queda: veinte
                # llamadas seguidas sin una sola respuesta no es una racha, es
                # que el endpoint no esta. Lo que NO se le pide es que repita
                # nada: su mensaje sigue sin confirmar en Telegram y volvera
                # a entrar solo.
                return ("El proveedor no responde. Llevo %d intentos en %d "
                        "minutos y no hay manera — no es cosa tuya ni hace "
                        "falta que repitas nada, lo sigo intentando yo."
                        % (err.intentos, max(1, err.segundos // 60)))

        if tiene_chino(respuesta):
            # Una llamada minuscula que traduce solo los fragmentos. Nada de
            # reintentar el turno entero: eso no lo arregla, solo lo repite.
            respuesta = traducir_chino(respuesta, self._modelo, log=self._log)

        historial.append({"role": "assistant", "content": respuesta})
        self._recortar(historial, mensaje["chat_id"])
        self._persistir()
        return respuesta

    @property
    def chats(self):
        return list(self._historial)

    @property
    def modelo(self):
        return self._modelo

    @property
    def diario(self):
        return self._diario

    @property
    def memoria(self):
        return self._memoria

    def _del_turno(self):
        """Las herramientas de ESTE turno: las de siempre mas las que el dueño
        haya abierto con su mensaje.

        Aqui esta la contencion. Si el no ha pedido un apunte, APUNTAR y
        OLVIDAR sencillamente no viajan en la peticion, asi que el modelo no
        puede llamarlas aunque quiera. No es una regla que ella cumpla: es que
        no hay funcion.
        """
        base = list(self._herramientas or [])
        return (base + self._extra) or None

    def _apuntar(self, nombre, funcion):
        """Escribe o borra en MEMORY.md. Solo se llega aqui si _del_turno()
        dejo pasar la herramienta, y eso solo pasa si el dueño lo pidio."""
        if not self._extra:
            # Cinturon: aunque el modelo se inventara la llamada, sin puerta
            # abierta no se toca el fichero.
            self._log("aviso: apunte pedido sin que el dueño lo autorizara; ignorado")
            return "error: esa funcion no existe"
        try:
            args = json.loads(funcion.get("arguments") or "{}")
        except json.JSONDecodeError:
            return "error: argumentos ilegibles"
        try:
            if nombre == "apuntar_en_memoria":
                return self._apuntes.apuntar(args.get("texto") or "")
            return self._apuntes.olvidar(args.get("sobre") or args.get("texto") or "")
        except ErrorApuntes as err:
            return "error: %s" % err

    def _al_tardar(self):
        """Avisa de que esto va para largo, si alguien de fuera puso como.

        El enrutador no sabe que existe Telegram y no tiene por que saberlo:
        main.py le pone aqui una funcion que manda el aviso y se queda con su
        message_id, para borrarlo despues. Si nadie la pone, no se avisa y ya.
        """
        avisar = getattr(self, "avisar", None)
        if avisar:
            try:
                avisar()
            except Exception as err:                  # noqa: BLE001
                self._log("aviso: no se pudo avisar de la espera (%r)" % err)

    def hilo(self, chat_id):
        """La ventana viva de esa conversacion, para quien la quiera LEER.

        La usa el sondeo, que necesita el trozo de conversacion tal cual se
        dijo. Se devuelve una copia: nadie de fuera toca el historial, que
        lo recorta y lo archiva esta clase y solo esta clase.
        """
        return list(self._historial.get(chat_id) or [])

    def olvidar(self, chat_id):
        """Vacia el hilo de esa conversacion."""
        self._historial.pop(chat_id, None)
        self._persistir()

    def _persistir(self):
        if self._hilos:
            self._hilos.guardar(self._historial)

    # -- interno -----------------------------------------------------------

    def _conversar(self, historial):
        # El escriba necesita ver la conversacion; se guarda la referencia del
        # turno en curso para no tener que arrastrarla por toda la cadena.
        self._historial_en_curso = historial
        """Llama al modelo y atiende sus llamadas a la herramienta.

        Las notas del diario son silenciosas: nunca salen en la respuesta ni
        se le confirman al usuario. El registro del intercambio SI se queda en
        el historial, para que no vuelva a apuntar dos veces lo mismo.
        """
        notas = 0
        for ronda in range(MAX_RONDAS):
            espera_datos = False
            texto, llamadas = self._modelo.responder(self._construir(historial),
                                                     herramientas=self._del_turno())
            texto = limpiar_fuga(texto)
            if not llamadas:
                return texto

            historial.append({"role": "assistant", "content": texto or None,
                              "tool_calls": llamadas})
            for llamada in llamadas:
                nombre = ((llamada.get("function") or {}).get("name") or "")
                if nombre == "append_to_diary":
                    notas += 1
                else:
                    # Herramienta que DEVUELVE algo que ella tiene que leer.
                    espera_datos = True
                resultado = self._ejecutar(llamada, permitido=notas <= MAX_NOTAS_POR_TURNO)
                historial.append({"role": "tool",
                                  "tool_call_id": llamada.get("id"),
                                  "content": resultado})

            # Si ya ha dicho algo en esta misma ronda, ESO es la respuesta: el
            # diario es silencioso, asi que despues de apuntar no le queda nada
            # que anadir. Pedirle otra ronda hacia que repitiera lo dicho o,
            # peor, que contestara "mi respuesta anterior ya esta completa" y
            # se enviara ese parte de estado en lugar de lo que habia escrito.
            if texto and not espera_datos:
                return texto
            if ronda == MAX_RONDAS - 1:
                # Se acabaron las rondas y sigue pidiendo herramientas.
                #
                # Antes aqui se devolvia lo que hubiera dicho, que en una
                # cadena de busquedas es NADA: cada busqueda gasta una ronda,
                # y con tres seguidas no le queda ninguna para hablar. Paso el
                # 29/08 con la pregunta de Wanda Maximoff: busco tres veces
                # (5 resultados, 1, 0) y salio el fallback. El comentario de
                # arriba decia que con 3 rondas "queda siempre una ultima
                # llamada para decir algo" y era falso: la tercera se la comia
                # la tercera busqueda.
                #
                # Ahora se le da esa ultima llamada de verdad, SIN pasarle las
                # herramientas. No se le pide que conteste: es que ya no puede
                # pedir nada mas, asi que tiene que responder con lo que trae.
                # Es una llamada de mas, y solo en el caso raro.
                if texto:
                    return texto
                self._log("aviso: tope de rondas, ultima llamada sin herramientas")
                ultimo, _ = self._modelo.responder(self._construir(historial))
                ultimo = limpiar_fuga(ultimo)
                if ultimo:
                    return ultimo
                raise ErrorModelo("se acabaron las rondas y la llamada final "
                                  "tampoco escribio nada")
        raise ErrorModelo("el ciclo termino sin texto")

    def _ejecutar(self, llamada, permitido):
        """Ejecuta una llamada a herramienta y devuelve el resultado en texto."""
        funcion = (llamada.get("function") or {})
        nombre = funcion.get("name")
        if nombre in ("search_web", "open_result"):
            return self._navegar(nombre, funcion)
        if nombre in ("apuntar_en_memoria", "olvidar_de_memoria"):
            return self._apuntar(nombre, funcion)
        if nombre != "append_to_diary" or not self._diario:
            self._log("aviso: el modelo pidio una herramienta desconocida: %r" % nombre)
            return "error: esa funcion no existe"
        if not permitido:
            self._log("aviso: mas de %d notas en un turno, ignorada" % MAX_NOTAS_POR_TURNO)
            return "error: limite de notas por turno alcanzado"
        try:
            args = json.loads(funcion.get("arguments") or "{}")
        except json.JSONDecodeError:
            return "error: argumentos ilegibles"
        sobre = (args.get("sobre") or args.get("texto") or "").strip()
        # Se responde al momento y se redacta por detras: ella no espera a
        # escribir para seguir hablando, y la entrada se compone con calma.
        self._escriba.apuntar_luego(self._historial_en_curso, sobre)
        self._log("diario: intencion anotada (%s)" % (sobre[:60] or "sin detalle"))
        return "anotado; lo escribirás luego"

    def _con_suplente(self, historial):
        """Un ultimo intento con el hermano pequeno. Devuelve texto o None.

        Solo se llega aqui cuando los reintentos con el grande se han comido
        los 120 segundos enteros. No es un atajo para ir mas barato: es la
        diferencia entre que ella conteste distinto una vez de cada ciento y
        pico, o que parezca muerta.

        Queda escrito en el log a proposito. Un suplente que no se anuncia es
        justo lo que este proyecto no quiere.
        """
        suplente = (self._cfg.get("modelo") or {}).get("suplente")
        if not suplente:
            return None
        original = self._modelo._id
        if suplente == original:
            return None
        self._log("modelo: SUPLENTE — %s no contesta, lo intenta %s"
                  % (original, suplente))
        self._modelo._id = suplente
        try:
            texto = self._conversar(historial)
        except ErrorModelo as err:
            self._log("modelo: el suplente tampoco (%s)" % err)
            return None
        finally:
            self._modelo._id = original
        if texto:
            self._log("modelo: contesto el suplente (%d caracteres)" % len(texto))
        return texto or None

    def _mirar_recuerdos(self, texto):
        """Busca en el album y vuelve a MIRAR lo que salga.

        No se devuelve la descripcion que se escribio el dia que llego la foto:
        aquella se redacto sin saber que se le iba a preguntar hoy. Se recupera
        el medio y Muse lo mira otra vez con la pregunta delante.

        Si Muse falla, no se sustituye por la descripcion vieja: se calla. Un
        recuerdo visual a medias es peor que ninguno.
        """
        if not (self._album and self._vision):
            return None
        try:
            hallados = self._album.recordar(texto)
        except ErrorAlbum as err:
            self._log("album: %s" % err)
            return None
        lecturas = []
        for h in hallados:
            try:
                lecturas.append(self._vision.recordar_mirando(
                    self._album.leer(h), h.get("mime"),
                    h.get("tipo", "imagen"), texto))
            except (ErrorAlbum, ErrorVision) as err:
                self._log("album: no se pudo volver a mirar (%s)" % err)
        return lecturas or None

    def _guardar_visto(self, mensaje, percepcion):
        """Indexa el medio recien llegado, por detras.

        Va en un hilo aparte porque vectorizar tarda un segundo largo y no
        tiene por que hacerle esperar a el para contestar.
        """
        if not (self._album and mensaje.get("datos")):
            return
        datos = mensaje["datos"]
        mime = mensaje.get("mime")
        tipo = VISUALES.get(mensaje["tipo"], "imagen")
        sobre = (percepcion or "")[:600]
        chat = mensaje.get("chat_id")

        def trabajo():
            try:
                insistir(lambda: self._album.guardar(datos, mime, sobre,
                                                     tipo=tipo, chat_id=chat),
                         errores=(ErrorAlbum,),
                         intentos_max=INTENTOS_ALBUM,
                         log=self._log, etiqueta="album:")
            except SinSuerte as err:
                # Diez minutos insistiendo. Ahora si se rinde, y se dice:
                # una foto que no entra en el album no vuelve sola.
                self._log("album: no se pudo guardar tras %d intento(s) en %ds (%s)"
                          % (err.intentos, err.segundos, err.ultimo))

        threading.Thread(target=trabajo, daemon=True).start()

    def _navegar(self, nombre, funcion):
        """search_web / open_result. Un fallo se cuenta tal cual: si la pagina
        no se deja leer, ella lo sabe y prueba otra en vez de inventarsela."""
        if not self._web:
            return "error: esa funcion no existe"
        try:
            args = json.loads(funcion.get("arguments") or "{}")
        except json.JSONDecodeError:
            return "error: argumentos ilegibles"
        try:
            if nombre == "search_web":
                return self._web.buscar(args.get("consulta") or args.get("query"))
            return self._web.abrir(args.get("numero", args.get("number")))
        except ErrorWeb as err:
            self._log("web: %s" % err)
            return ("No se ha podido leer eso: %s. Si habia mas resultados, "
                    "prueba con otro." % err)

    def _mirar(self, mensaje):
        """Devuelve (percepcion, aviso). Si hay aviso, se responde eso y ya.

        Nunca se inventa una descripcion: si el nodo falla, se dice.
        """
        tipo = mensaje["tipo"]
        if tipo in SIN_RUTA:
            return None, ("Me ha llegado %s, pero todavia no tengo forma de "
                          "procesarlo. Por ahora solo imagenes y videos."
                          % SIN_RUTA[tipo])
        if tipo not in VISUALES:
            return None, "Me ha llegado un archivo de tipo '%s' que no se mirar." % tipo
        if not self._vision:
            return None, "No tengo el nodo visual configurado, asi que no puedo ver esto."

        if mensaje.get("error_medio"):
            return None, "No he podido descargar el archivo: %s" % mensaje["error_medio"]
        if not mensaje.get("datos"):
            return None, "El archivo ha llegado vacio."

        try:
            obj = self._vision.mira(mensaje["datos"], mensaje.get("mime"), VISUALES[tipo])
        except ErrorVision as err:
            self._log("ERROR del nodo visual:", err)
            return None, "He recibido el archivo pero no he podido mirarlo: %s" % err
        return como_percepcion(obj), None

    def _recordar_medio(self, chat_id, datos, mime, tipo):
        """Guarda el ultimo medio de ese chat, para poder volver a mirarlo.

        En disco y no en memoria a proposito: esto se reinicia a diario y
        seria absurdo que /muse dejara de funcionar por eso. Un fichero por
        chat, sobrescrito: no crece.
        """
        try:
            self._ultimo_medio.mkdir(parents=True, exist_ok=True)
            (self._ultimo_medio / ("%s.bin" % chat_id)).write_bytes(datos)
            (self._ultimo_medio / ("%s.json" % chat_id)).write_text(
                json.dumps({"mime": mime, "tipo": tipo}), encoding="utf-8")
        except OSError as err:
            self._log("ultimo medio: no se pudo guardar (%s)" % err)

    def enfocar(self, chat_id, enfoque, datos=None, mime=None, tipo=None):
        """Vuelve a mirar el ultimo medio de ese chat, enfocando.

        Devuelve (percepcion, resumen, aviso). `percepcion` es el bloque listo
        para inyectar en su contexto; `resumen` es texto llano para ensenarselo
        a el; `aviso` es lo que hay que contestar si no se ha podido.
        """
        if not self._vision:
            return None, None, "No tengo el nodo visual configurado."
        # Si el medio viene con el propio mensaje —una foto con /muse de pie de
        # foto— es ESE el que hay que mirar, no el de la vez pasada. El 26/08
        # se miro el anterior y ella hablo de una foto que el no tenia delante.
        if datos:
            self._recordar_medio(chat_id, datos, mime, VISUALES.get(tipo, tipo or "imagen"))
        cuerpo = self._ultimo_medio / ("%s.bin" % chat_id)
        ficha = self._ultimo_medio / ("%s.json" % chat_id)
        if not (cuerpo.exists() and ficha.exists()):
            return None, None, "No tengo ninguna foto reciente que mirar."
        try:
            meta = json.loads(ficha.read_text(encoding="utf-8"))
            datos = cuerpo.read_bytes()
        except (OSError, json.JSONDecodeError) as err:
            return None, None, "No he podido recuperar la ultima foto: %s" % err
        try:
            obj = self._vision.mira(datos, meta.get("mime"),
                                    meta.get("tipo", "imagen"), enfoque=enfoque)
        except ErrorVision as err:
            self._log("enfoque: %s" % err)
            return None, None, "He vuelto a mirarla pero no he podido: %s" % err

        elementos = obj.get("elementos_clave") or []
        if isinstance(elementos, list):
            elementos = "\n".join("- %s" % e for e in elementos)
        resumen = "%s\n\n%s\n\n%s" % (obj.get("descripcion_general") or "",
                                       elementos,
                                       obj.get("linea_temporal_o_detalles") or "")
        return como_percepcion(obj), resumen.strip(), None

    def _refrescar_sistema(self):
        """Relee los .md de personalidad en cada turno.

        El ancla llega recien leida a cada llamada al modelo, y si el dueño edita
        un .md entra en caliente sin reiniciar. Tolerante: un fichero que
        falta o no se puede leer se salta; si ninguno da texto, se conserva
        lo ultimo que habia (el ancla nunca se queda vacia por un susto).
        """
        if not self._rutas_sistema:
            return
        partes = []
        for ruta in self._rutas_sistema:
            try:
                texto = _COMENTARIO.sub("", ruta.read_text(encoding="utf-8")).strip()
            except OSError:
                continue
            if texto:
                partes.append(texto)
        if not partes:
            return
        nuevo = "\n\n".join(partes)
        if nuevo != self._sistema:
            self._log("prompt: personalidad releida en turno (%d caracteres)" % len(nuevo))
            self._sistema = nuevo

    def _construir(self, historial):
        # Dos capas de sistema a proposito: la personalidad es de el dueño y la
        # tecnica es del codigo. Asi el mecanismo del diario funciona escriba
        # lo que escriba en el SOUL, y el no tiene que recordar la sintaxis.
        mensajes = []
        if self._sistema:
            # El ancla del vocativo va AQUI, pegada a la personalidad, y no
            # al final como estaba. Medido el 25/08, pareado:
            #
            #   al final     2/8 vacias,  81 tokens, decia el vocativo 4/8
            #   al principio 0/8 vacias, 249 tokens, decia el vocativo 8/8
            #
            # Un mensaje de sistema corto y directivo justo antes de que
            # hable la deja en monosilabos y a veces muda del todo. Y ahi
            # cumplia peor su propio trabajo que pegada al SOUL.
            mensajes.append({"role": "system",
                             "content": self._sistema + "\n\n" + self._ancla})
        elif self._ancla:
            mensajes.append({"role": "system", "content": self._ancla})
        if self._diario:
            mensajes.append({"role": "system", "content": INSTRUCCION})
        if self._web:
            mensajes.append({"role": "system", "content": INSTRUCCION_WEB})
        # MEMORY.md va SIEMPRE, lo haya pedido o no en este turno: escribir en
        # el esta cerrado, leerlo no. De nada serviria apuntar algo si solo lo
        # recordara en el turno en que se lo dijeron.
        bloque = self._apuntes.como_bloque()
        if bloque:
            mensajes.append({"role": "system", "content": bloque})
        if self._recuerdos:
            bloque = Memoria.como_bloque(self._recuerdos)
            if bloque:
                mensajes.append({"role": "system", "content": bloque})
        if self._vistos:
            bloque = Album.como_bloque(self._vistos)
            if bloque:
                mensajes.append({"role": "system", "content": bloque})
        # El ultimo de todos: es el mas inmediato y el mas corto.
        if self._ahora:
            mensajes.append({"role": "system", "content": self._ahora})
        mensajes.extend(historial)
        return mensajes

    def _recortar(self, historial, chat_id=None):
        """Deja solo los ultimos turnos EN EL PROMPT, pero no tira nada.

        Sin resumir ni compactar: compactar automaticamente es de las cosas
        que no queremos. Lo que sale de la ventana se archiva tal cual.

        Hasta el 26/08 esto hacia `del historial[:sobran]` y la conversacion
        vieja desaparecia del disco para siempre. Ninguna herramienta podia
        volver sobre ella, y cuando el pregunto "cuantas veces me has dicho X"
        no habia donde mirar: hilos.json solo tenia la ventana. OpenClaw
        guardaba la conversacion entera y aqui se habia perdido esa propiedad.

        El corte se desplaza hasta un punto valido: si dejara un resultado de
        herramienta sin su llamada (o al reves), el proveedor rechaza la
        peticion entera.
        """
        sobran = self._sobrantes(historial)
        if sobran <= 0:
            return
        while sobran < len(historial) and not self._corte_valido(historial, sobran):
            sobran += 1
        self._archivar(chat_id, historial[:sobran])
        del historial[:sobran]

    def _sobrantes(self, historial):
        """Cuantos mensajes del principio hay que quitar para caber."""
        if not self._max_tokens_hilo:
            return len(historial) - self._max_turnos * 2
        tope = self._max_tokens_hilo * CARACTERES_POR_TOKEN
        total = sum(_pesa(m) for m in historial)
        if total <= tope:
            return 0
        fuera = 0
        for m in historial:
            if total <= tope:
                break
            total -= _pesa(m)
            fuera += 1
        return fuera

    def rellenar_desde_archivo(self, chat_id):
        """Al arrancar, completa la ventana hacia atras con lo archivado.

        Sin esto una ventana grande solo se llenaria con conversacion futura y
        los diez dias que se importaron el 26/08 seguirian sin entrar nunca.

        El empalme se busca por contenido: se localiza en el archivo el primer
        mensaje que ya esta en la ventana y se coge todo lo anterior. Asi no se
        duplica nada aunque el archivo y la ventana se solapen.
        """
        if not self._max_tokens_hilo:
            return 0
        ruta = self._archivo / ("%s.jsonl" % chat_id)
        if not ruta.exists():
            return 0
        try:
            viejos = []
            for linea in ruta.read_text(encoding="utf-8").splitlines():
                if not linea.strip():
                    continue
                d = json.loads(linea)
                if d.get("role") in ("user", "assistant") and d.get("content"):
                    viejos.append({"role": d["role"], "content": d["content"]})
        except (OSError, json.JSONDecodeError) as err:
            self._log("archivo: no se pudo leer (%s)" % err)
            return 0
        if not viejos:
            return 0

        actual = self._historial.setdefault(chat_id, [])
        corte = len(viejos)
        if actual:
            primero = (actual[0].get("role"), actual[0].get("content"))
            for i in range(len(viejos) - 1, -1, -1):
                if (viejos[i]["role"], viejos[i]["content"]) == primero:
                    corte = i
                    break
        candidatos = viejos[:corte]

        # Se meten desde el mas reciente hacia atras hasta llenar el techo.
        tope = self._max_tokens_hilo * CARACTERES_POR_TOKEN
        usado = sum(_pesa(m) for m in actual)
        entran = []
        for m in reversed(candidatos):
            c = _pesa(m)
            if usado + c > tope:
                break
            entran.append(m)
            usado += c
        entran.reverse()
        if entran:
            actual[:0] = entran
            self._log("archivo: %d mensaje(s) viejos devueltos a la ventana "
                      "(%d en total, ~%dk tokens)"
                      % (len(entran), len(actual), usado / CARACTERES_POR_TOKEN / 1000))
        return len(entran)

    def _archivar(self, chat_id, mensajes):
        """Guarda lo que sale de la ventana. Uno por linea, para siempre.

        Es un anexo: nunca se reescribe ni se reordena, asi que no puede
        corromper lo que ya habia. Y NO vuelve al prompt solo: la ventana
        sigue siendo la que es. Esto es el archivo, no la memoria.
        """
        if not mensajes:
            return
        try:
            self._archivo.mkdir(parents=True, exist_ok=True)
            ruta = self._archivo / ("%s.jsonl" % (chat_id or "sin-chat"))
            sello = datetime.now().isoformat(timespec="seconds")
            with ruta.open("a", encoding="utf-8") as f:
                for m in mensajes:
                    f.write(json.dumps({"archivado": sello, **m},
                                       ensure_ascii=False) + "\n")
            self._log("archivo: %d mensaje(s) fuera de la ventana, guardados"
                      % len(mensajes))
        except OSError as err:
            self._log("archivo: no se pudo guardar (%s)" % err)

    @staticmethod
    def _corte_valido(historial, i):
        """True si el historial puede empezar en el indice i."""
        m = historial[i]
        if m.get("role") == "tool":
            return False                  # resultado sin su llamada delante
        # Una llamada debe conservar sus resultados, que van justo detras.
        return True
