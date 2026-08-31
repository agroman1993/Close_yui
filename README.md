# Close Yui

Infraestructura minima para una compañera de Telegram con memoria, sueño y
vision, construida desde cero para **conversar** — no para automatizar.

Sin frameworks, sin bucles de herramientas, sin compactacion automatica de
contexto: se olvida, no se resume. Contencion por topologia: el modelo
produce texto y no alcanza el sistema — las rutas las fija Python, nunca el.

> Licencia: **AGPL-3.0-or-later** (ver `LICENSE`). Si lo usas por red
> (es exactamente lo que hace un bot), quien lo use tiene derecho al codigo.

---

## Arranque rapido (4 pasos)

1. Pide un bot a [@BotFather](https://t.me/BotFather) (`/newbot`) y tu id a
   [@userinfobot](https://t.me/userinfobot). Bot **nuevo**: dos programas
   haciendo polling del mismo se pelean.
2. Consigue una api_key de cualquier proveedor compatible con OpenAI
   (OpenRouter, OpenAI, Ollama local, vLLM...).
3. Instala:
   - **Windows:** doble click en `instalar.bat`
   - **Ubuntu:** `./instalar.sh`
   - **Docker:** copia `config.ejemplo.json` a `config.json`, rellena, y
     `docker compose up -d`
4. Arranca: `python main.py` (o `.venv/bin/python main.py` en Ubuntu).

El asistente del instalador te pregunta solo token, id, proveedor y modelo,
y escribe `config.json` el solo.

## Que necesita

- Python 3.10+ (probado con 3.14)
- Una unica dependencia: **Pillow** (resto: biblioteca estandar)
- Un proveedor con API compatible OpenAI: chat, embeddings y (opcional) un
  modelo multimodal para la vision

## Agnostico de proveedor

Todo lo que llama a un modelo va por endpoints compatibles con OpenAI
(`/chat/completions`, `/embeddings`), con `base_url` configurable por bloque:

| Bloque | Para que | Ejemplos de base_url |
|---|---|---|
| `modelo` | la charla | `https://openrouter.ai/api/v1`, `http://localhost:11434/v1` (Ollama) |
| `vision` | extraer JSON de imagen/video | cualquier multimodal |
| `memoria.modelo_embedding` | vectorizar recuerdos | cualquier endpoint `/embeddings` |
| `memoria.modelo_frio` | juez sin persona | un modelo barato y analitico |
| `sondeo.modelo` | tomar notas cada 10 mensajes | un modelo bueno (ver `docs/PROMPTS.md`) |

`persona` en config.json decide el personaje: `nombre` (como se llama ella)
y `vocativo` (como le llama ella a su dueño). Los prompts del pipeline llevan
marcas `__NOMBRE__`/`__VOCATIVO__` y se resuelven al arrancar — puedes
cambiar el personaje sin tocar codigo.

## Estructura

```
main.py               arranque; solo pega las piezas
nucleo/telegram.py    transporte: recibir y enviar por Telegram
nucleo/enrutador.py   hilo de conversacion y decision  <- LA COSTURA
nucleo/vision.py      nodo extractor: medio -> JSON estructurado
nucleo/diario.py      lo unico que ella escribe en disco (dueño de la ruta)
nucleo/sueno.py       digestion inconsciente del diario
nucleo/memoria.py     extraccion, tribunal de promocion y recuperacion
nucleo/sondeo.py      notas al vuelo y consolidacion de dias alternos
nucleo/persona.py     nombre y vocativo desde config
nucleo/hilos.py       el hilo reciente sobrevive a los reinicios
nucleo/modelo.py      cliente del proveedor (OpenAI-compatible)
herramientas/         mantenimiento: consolidar, limpiar, podar, revotar
pruebas/              banco aséptico para comparar modelos con datos
SOUL.md IDENTITY.md USER.md   la personalidad (plantillas; editalas)
config.ejemplo.json   plantilla de configuracion
```

`telegram.py` no sabe quien contesta; `enrutador.py` no sabe que Telegram
existe. El nodo visual entra por `enrutador.py` sin tocar el transporte.

## El pipeline de memoria (sin que ella escriba)

1. **Sondeo** — cada 10 mensajes suyos, un modelo lee lo ultimo y apunta
   particulas nuevas o amplia las de la pre-memoria. En un hilo aparte: el
   usuario no espera.
2. **Consolidacion** — en dias alternos (tarea programada; el script decide
   si toca), un modelo bueno convierte particulas en recuerdos: refuerza,
   crea o descarta. Copia de seguridad antes de tocar nada; nada se borra.
3. **Diario + sueño** — ella escribe notas atomicas en su diario; tras 30
   min de silencio el motor SIN personaje sueña el dia (prosa asociativa).
4. **Tribunal** — del sueño se extraen capsulas; dos jueces las puntuan
   0-10 (emocion: el modelo de rol CON su SOUL; conectividad: un modelo
   frio sin persona) y solo lo que llega al corte se recupera en vivo.
5. **Recuperacion** — por turno entran hasta `max_recuerdos`, por similitud
   con umbral duro: mejor ningun recuerdo que uno equivocado. No se
   acumulan en el historial.

## Vision

El nodo visual convierte imagen/video en un JSON de 4 claves
(`tipo`, `descripcion_general`, `elementos_clave`,
`linea_temporal_o_detalles`) que se inyecta como percepcion antes del turno.
El prompt esta probado contra varios multimodales y tiene decisiones
medidas (ver `docs/PROMPTS.md`). El nodo nunca inventa: si falla, lo dice.

## Servicio opcional

- **Windows:** `wscript arrancar.vbs` (sin ventana) y tareas programadas:
  `\Close Yui` al iniciar sesion y guardian cada 5 min (`guardian.vbs`),
  mas `consolidar.vbs` cada hora. Cabeceras de cada .vbs explican el porqué.
- **Ubuntu:** unidad de usuario systemd, p.ej.
  `~/.config/systemd/user/close-yui.service` con
  `ExecStart=<ruta>/.venv/bin/python <ruta>/main.py`,
  `Restart=always`, y `systemctl --user enable --now close-yui`
  (mas `loginctl enable-linger $USER` para que sobreviva al logout).
  La consolidacion: un timer de systemd que llame a
  `herramientas/consolidar_memoria.py` cada hora.

## Seguridad minima

- `dueno_id`: solo tu id responde; el resto se ignora. Un bot de Telegram
  es publico: cualquiera que sepa su nombre puede escribirle.
- `config.json` lleva el token y las claves: esta en `.gitignore`. No lo
  subas a ningun sitio.

## Probado con

El desarrollo original corre sobre un modelo de rol (charla), un
multimodal (vision), un embedding y un modelo frio (juez). Los detalles de
que funciono y que fallo con cada uno, y los prompts tal cual quedaron,
estan en `docs/PROMPTS.md` y en los comentarios del codigo — son notas de
campo, no dependencias: cambia los modelos en `config.json` y prueba con
`pruebas/`.

## Licencia

AGPL-3.0-or-later. Copyright de los autores de Close Yui. Cada fichero
lleva su cabecera SPDX.
