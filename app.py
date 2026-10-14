"""Auto Clipper (web): upload video atau paste link -> klip 9:16 + subtitle."""
import hmac
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import autoclip

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024

DATA = Path(os.getenv("DATA_DIR", "/tmp/autoclip"))
DATA.mkdir(parents=True, exist_ok=True)
PASSWORD = os.getenv("APP_PASSWORD", "")
WHISPER = os.getenv("WHISPER_MODEL", "base")
TTL = int(os.getenv("JOB_TTL_HOURS", "6")) * 3600
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}

JOBS = {}
RUN_LOCK = threading.Lock()  # satu job diproses sekali waktu (transkrip + render berat)


def authed():
    if not PASSWORD:
        return True
    given = request.headers.get("X-App-Password") or request.args.get("p") or ""
    return hmac.compare_digest(given, PASSWORD)


@app.before_request
def guard():
    p = request.path
    if (p.startswith("/api/") and p != "/api/config") or p.startswith("/files/"):
        if not authed():
            return jsonify(error="Password salah atau belum diisi."), 401


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/health")
def health():
    return "ok"


@app.get("/api/config")
def config():
    return jsonify(needs_password=bool(PASSWORD), max_upload_mb=app.config["MAX_CONTENT_LENGTH"] // 1024 // 1024)


def clamp(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def friendly_error(exc, from_url):
    if isinstance(exc, subprocess.CalledProcessError):
        cmd = exc.cmd[0] if exc.cmd else ""
        if cmd == "yt-dlp":
            return (
                "Gagal mengunduh video dari link. YouTube sering memblokir server "
                "cloud; coba upload file videonya langsung."
            )
        return "Gagal memproses video (ffmpeg). Pastikan file video-nya valid."
    if isinstance(exc, SystemExit):
        return str(exc.code or "Proses dihentikan.")
    return f"Terjadi kesalahan: {exc}"


def worker(job_id, source, from_url, opts):
    job = JOBS[job_id]
    wd = DATA / job_id
    try:
        with RUN_LOCK:
            job["stage"] = "Mengambil video"
            video = autoclip.get_video(source, wd)
            job["stage"] = "Membuat transkrip (bisa beberapa menit)"
            sents, words = autoclip.transcribe(video, wd, WHISPER, opts["lang"])
            if not sents:
                raise RuntimeError("Tidak ada ucapan terdeteksi di video ini.")
            job["stage"] = "Memilih bagian terbaik"
            clips = autoclip.pick_clips(sents, opts["num"], opts["min"], opts["max"])
            if not clips:
                raise RuntimeError("Tidak ada bagian yang cocok. Coba longgarkan durasi.")
            result = []
            for i, clip in enumerate(clips, 1):
                job["stage"] = f"Render klip {i} dari {len(clips)}"
                path = autoclip.render(video, clip, words, wd, i, "Arial", 78)
                result.append(
                    {
                        "file": path.name,
                        "title": clip.get("title", ""),
                        "hashtags": clip.get("hashtags", []),
                        "duration": round(clip["end"] - clip["start"]),
                    }
                )
            # hemat disk: video sumber tidak diperlukan lagi
            for f in wd.glob("source.*"):
                f.unlink(missing_ok=True)
            for f in wd.glob("upload.*"):
                f.unlink(missing_ok=True)
            job["clips"] = result
            job["status"] = "done"
            job["stage"] = "Selesai"
    except BaseException as exc:  # termasuk SystemExit dari autoclip
        job["status"] = "error"
        job["error"] = friendly_error(exc, from_url)


@app.post("/api/jobs")
def create_job():
    upload = request.files.get("file")
    url = (request.form.get("url") or "").strip()
    opts = {
        "num": clamp(request.form.get("num"), 1, 6, 3),
        "min": clamp(request.form.get("min"), 10, 90, 20),
        "max": clamp(request.form.get("max"), 15, 120, 45),
        "lang": (request.form.get("lang") or "").strip() or None,
    }
    if opts["max"] < opts["min"]:
        opts["max"] = opts["min"] + 10
    if opts["lang"] and not re.fullmatch(r"[a-z]{2,3}", opts["lang"]):
        opts["lang"] = None

    job_id = uuid.uuid4().hex[:12]
    wd = DATA / job_id
    wd.mkdir(parents=True)

    if upload and upload.filename:
        ext = Path(upload.filename).suffix.lower()
        if ext not in VIDEO_EXT:
            shutil.rmtree(wd, ignore_errors=True)
            return jsonify(error="Format harus video: " + ", ".join(sorted(VIDEO_EXT))), 400
        src = wd / f"upload{ext}"
        upload.save(src)
        source, from_url = str(src), False
    elif url.lower().startswith(("http://", "https://")):
        source, from_url = url, True
    else:
        shutil.rmtree(wd, ignore_errors=True)
        return jsonify(error="Isi link video (http/https) atau pilih file video."), 400

    JOBS[job_id] = {"status": "running", "stage": "Menunggu antrean", "created": time.time()}
    threading.Thread(target=worker, args=(job_id, source, from_url, opts), daemon=True).start()
    return jsonify(id=job_id)


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Job tidak ditemukan atau sudah dihapus."), 404
    return jsonify({k: v for k, v in job.items() if k != "created"})


@app.get("/files/<job_id>/<name>")
def get_file(job_id, name):
    if not re.fullmatch(r"[0-9a-f]{12}", job_id) or not re.fullmatch(r"clip_\d{2}\.mp4", name):
        return jsonify(error="Tidak ditemukan"), 404
    return send_from_directory(DATA / job_id, name, as_attachment=request.args.get("dl") == "1")


def cleanup_loop():
    while True:
        time.sleep(600)
        now = time.time()
        for job_id, job in list(JOBS.items()):
            if now - job["created"] > TTL and job["status"] != "running":
                shutil.rmtree(DATA / job_id, ignore_errors=True)
                JOBS.pop(job_id, None)
        for d in DATA.iterdir():  # sisa job dari restart
            if d.is_dir() and d.name not in JOBS and now - d.stat().st_mtime > TTL:
                shutil.rmtree(d, ignore_errors=True)


threading.Thread(target=cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
