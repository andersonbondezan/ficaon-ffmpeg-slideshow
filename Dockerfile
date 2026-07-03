FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py .
RUN pip install --no-cache-dir flask gunicorn

EXPOSE 8080
# 1 worker, timeout alto (geração de vídeo pode levar dezenas de segundos)
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "1", "-t", "300", "app:app"]
