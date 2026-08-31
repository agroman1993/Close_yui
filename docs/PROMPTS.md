# Prompts probados (notas de campo)

Los prompts del pipeline se quedaron como quedaron despues de probarlos
contra varios modelos. Esto es lo que se midio y por que cada uno es asi.
Si cambias de proveedor, lee esto antes de "mejorarlos".

Todos llevan marcas `__NOMBRE__` / `__VOCATIVO__` que se resuelven desde
`config.json → persona` (ver `nucleo/persona.py`).

## 1. Nodo visual — `nucleo/vision.py` (SISTEMA + RECORDATORIO)

El mas probado de todos: es el que convierte imagen/video en un JSON de 4
claves para inyectar como percepcion.

Decisiones medidas con el banco de `pruebas/`:

- `response_format: json_object` y estructura fija de 4 claves.
- La instruccion va repetida en el turno de usuario ademas del system: con
  video, solo con system, un modelo ignoro el formato y respondio en prosa.
- `tipo` NO se le pregunta al modelo: lo fija Python. Dos modelos distintos
  etiquetaron como "video" un fotograma fijo. No preguntes al modelo lo que
  ya sabes.
- Un multimodal bueno reconocio entidades concretas (personajes de anime,
  modelos de avion, marcas de producto), transcribio la voz literalmente y
  no se invento contenido. Un modelo rapido y barato alucino una cronologia
  entera sobre una imagen fija: para este trabajo, no inventar vale mas que
  ser veloz.
- Reintentos de red (2): un corte puntual no debe dejarla ciega de una foto.

## 2. Sondeo — `nucleo/sondeo.py` (SONDEO)

Extractor de particulas cada 10 mensajes, en hilo aparte.

- Pide explicitamente la lista vacia cuando no hay nada nuevo, y la llama
  "una respuesta perfectamente buena": sin eso, los modelos rellenan.
- "Tu NO eres ella y no participas": sin esa frase, un modelo barato se
  puso a contestar como la asistente dentro de la extraccion.
- Un modelo flash barato escribia con faltas ("tieen", "peqeuño"): las
  particulas acaban siendo recuerdos literales, asi que aqui no se ahorra.

## 3. Consolidacion — `nucleo/sondeo.py` (CONSOLIDACION)

Solo tres destinos: refuerza / nuevo / paja. "La mayoria acaba en paja y
esta bien que asi sea" — sin esa normalizacion, los modelos guardan todo.

## 4. Sueño — `nucleo/sueno.py` (SISTEMA)

El motor sueña SIN el SOUL, a proposito: si digiriera su propio diario
siendo ella, consolidaria lo que encaja con su tono en vez de lo que paso.
Prosa asociativa, sin listas ni etiquetas. "Un sueño no concluye: deja poso."

## 5. Tribunal de memoria — `nucleo/jurado.py` + `nucleo/memoria.py`

Dos jueces independientes, cada uno en lo suyo:

- **Emocion**: el modelo de rol CON su SOUL ("eres tu evaluando tus propios
  recuerdos"). Es el unico trabajo para el que un modelo de personaje es el
  mejor juez.
- **Conectividad**: un modelo frio SIN persona. No juzga si algo es bonito:
  estima cuantas veces resonara ese recuerdo en conversaciones futuras.
- Notas continuas 0-10, no binario. El tribunal anterior preguntaba
  "¿merece la pena?" en binario y aprobaba el 94% de lo que llegaba.
- Cada nota se guarda con el recuerdo: el corte se puede reajustar despues
  sin volver a pagar por juzgar.

## 6. Ancla de vocativo — `nucleo/enrutador.py` (ANCLA_VOCATIVO)

La regla del vocativo va pegada al SOUL al principio del contexto, no al
final. Medido pareado: al final, respuestas sin el vocativo en la mitad de
los casos; al principio, en ninguna. Un mensaje de sistema corto y
directivo justo antes de la generacion la dejaba en monosilabos.

## 7. Presupuesto de razonamiento — `nucleo/modelo.py`

Con modelos que razonan, el razonamiento puede gastarse el presupuesto
entero de salida y dejar la respuesta vacia. Parametro `reasoning` con tope
(en config: `modelo.razonamiento.max_tokens`); si el proveedor lo rechaza
con un 400, se desactiva solo y se reintenta una vez (autosaneado).

## 8. Filtro CJK — `nucleo/enrutador.py`

Un modelo con base GLM colaba de vez en cuando palabras en chino fuera de
las citas deliberadas. Hay un filtro que detecta CJK fuera de `<cita>` y
pide traduccion. Si tu modelo no lo hace, el filtro no estorba.

## Como probar modelos nuevos

`pruebas/` es un banco aséptico: llama a los modelos directamente, sin
Telegram ni personaje. Pon tus medios de prueba, edita la lista `MODELOS` y
compara salidas en crudo antes de decidir que entra en `config.json`.
