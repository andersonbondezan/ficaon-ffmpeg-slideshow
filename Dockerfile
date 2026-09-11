FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py .
COPY backgrounds/ backgrounds/
RUN pip install --no-cache-dir flask gunicorn yt-dlp edge-tts==7.2.8

EXPOSE 8080
# 1 worker, timeout bem alto (/longform agora gera 5 historias longas por video,
# 800+ palavras cada - TTS + render de ~25min de video pode passar de 20min)
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "1", "-t", "2700", "app:app"]
