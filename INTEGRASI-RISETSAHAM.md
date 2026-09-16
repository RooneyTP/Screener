# Integrasi ke RisetSaham — Sinyal V7 → halaman /screener (visual)

Sejak 16 Sep 2026, hasil scan V7 dikirim otomatis ke app **RisetSaham**
(dipakai keluarga) lewat endpoint `POST /riset/screener/api`. Di sana tampil
sebagai **kartu visual**: skor + bar, mode SWING/INTRADAY, garis level
**Entry / SL / TP** di atas grafik harga, harga live, dan jarak ke entry.
**Telegram kini opsional** — kanal utama = RisetSaham.

## Alur

```
21:00 WIB (hari bursa)
  cron_v3_scan.py
    ├─ jalankan v7_scan.py  (data Invezgo)
    ├─ kirim pesan Telegram   ← opsional (hanya bila TELEGRAM_BOT_TOKEN diisi)
    └─ kirim_ke_risetsaham.py ← SELALU
         baca data/perf_tracker_v7.csv (baris hari ini WIB)
         POST /riset/screener/api (header X-Screener-Token,
              ?sumber=V7 Alpha[&notif=1])
           → app menampilkan kartu visual + (opsional) notifikasi ke HP keluarga
```

## Setup di server

```cron
# crontab server (hari bursa, 21:00 WIB)
0 21 * * 1-5 cd /home/yuan/screener && .venv/bin/python cron_v3_scan.py >> logs/scan.log 2>&1
```

- `.env` (root repo, tidak ter-commit): `INVEZGO_API_KEY` (wajib);
  `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (opsional); `DEEPSEEK_API_KEY`
  (opsional, narasi AI).
- Isi `.env` dengan aman: `python isi_env.py` (input tersembunyi, tidak lewat
  chat, file ditulis mode 600).
- Token kirim ke RisetSaham dibaca OTOMATIS dari `/home/yuan/risetsaham/.env`
  (`SCREENER_TOKEN`) — TIDAK disimpan di repo ini.

## Uji manual

```bash
.venv/bin/python kirim_ke_risetsaham.py --dry            # tampilkan saja
.venv/bin/python kirim_ke_risetsaham.py --tanggal 2026-09-16 --tanpa-notif
.venv/bin/python kirim_ke_risetsaham.py --file uji.csv --dry   # dari file lain
```

Catatan: data Invezgo/Stockbit = pemakaian **NON-KOMERSIAL** (keluarga),
volume panggilan santun. Jangan publikasikan datanya.
