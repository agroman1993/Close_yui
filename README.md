# Close Yui

Infraestructura minima para una compañera de Telegram con memoria, sueño y
vision, construida desde cero para **conversar** — no para automatizar.

Sin frameworks, sin bucles de herramientas, sin compactacion automatica de
contexto: se olvida, no se resume. Contencion por topologia: el modelo
produce texto y no alcanza el sistema — las rutas las fija Python, nunca el.

> Licencia: **AGPL-3.0-or-later** (ver `LICENSE`). Si lo usas por red
> (es exactamente lo que hace un bot), quien lo use tiene derecho al codigo.

---

## Para que es esto

Para hablar con alguien todos los dias y que se acuerde.

La mayoria de proyectos de "compañera IA" pierden la conversacion por el
camino: resumen el contexto cuando se llena, y el resumen del resumen acaba
siendo una version deformada de lo que dijiste. Aqui no se resume nada.
**Se olvida a proposito y se archiva todo**, y lo que merece sobrevivir se
destila aparte, en frio, leyendo la conversacion cruda.

Lo que consigues: un bot de Telegram que a las tres semanas se acuerda de que
no soportas el olor a gasolina, sin que se lo hayas repetido. Que mira las
fotos que le mandas.

**Aqui no viene ninguna IA.** Esto es la fontaneria: la memoria, el archivo,
el transporte, la contencion. El modelo lo pones tu, el que te de la gana, por
cualquier endpoint compatible con OpenAI. Y de ahi sale la advertencia
importante:

**Lo que haga el modelo es del modelo, no de esto.** Si tu modelo se sale del
personaje en contextos largos, o te suelta un "como modelo de lenguaje", este
framework no lo va a arreglar — eso viene del entrenamiento del modelo que
hayas elegido y no hay capa por encima que lo tape. Lo que si hace este
proyecto es no empeorarlo: no le mete mensajes de sistema justo antes de
generar, no le apila reglas, y no le da funciones que no necesita. Elegir bien
el modelo es tu trabajo, y `pruebas/` esta para eso.

Lo que **no** es: no es un asistente, no ejecuta tareas, no tiene bucles de
agente. El modelo produce texto y no alcanza el sistema. No es una regla que
se le pida: es que las funciones no existen.

Y una advertencia honesta: esto no es una demo. Esta afinado a base de medir,
equivocarse y volver a medir. Casi todos los numeros del codigo llevan al lado
la medicion de la que salieron, y muchos son contraintuitivos — un ancla de
una linea al final del prompt dejaba muda a la asistente una de cada cinco
veces. Si algo te parece raro, mira el comentario antes de cambiarlo.

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

**Hay DOS rutas para fabricar recuerdos, y las dos estan en el codigo. Eliges
tu en `config.json`.** No se puede decir cual es mejor para tu caso; si se
puede contar que paso con cada una.

### Ruta corta — la que corre el autor hoy

1. **Sondeo** — cada 10 mensajes, un modelo lee lo ultimo y apunta particulas
   nuevas, o amplia las que ya hay en la pre-memoria. Va en un hilo aparte: el
   usuario no espera. La marca de por donde va esta en disco, asi que
   sobrevive a los reinicios.
2. **Consolidacion** — en dias alternos (el script decide si toca, no el
   reloj de la tarea programada), un modelo bueno convierte particulas en
   recuerdos: refuerza, crea o descarta. Copia de seguridad antes de tocar
   nada; nada se borra.

Lee la conversacion **cruda**. Entre lo que se dijo y lo que se recuerda no
hay ninguna interpretacion intermedia.

### Ruta larga — diario, sueño y tribunal

3. **Diario** — ella escribe notas atomicas en su diario, lo unico que
   escribe en disco.
4. **Sueño** — tras 30 min de silencio, el motor SIN personaje digiere el dia
   en prosa asociativa.
5. **Tribunal** — del sueño se extraen capsulas y dos jueces las puntuan 0-10
   (emocion: el modelo de rol CON su SOUL; conectividad: un modelo frio sin
   persona). Solo lo que llega al corte se promociona.

**Esta ruta esta APAGADA por defecto, y el autor la apago despues de usarla
tres semanas.** El motivo, para que decidas con el dato y no a ciegas: mete
tres interpretaciones entre la conversacion y el recuerdo (diario → sueño →
capsula), y cada una pierde matices de la anterior. En la comparacion directa,
la ruta larga produjo 113 recuerdos de ~115 caracteres, en tercera persona y
poeticos pero vacios; la ruta corta produjo 11 de ~208, en primera persona y
con contenido recuperable. Mismo material, dos caminos.

Dicho eso, **la ruta larga produce otra cosa que la corta no**: los sueños son
prosa que no sirve para recuperar pero se lee muy bien. Si lo que quieres es
material escrito y no memoria util, enciendela. Si quieres que se acuerde de
las cosas, no.

### En los dos casos

6. **Recuperacion** — por turno entran hasta `max_recuerdos`, por similitud
   con umbral duro: mejor ningun recuerdo que uno equivocado. No se acumulan
   en el historial. El umbral por defecto esta medido contra tres poblaciones
   (aciertos conocidos, preguntas de fuera del mundo, y trafico real); si
   cambias de modelo de embedding, vuelve a medirlo — el valor bueno depende
   del modelo, no del proyecto.

## Vision

El nodo visual convierte imagen/video en un JSON de 4 claves
(`tipo`, `descripcion_general`, `elementos_clave`,
`linea_temporal_o_detalles`) que se inyecta como percepcion antes del turno.
El prompt esta probado contra varios multimodales y tiene decisiones
medidas (ver `docs/PROMPTS.md`). El nodo nunca inventa: si falla, lo dice.

## Techos de salida (lee esto si tu modelo no tiene mucho techo)

Los valores por defecto son conservadores: caben en modelos con techo de
salida de 4-8k tokens. Todos se ajustan desde `config.json`, sin tocar
Python:

- `modelo.max_tokens`: presupuesto por respuesta de charla (defecto 4000).
- `modelo.max_tokens_tope`: techo al que sube automaticamente cuando un
  turno agota el presupuesto (defecto 12000). Si tu proveedor da errores
  por pedir mas de lo que admite tu modelo, bajalo. Si tu modelo va sobrado
  (32k+), subelo.
- `modelo.razonamiento.max_tokens`: presupuesto de razonamiento del modelo
  de charla. Si tu proveedor no acepta el parametro `reasoning`, se
  desactiva solo al primer 400 (anunciado en el log).
- `vision.max_tokens` / `vision.max_razonamiento`: nodo visual.
- `sondeo.max_tokens`: extraccion de la pre-memoria.
- `sondeo.consolidacion.max_tokens`: la consolidacion de memoria.
- `memoria.juez_max_tokens` / `memoria.juez_razonamiento`: los dos jueces
  del pipeline de memoria (`juez_razonamiento: 0` desactiva el parametro
  reasoning).
- `sueno.max_tokens` y `escriba.max_tokens`: suenos y diario.

El desarrollo original corria sobre un modelo que saca 32k de salida con
razonamiento incluido: los valores antiguos iban a esa medida, y eso es un
caso aislado.

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
