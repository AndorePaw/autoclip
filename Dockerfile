FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-liberation fontconfig \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000 \
    WHISPER_MODEL=base \
    DATA_DIR=/tmp/autoclip \
    HF_HOME=/app/.cache/huggingface

# Unduh model whisper saat build supaya job pertama tidak menunggu unduhan.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', compute_type='int8')" || true

# 1 worker: job disimpan di memori proses. Thread dipakai untuk polling status.
CMD gunicorn -w 1 --threads 8 --timeout 900 -b 0.0.0.0:${PORT} app:app
