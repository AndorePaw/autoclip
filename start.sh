#!/bin/sh
# Cookies YouTube (opsional): isi variable YT_COOKIES_B64 (cookies.txt dalam base64).
if [ -n "$YT_COOKIES_B64" ]; then
  echo "$YT_COOKIES_B64" | base64 -d > /tmp/yt-cookies.txt 2>/dev/null || rm -f /tmp/yt-cookies.txt
fi

# Server PO token (hanya localhost) untuk membantu yt-dlp di YouTube.
node /opt/pot-server/build/main.js > /tmp/pot.log 2>&1 &

# 1 worker: job disimpan di memori proses. Thread dipakai untuk polling status.
exec gunicorn -w 1 --threads 8 --timeout 900 -b 0.0.0.0:${PORT} app:app
