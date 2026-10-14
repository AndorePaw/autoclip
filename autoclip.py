#!/usr/bin/env python3
"""Auto clipper: link video (atau file lokal) -> klip vertikal 9:16 dengan subtitle.

Alur: ambil video -> transkrip (faster-whisper) -> pilih bagian terbaik
(Claude jika ANTHROPIC_API_KEY ada, kalau tidak pakai heuristik) -> potong,
crop ke 9:16, tempel subtitle (ffmpeg).

Pakai hanya untuk video milikmu atau yang pemiliknya mengizinkan.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd, cwd=None):
    subprocess.run(cmd, check=True, cwd=cwd)


# ---------------------------------------------------------------- ambil video
def get_video(source, workdir):
    p = Path(source)
    if p.exists():
        return p
    if shutil.which("yt-dlp") is None:
        sys.exit("yt-dlp belum terpasang. Jalankan: pip install yt-dlp")
    target = workdir / "source.mp4"
    if target.exists():
        return target
    run([
        "yt-dlp",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]",
        "--merge-output-format", "mp4",
        "-o", str(workdir / "source.%(ext)s"),
        source,
    ])
    return target


# ------------------------------------------------------------------ transkrip
def transcribe(video, workdir, model_size, lang):
    cache = workdir / "transcript.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return data["sentences"], data["words"]

    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, compute_type="int8")
    segments, _ = model.transcribe(
        str(video), word_timestamps=True, language=lang, vad_filter=True
    )
    sentences, words = [], []
    for seg in segments:
        sentences.append(
            {"start": seg.start, "end": seg.end, "text": seg.text.strip()}
        )
        for w in seg.words or []:
            words.append(
                {"start": w.start, "end": w.end, "word": w.word.strip()}
            )
    cache.write_text(
        json.dumps({"sentences": sentences, "words": words}, ensure_ascii=False),
        encoding="utf-8",
    )
    return sentences, words


# ------------------------------------------------------------- pilih bagian
HOOK = re.compile(
    r"\b(rahasia|ternyata|jangan|kenapa|mengapa|gimana|bagaimana|penting|gila|"
    r"serius|cara|tips|salah|bahaya|secret|why|how|never|mistake|truth|actually)\b",
    re.I,
)


def heuristic_pick(sents, n, min_len, max_len):
    cands = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            dur = sents[j]["end"] - sents[i]["start"]
            if dur > max_len:
                break
            if dur < min_len:
                continue
            text = " ".join(s["text"] for s in sents[i : j + 1])
            score = (
                len(text.split()) / dur
                + 0.8 * len(HOOK.findall(sents[i]["text"]))
                + 0.3 * len(HOOK.findall(text))
                + 0.2 * text.count("?")
                + 0.1 * text.count("!")
            )
            cands.append((score, sents[i]["start"], sents[j]["end"], sents[i]["text"]))
    cands.sort(key=lambda c: c[0], reverse=True)

    chosen = []
    for score, s, e, first in cands:
        if all(e <= c["start"] or s >= c["end"] for c in chosen):
            chosen.append(
                {
                    "start": s,
                    "end": e,
                    "title": first[:70],
                    "hashtags": [],
                    "score": round(score, 2),
                }
            )
        if len(chosen) >= n:
            break
    return sorted(chosen, key=lambda c: c["start"])


def llm_pick(sents, n, min_len, max_len):
    import anthropic

    client = anthropic.Anthropic()
    transcript = "\n".join(
        f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in sents
    )
    prompt = (
        f"Berikut transkrip video dengan timestamp (detik).\n\n{transcript}\n\n"
        f"Pilih maksimal {n} bagian paling menarik untuk dijadikan short video "
        f"(YouTube Shorts/TikTok/Reels). Tiap bagian {min_len}-{max_len} detik, "
        "dimulai dengan hook kuat, utuh (tidak terpotong di tengah kalimat), "
        "dan tidak saling tumpang tindih. Balas HANYA JSON array: "
        '[{"start": 12.3, "end": 45.6, "title": "judul singkat", '
        '"hashtags": ["#tag1", "#tag2"]}]'
    )
    msg = client.messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError("Jawaban Claude bukan JSON")
    data = json.loads(match.group(0))

    starts = [s["start"] for s in sents]
    ends = [s["end"] for s in sents]
    clips = []
    for c in data:
        s = min(starts, key=lambda x: abs(x - float(c["start"])))
        e = min(ends, key=lambda x: abs(x - float(c["end"])))
        dur = e - s
        if dur < min_len * 0.7 or dur > max_len * 1.3:
            continue
        if any(not (e <= o["start"] or s >= o["end"]) for o in clips):
            continue
        clips.append(
            {
                "start": s,
                "end": e,
                "title": c.get("title", "")[:100],
                "hashtags": c.get("hashtags", []),
            }
        )
    if not clips:
        raise ValueError("Tidak ada bagian valid dari Claude")
    return sorted(clips, key=lambda c: c["start"])[:n]


def pick_clips(sents, n, min_len, max_len):
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            return llm_pick(sents, n, min_len, max_len)
        except Exception as exc:  # fallback ke heuristik
            print(f"[!] Pemilihan dengan Claude gagal ({exc}); pakai heuristik.")
    return heuristic_pick(sents, n, min_len, max_len)


# ------------------------------------------------------------------- subtitle
def fmt_ass(t):
    cs = int(round(max(t, 0) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words, clip_start, clip_end, path, font, size):
    ws = [
        w
        for w in words
        if w["word"] and w["end"] > clip_start and w["start"] < clip_end
    ]
    lines, cur = [], []
    for w in ws:
        if cur and (
            len(cur) >= 4
            or w["start"] - cur[-1]["end"] > 0.7
            or cur[-1]["word"][-1:] in ".?!,"
        ):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)

    out = [ASS_HEADER.format(font=font, size=size)]
    for i, line in enumerate(lines):
        s = max(line[0]["start"], clip_start) - clip_start
        e = min(line[-1]["end"] + 0.15, clip_end) - clip_start
        if i + 1 < len(lines):
            e = min(e, lines[i + 1][0]["start"] - clip_start)
        if e <= s:
            e = s + 0.3
        text = " ".join(w["word"] for w in line).replace("{", "(").replace("}", ")")
        out.append(
            f"Dialogue: 0,{fmt_ass(s)},{fmt_ass(e)},Default,,0,0,0,,{text}"
        )
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------- render
def render(video, clip, words, outdir, idx, font, size):
    outdir = Path(outdir)
    ass_name = f"clip_{idx:02d}.ass"
    build_ass(words, clip["start"], clip["end"], outdir / ass_name, font, size)
    out_name = f"clip_{idx:02d}.mp4"
    vf = (
        "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',"
        f"scale=1080:1920,ass={ass_name}"
    )
    run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{clip['start']:.2f}",
            "-i", str(Path(video).resolve()),
            "-t", f"{clip['end'] - clip['start']:.2f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k",
            out_name,
        ],
        cwd=outdir,
    )
    return outdir / out_name


def main():
    ap = argparse.ArgumentParser(description="Auto clipper: link -> klip 9:16 + subtitle")
    ap.add_argument("source", help="URL video atau path file lokal")
    ap.add_argument("-n", "--num", type=int, default=3, help="jumlah klip")
    ap.add_argument("--min", type=int, default=20, help="durasi minimum (detik)")
    ap.add_argument("--max", type=int, default=45, help="durasi maksimum (detik)")
    ap.add_argument("--lang", default=None, help="kode bahasa, mis. id atau en (default: auto)")
    ap.add_argument("--whisper", default="small", help="ukuran model whisper: tiny/base/small/medium/large-v3")
    ap.add_argument("--font", default="Arial", help="nama font subtitle")
    ap.add_argument("--font-size", type=int, default=78)
    ap.add_argument("--out", default="output", help="folder hasil")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg belum terpasang. Pasang dulu (lihat README).")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("[1/4] Mengambil video...")
    video = get_video(args.source, out)
    print("[2/4] Transkrip (pertama kali bisa lama)...")
    sents, words = transcribe(video, out, args.whisper, args.lang)
    if not sents:
        sys.exit("Tidak ada ucapan terdeteksi di video ini.")
    print("[3/4] Memilih bagian terbaik...")
    clips = pick_clips(sents, args.num, args.min, args.max)
    if not clips:
        sys.exit("Tidak ada bagian yang cocok. Coba longgarkan --min/--max.")
    print(f"[4/4] Render {len(clips)} klip...")
    for i, clip in enumerate(clips, 1):
        path = render(video, clip, words, out, i, args.font, args.font_size)
        clip["file"] = path.name
        print(f"  - {path}  ({clip['end'] - clip['start']:.0f} dtk) {clip['title']}")
    (out / "clips.json").write_text(
        json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Selesai. Judul dan hashtag ada di {out / 'clips.json'}")


if __name__ == "__main__":
    main()
