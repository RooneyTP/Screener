# Integrasi ke RisetSaham — Sinyal V7 → halaman /screener (visual)

Sejak 16 Sep 2026:
1. **Sumber data = RisetSaham lokal** (Yahoo + Stockbit dari server ini) —
   **TIDAK butuh Invezgo**.
2. Hasil scan dikirim otomatis ke app **RisetSaham** (`POST /riset/screener/api`)
   dan tampil sebagai **kartu visual**: skor + bar, mode SWING/INTRADAY, garis
   level **Entry / SL / TP** di atas grafik harga, harga live, jarak ke entry.
   **Telegram kini opsional** — kanal utama = RisetSaham.

## Sumber data & provider

- `data_provider.py` — pemilih sumber data.
  Default **`risetsaham`** (modul `~/risetsaham/yahoo.py` + `broker.py`).
  Set env `SCREENER_DATA=invezgo` (+ `INVEZGO_API_KEY`) kalau mau kembali.
- `data_risetsaham.py` — kelas `InvezgoProvider` drop-in (nama & bentuk return
  sama dgn `data_invezgo.py`):
  - OHLCV harian ±1 thn, IHSG, fundamental (PER/PBV/ROE/DY) → Yahoo
  - Broker summary + broker asing → Stockbit (periode terbaru ≈ 1 hari)
  - Riwayat flow (bandarmologi) → **net asing harian** (cache permanen,
    backfill bertahap ≤ `days` hari, weekend disaring)
  - Intraday IHSG 5-menit → Yahoo (^JKSE)
  - Kalender corporate action → `[]` (Invezgo-only; blackout CA nonaktif)

## Alur

```
21:00 WIB (hari bursa)
  cron_v3_scan.py → jalankan v7_scan.py (data lokal RisetSaham)
    ├─ kirim pesan Telegram   ← opsional (bila TELEGRAM_BOT_TOKEN diisi)
    └─ kirim_ke_risetsaham.py ← SELALU
         baca data/perf_tracker_v7.csv (baris hari ini WIB)
         POST /riset/screener/api (?sumber=V7 Alpha[&notif=1])
           → kartu visual di app + (opsional) notifikasi HP keluarga
```

## Setup di server

```cron
# crontab server (hari bursa, 21:00 WIB)
0 21 * * 1-5 cd /home/yuan/screener && .venv/bin/python cron_v3_scan.py >> logs/scan.log 2>&1
```

- `.env`: `SCREENER_DATA` (opsional, default `risetsaham` — tanpa API key),
  `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (opsional),
  `DEEPSEEK_API_KEY` (opsional, narasi AI), `INVEZGO_API_KEY` (hanya bila
  `SCREENER_DATA=invezgo`).
- Isi `.env` dengan aman: `python isi_env.py` (input tersembunyi, mode 600).
- Token kirim ke RisetSaham dibaca OTOMATIS dari `/home/yuan/risetsaham/.env`
  (`SCREENER_TOKEN`) — TIDAK disimpan di repo ini.

## Uji manual

```bash
.venv/bin/python kirim_ke_risetsaham.py --dry              # tampilkan saja
.venv/bin/python kirim_ke_risetsaham.py --tanpa-notif      # kirim tanpa notif HP
.venv/bin/python kirim_ke_risetsaham.py --file uji.csv --dry
cd idx_alpha_screener && ../.venv/bin/python v7_scan.py    # scan penuh (data lokal)
```

Catatan: data Yahoo/Stockbit = pemakaian **NON-KOMERSIAL** (keluarga), volume
panggilan santun. Jangan publikasikan datanya.
