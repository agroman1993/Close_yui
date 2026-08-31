# Close Yui - imagen minima.
# El bot solo necesita la biblioteca estandar + Pillow.
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Los datos (memoria.jsonl, hilos.json, diario...) se escriben en /app.
# Monta un volumen si quieres conservarlos entre reconstrucciones: ver docker-compose.yml.
CMD ["python", "main.py"]
