# Deploy Auto Clipper ke Railway

Sekitar 10 menit. Kamu butuh akun GitHub dan akun Railway.

1. **Upload ke GitHub.** Buat repo baru (private lebih aman), lalu unggah semua isi folder ini
   (`app.py`, `autoclip.py`, `static/`, `Dockerfile`, `requirements.txt`).
2. **Railway:** New Project, Deploy from GitHub repo, pilih repo tadi. Railway otomatis
   memakai `Dockerfile`.
3. **Variables** (tab Variables di service):
   - `APP_PASSWORD` = password pilihanmu. **Wajib diisi**, kalau tidak siapa pun yang tahu
     alamatnya bisa memakai servermu.
   - `ANTHROPIC_API_KEY` = (opsional) agar Claude yang memilih bagian terbaik.
   - `WHISPER_MODEL` = `base` (default). Pakai `small` kalau RAM server 4 GB ke atas.
4. **Domain:** Settings, Networking, Generate Domain. Buka alamatnya dan masukkan password.

## Catatan

- Rekomendasi RAM minimal 2 GB. Render video memakai CPU, jadi video panjang bisa lama.
- Klip dihapus otomatis setelah 6 jam (ubah lewat `JOB_TTL_HOURS`). Download hasilnya sebelum itu.
- Status job disimpan di memori, jadi kalau server restart, job yang sedang berjalan hilang.
- Link YouTube sering diblokir dari server cloud. Upload file langsung lebih andal.
- Pakai hanya untuk video milikmu atau yang pemiliknya mengizinkan.
