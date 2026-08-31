# Banco de pruebas aséptico

Llama a los modelos DIRECTAMENTE, sin Telegram y sin personaje, para
comparar salidas en crudo antes de elegir que va en `config.json`.

- `banco_visual.py` — el prompt del nodo visual contra un modelo dado.
- `comparar.py` — varios modelos multimodales contra los mismos casos.
- `banco_fusion.py` — el criterio de fusion de particulas de memoria.
- `exportar.py` — vuelca resultados a JSON para leerlos con calma.

## Que falta (a proposito)

Los medios de prueba NO se publican: son conversaciones y fotos personales.
Para usar el banco, deja aqui tus propios ficheros:

```
pruebas/medios/frame.jpg       (una imagen fija con cosas reconocibles)
pruebas/medios/clip.mp4        (un video corto con subtitulos o voz)
pruebas/medios/solo_audio.mp4  (pantalla en negro con voz: prueba de audio)
```

Y edita la lista `MODELOS` de cada script con los ids que quieras comparar.
`comparar.py` comprueba tambien que el modelo OYE: si describe el audio con
las palabras de la pista (`PISTAS_AUDIO`), oye de verdad.
