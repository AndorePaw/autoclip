# --- Tahap 1: bangun server PO token (bgutil) ---
FROM node:22-bookworm-slim AS pot
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /pot
# Versi harus sama dengan bgutil-ytdlp-pot-provider di requirements.txt
RUN git clone --depth 1 --branch 2.0.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git .
WORKDIR /pot/server
RUN npm ci --no-audit --no-fund && npx tsc && npm prune --omit=dev

# --- Tahap 2: aplikasi ---
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-liberation fontconfig libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

# JS runtime untuk yt-dlp (deno) dan server PO token (node)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
COPY --from=pot /usr/local/bin/node /usr/local/bin/node
COPY --from=pot /pot/server /opt/pot-server

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

CMD ["sh", "/app/start.sh"]
