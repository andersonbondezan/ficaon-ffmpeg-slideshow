FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py .
COPY backgrounds/ backgrounds/
RUN pip install --no-cache-dir flask gunicorn yt-dlp edge-tts==6.1.19

EXPOSE 8080
# 1 worker, timeout alto (/longform gera TTS + renderiza vídeo longo, pode levar minutos)
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "1", "-t", "900", "app:app"]
