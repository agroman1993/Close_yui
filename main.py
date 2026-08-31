# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 los autores de Close Yui
"""Arranque: conecta el transporte de Telegram con el enrutador.

Este fichero solo pega las piezas. Toda la logica de Telegram vive en
nucleo/telegram.py y toda la de "que se contesta" en nucleo/enrutador.py.
"""

import json
import re
import sys
import time
import urllib.error
from datetime import datetime
from pathlib import Path

from nucleo.enrutador import Enrutador
from nucleo.sondeo import Sondeo
from nucleo.sueno import Sueno
from nucleo.telegram import ErrorTelegram, Telegram

RAIZ = Path(__file__).parent
CONFIG = RAIZ / "config.json"

# Si la red falla, se espera un poco antes de reintentar y se va subiendo el
# tiempo para no castigar a la API si el corte es largo.
ESPERA_MIN = 2
ESPERA_MAX = 60

# Si el proveedor se cae, el dueño puede tardar horas en escribir y no enterarse.
# Tras unos fallos seguidos se le avisa — texto del sistema, sin personaje —
# y cuando vuelve a funcionar se le confirma que todo esta en orden.
FALLOS_PARA_AVISO = 2
AVISO_CAIDA = ("[Sistema] El proveedor del modelo se ha caido: las respuestas van "
               "a fallar. El bot no esta roto, sigue funcionando y lo intenta en "
               "cada mensaje; en cuanto vuelva, contesta.")
AVISO_VUELTA = "[Sistema] El proveedor del modelo ha vuelto. Todo en orden."

# Efimero: se manda cuando el proveedor lleva ya unos cuantos fallos seguidos
# y se BORRA en cuanto hay respuesta. No pide nada, que es el punto: el
# mensaje de el dueño sigue sin confirmar en Telegram y el codigo insiste solo.
AVISO_ESPERA = ("[Sistema] El proveedor esta fallando; sigo insistiendo. "
                "No hace falta que repitas nada.")
aviso_estado = {"fallos": 0, "dicho": False}


def log(*a):
    try:
        print("[%s]" % datetime.now().strftime("%H:%M:%S"), *a, flush=True)
    except Exception:                          # noqa: BLE001
        pass   # el log jamas puede tumbar el bot


def blindar_salida():
    """UTF-8 en stdout/stderr del proceso.

    cp1252 mato el bot el 17/08: un caracter fuera de mapa en el log lanzo
    UnicodeEncodeError fuera de cualquier except del bucle principal y el
    proceso murio entero. Con errors='replace', si algo no codifica se
    sustituye; no revienta.
    """
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                      # noqa: BLE001
            pass


CONSERVAR = 7          # copias diarias que se guardan de cada fichero


def respaldo_diario():
    """Copia de seguridad de los datos irremplazables, una vez al dia.

    memoria.jsonl es su memoria consolidada; el diario y los suenos son su
    vida interior. Si uno de estos ficheros se corrompe, sin copia se pierde
    para siempre. Una copia por dia y fichero; se conservan los ultimos 7.
    """
    destino = RAIZ / "copias"
    try:
        destino.mkdir(exist_ok=True)
    except OSError as err:
        log("AVISO: no se pudo crear copias/ (%s)" % err)
        return
    hoy = datetime.now().strftime("%Y-%m-%d")
    for nombre in ("memoria.jsonl", "diario_notas.md", "suenos.md"):
        origen = RAIZ / nombre
        if not origen.exists():
            continue
        objetivo = destino / ("%s.%s" % (hoy, nombre))
        if objetivo.exists():
            continue                            # ya hay copia de hoy
        try:
            objetivo.write_bytes(origen.read_bytes())
            log("copia de seguridad: %s" % objetivo.name)
        except OSError as err:
            log("AVISO: no se pudo copiar %s (%s)" % (nombre, err))

        # La poda que esta linea de arriba prometia y nunca hacia. Sin ella
        # esto crecia unos 12 MB al dia para siempre.
        viejas = sorted(destino.glob("*.%s" % nombre), reverse=True)[CONSERVAR:]
        for v in viejas:
            try:
                v.unlink()
                log("copia vieja retirada: %s" % v.name)
            except OSError:
                pass
    # Retencion: por fichero base, conservar solo los 7 dias mas recientes.
    try:
        por_base = {}
        for f in destino.iterdir():
            if len(f.name) < 12 or f.name[10] != ".":
                continue                        # no tiene formato YYYY-MM-DD.<base>
            por_base.setdefault(f.name[11:], []).append(f)
        for ficheros in por_base.values():
            ficheros.sort(key=lambda f: f.name, reverse=True)
            for f in ficheros[7:]:
                f.unlink()
    except OSError as err:
        log("AVISO: limpieza de copias: %s" % err)


def cargar_config():
    if not CONFIG.exists():
        sys.exit("Falta config.json. Copia config.ejemplo.json y pon tu token.")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    token = (cfg.get("token") or "").strip()
    if not token or token.startswith("PON_AQUI"):
        sys.exit("config.json no tiene token. Pidele uno a @BotFather en Telegram.")

    # La personalidad vive en ficheros propios: es texto largo, se edita a
    # menudo y no tiene por que estar escapado dentro de un JSON.
    cfg["sistema"] = cargar_prompt(cfg.get("sistema_ficheros")
                                   or ([cfg["sistema_fichero"]] if cfg.get("sistema_fichero") else []))
    return cfg


# Los comentarios HTML son para ti, no para ella: se quitan antes de enviar.
# Asi una plantilla sin tocar no aporta nada al prompt.
COMENTARIO = re.compile(r"<!--.*?-->", re.S)


def cargar_prompt(ficheros):
    """Concatena las fuentes del system prompt en el orden dado.

    Cada fichero es opcional: si falta, esta vacio o no se puede leer, se salta
    y se sigue con los demas. Que falte IDENTITY.md o USER.md no debe impedir
    que arranque con su SOUL.
    """
    partes = []
    for nombre in ficheros:
        ruta = Path(nombre)
        if not ruta.is_absolute():
            ruta = RAIZ / ruta
        try:
            crudo = ruta.read_text(encoding="utf-8")
        except FileNotFoundError:
            log("prompt: %s no existe, se omite" % ruta.name)
            continue
        except OSError as err:
            log("prompt: no se pudo leer %s (%s), se omite" % (ruta.name, err))
            continue
        texto = COMENTARIO.sub("", crudo).strip()
        if not texto:
            log("prompt: %s esta vacio, se omite" % ruta.name)
            continue
        log("prompt: %s (%d caracteres)" % (ruta.name, len(texto)))
        partes.append(texto)
    if not partes:
        log("AVISO: no se cargo ninguna fuente de personalidad")
    return "\n\n".join(partes)


# El texto entre el comando y la primera linea en blanco es para Muse; lo que
# venga despues es para ella. La frontera es la linea en blanco porque en
# Telegram el Enter no manda, solo salta de linea: se escribe sin pelear.
MUSE = re.compile(r"^/muse\b[ \t]*", re.I)


def atender_muse(tg, enrutador, msg):
    """/muse <que mirar>  [linea en blanco]  [lo que le dices a ella]"""
    cuerpo = MUSE.sub("", (msg["texto"] or "").strip(), count=1)
    trozos = re.split(r"\n[ \t]*\n", cuerpo, maxsplit=1)
    enfoque = trozos[0].strip()
    suyo = trozos[1].strip() if len(trozos) > 1 else ""
    if not enfoque:
        tg.enviar(msg["chat_id"], "Dime qué quieres que mire. Ejemplo: "
                                  "/muse haz zoom a la flor del pelo")
        return

    tg.escribiendo(msg["chat_id"])
    percepcion, resumen, aviso = enrutador.enfocar(
        msg["chat_id"], enfoque,
        datos=msg.get("datos"), mime=msg.get("mime"), tipo=msg.get("tipo"))
    if aviso:
        tg.enviar(msg["chat_id"], aviso)
        log("-> muse: %s" % aviso)
        return

    if not suyo:
        # Sin frase para ella no se la molesta: un turno con percepcion y sin
        # nada que contestar es un turno cojo. Esto es el modo de mirar tu.
        tg.enviar(msg["chat_id"], resumen[:3500])
        log("-> muse: %d caracteres, solo para el" % len(resumen))
        return

    # Con frase, entra por su percepcion como si acabara de fijarse mejor.
    msg["texto"] = suyo
    msg["tipo"] = "texto"
    msg["percepcion"] = percepcion
    return "seguir"


def atender(tg, enrutador, cfg, msg):
    """Procesa un mensaje ya recibido."""
    dueno = cfg.get("dueno_id")
    # Un bot de Telegram es publico: cualquiera que sepa su nombre puede
    # escribirle. Si hay dueno configurado, se ignora al resto.
    if dueno and msg["usuario_id"] != dueno:
        log("ignorado (no es el dueno): id=%s nombre=%s" % (msg["usuario_id"], msg["nombre"]))
        return

    log("<- %s (%s): %s" % (msg["nombre"], msg["tipo"], (msg["texto"] or "")[:60]))

    orden = (msg["texto"] or "").strip().lower()
    if orden in ("/nuevo", "/new", "/reset"):
        enrutador.olvidar(msg["chat_id"])
        tg.enviar(msg["chat_id"], "Hilo olvidado. Empezamos de cero.")
        log("-> hilo reiniciado")
        return


    if enrutador.sueno:
        enrutador.sueno.toca()          # hay conversacion: no toca dormir

    tg.escribiendo(msg["chat_id"])

    # La descarga la hace quien conoce el transporte. El enrutador recibe los
    # bytes ya resueltos y no se entera de que existe Telegram.
    if msg.get("medio") and msg["medio"].get("file_id"):
        try:
            datos, mime = tg.descargar(msg["medio"]["file_id"])
            msg["datos"] = datos
            msg["mime"] = msg["medio"].get("mime") or mime
            log("   descargado: %.1f KB (%s)" % (len(datos) / 1024, msg["mime"]))
        except (ErrorTelegram, urllib.error.URLError, OSError, TimeoutError) as err:
            msg["error_medio"] = str(err)
            log("   fallo al descargar:", err)

    # El comando va AQUI, con la descarga ya hecha: si la foto viene con el
    # propio mensaje hay que mirar ESA. El 26/08 estaba antes de descargar y
    # /muse se quedaba anclado en la foto anterior.
    if MUSE.match(orden):
        if atender_muse(tg, enrutador, msg) != "seguir":
            return

    inicio = time.time()

    # Aviso efimero. Si el proveedor falla varias veces seguidas, el enrutador
    # llama aqui y el dueño ve que la cosa va para largo. Ese mensaje se BORRA de
    # Telegram en cuanto llega la respuesta de verdad: como si el error no
    # hubiera existido. La capa de transparencia esta bien mientras dura la
    # espera; una vez resuelta, sobra y solo ensucia el chat.
    avisos = []

    def avisar_de_la_espera():
        if avisos:
            return                                    # uno y no mas
        avisos.extend(tg.enviar(msg["chat_id"], AVISO_ESPERA) or [])
        log("aviso de espera enviado (se borrara al responder)")

    enrutador.avisar = avisar_de_la_espera
    try:
        respuesta = enrutador.responder(msg)
    except Exception as err:                      # noqa: BLE001
        log("ERROR en el enrutador:", repr(err))
        respuesta = "Se me ha roto algo al pensar la respuesta. Mira el log."

    # Cuenta de fallos del proveedor: tras FALLOS_PARA_AVISO seguidos se avisa
    # al operador; un exito tras el aviso confirma que volvio.
    if respuesta and respuesta.startswith("No he podido responder"):
        aviso_estado["fallos"] += 1
        if aviso_estado["fallos"] >= FALLOS_PARA_AVISO and not aviso_estado["dicho"]:
            aviso_estado["dicho"] = True
            try:
                tg.enviar(msg["chat_id"], AVISO_CAIDA)
                log("aviso de caida enviado al operador")
            except (ErrorTelegram, urllib.error.URLError, OSError) as err:
                log("no se pudo avisar de la caida:", err)
    elif aviso_estado["dicho"]:
        aviso_estado["fallos"] = 0
        aviso_estado["dicho"] = False
        try:
            tg.enviar(msg["chat_id"], AVISO_VUELTA)
            log("aviso de vuelta enviado al operador")
        except (ErrorTelegram, urllib.error.URLError, OSError) as err:
            log("no se pudo avisar de la vuelta:", err)
    else:
        aviso_estado["fallos"] = 0

    if respuesta is None:
        log("-> (sin respuesta)")
        return
    tg.enviar(msg["chat_id"], respuesta)
    # Ya hay respuesta: el aviso de espera se retira y no queda rastro del
    # bache. Va DESPUES de enviar para que el chat nunca se quede un instante
    # sin nada, y antes de confirmar porque es parte de resolver el turno.
    if avisos:
        tg.borrar(msg["chat_id"], avisos)
        log("aviso de espera retirado")
    # Resuelto: ya se puede dar por entregado. Si se hubiera muerto o rendido
    # antes de esta linea, Telegram lo vuelve a traer.
    tg.confirmar()
    uso = getattr(enrutador.modelo, "ultimo_uso", None) or {}
    detalle = ""
    if uso.get("salida") is not None:
        detalle = ", %s tokens de salida, %s razonando, %s de contexto" % (
            uso.get("salida"), uso.get("razonando"), uso.get("entrada"))
    log("-> enviado (%d caracteres, %.1fs%s)"
        % (len(respuesta), time.time() - inicio, detalle))

    # El sondeo va AQUI, con el turno ya enviado y confirmado: lo que hace es
    # tomar notas por detras y el dueño no tiene por que esperarlo. Cada diez
    # mensajes se va en un hilo aparte; las otras nueve veces solo suma uno a
    # un contador. Si revienta, se anota y la conversacion sigue.
    sondeo = getattr(enrutador, "sondeo", None)
    if sondeo:
        sondeo.turno(enrutador.hilo(msg["chat_id"]))


def main():
    blindar_salida()
    respaldo_diario()
    cfg = cargar_config()
    tg = Telegram(cfg["token"], log=log)
    enrutador = Enrutador(cfg, log=log)

    # La ventana se rellena hacia atras con lo archivado. Sin esto, una ventana
    # grande solo se llenaria con conversacion futura y los diez dias que se
    # importaron el 26/08 no entrarian nunca. No duplica: empalma por contenido.
    chats = set(enrutador.chats)
    if cfg.get("dueno_id"):
        chats.add(cfg["dueno_id"])
    for chat in chats:
        try:
            enrutador.rellenar_desde_archivo(chat)
        except Exception as err:                  # noqa: BLE001
            log("archivo: no se pudo rellenar el chat %s (%s)" % (chat, err))

    # Se comprueba el token antes de nada: si esta mal, mejor decirlo aqui
    # que dejar el bot en un bucle de reintentos sin explicar por que.
    try:
        yo = tg.quien_soy()
    except ErrorTelegram as err:
        sys.exit("Telegram rechaza el token (%s).\n"
                 "Recupera el bueno en @BotFather con /mybots -> tu bot -> API Token." % err)
    except (urllib.error.URLError, OSError, TimeoutError) as err:
        sys.exit("No hay conexion con Telegram: %s" % err)
    log("conectado como @%s (%s)" % (yo.get("username"), yo.get("first_name")))
    log("modelo: %s" % cfg["modelo"]["id"])
    if cfg.get("dueno_id"):
        log("solo respondera al dueno id=%s" % cfg["dueno_id"])
    else:
        log("AVISO: sin dueno_id, respondera a cualquiera que le escriba")
    if cfg.get("sueno") and enrutador.diario:
        enrutador.sueno = Sueno(cfg, enrutador.diario, log=log, memoria=enrutador.memoria)
        enrutador.sueno.arrancar()

    # El sondeo cada N mensajes. No arranca ningun hilo aqui: solo cuenta, y
    # cuando llega a N se va a mirar la conversacion en segundo plano.
    if (cfg.get("sondeo") or {}).get("activo", True):
        enrutador.sondeo = Sondeo(cfg, log=log)
        log("sondeo: cada %d mensajes -> %s"
            % (enrutador.sondeo._cada, (cfg["sondeo"]).get("pre_memoria",
                                                           "pre-memoria.jsonl")))

    log("esperando mensajes... (Ctrl+C para parar)")

    espera = ESPERA_MIN
    while True:
        try:
            for msg in tg.recibir():
                try:
                    atender(tg, enrutador, cfg, msg)
                except Exception as err:          # noqa: BLE001
                    # Ningun mensaje individual puede tumbar el bot: se
                    # registra el fallo y se sigue con el siguiente.
                    log("ERROR procesando un mensaje (%s); se continua" % repr(err))
            espera = ESPERA_MIN                   # ciclo limpio: se reinicia
        except KeyboardInterrupt:
            log("parado a mano")
            return
        except (urllib.error.URLError, ErrorTelegram, OSError, TimeoutError) as err:
            log("fallo de red o API (%s). reintento en %ss" % (err, espera))
            time.sleep(espera)
            espera = min(espera * 2, ESPERA_MAX)
        except Exception as err:                  # noqa: BLE001
            # Lo que no sea red tampoco mata el bot: se apunta, se espera y se
            # persiste. El backoff evita bucle loco si el error es sistematico.
            log("ERROR inesperado (%s); reintento en %ss" % (repr(err), espera))
            time.sleep(espera)
            espera = min(espera * 2, ESPERA_MAX)


if __name__ == "__main__":
    main()
