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

## Screening Mandiri (scan on-demand dari app) — 16 Sep 2026

Selain jadwal 21:00, halaman `/screener` RisetSaham punya panel **🔍 Screening
Mandiri**: user pilih kode (watchlist akun / ketik sendiri, maks 20) → app
menjalankan `scan_mandiri.py` di server → hasil tampil sebagai kartu + riwayat,
dengan progress live (polling status tiap 2 detik).

Alur:

1. Tombol di `/riset/screener` → `POST /riset/screener/mandiri`.
2. Server: subprocess `.venv/bin/python scan_mandiri.py --tickers A,B,C --out /tmp/…csv`.
3. App baca CSV → batch **"V7 Mandiri · dd/mm HH:MM"** → kartu visual (skor +
   grafik + garis E/SL/TP bila sinyal).

Ciri `scan_mandiri.py`:

- Rantai keputusan V7 **SAMA** dgn `v7_scan.py` (fungsi `_swing_gate`,
  `gate_swing_signal`, `_signal_from_score` diimpor dari sana — jangan salin ulang).
- **TANPA efek samping**: tidak menulis `perf_tracker_v7.csv`, tidak menyentuh
  cooldown, tidak kirim Telegram — murni "apa kata V7 saat ini".
- **Semua** ticker yang dipindai tampil (yang tidak lolos gate ikut, dengan skor
  & alasan) — itu inti "screening mandiri", bukan cuma daftar sinyal.
- Progress ke stdout: baris `PROGRESS i/n KODE` (dibaca app untuk progress bar).
- Uji: `.venv/bin/python scan_mandiri.py --tickers TLKM,BRPT --out /tmp/x.csv`
  (±2–3 dtk/saham saat cache hangat; panggilan Stockbit tetap dibatasi sopan).

## Scan SEMUA SAHAM IHSG (16 Sep 2026 malam)

Tombol **🌐 Semua saham IHSG** di panel Screening Mandiri menjalankan
`scan_ihsg.py` — 2 fase, sopan ke sumber data:

1. **Fase 1 — peringkat seluruh pasar**: daftar ±975 saham IDX di-sweep dari
   11 sektor saham Stockbit (`/emitten/v3/sector/:id/company`; cache harian
   `idx_alpha_screener/data/ihsg_universe.json`, TTL 20 jam). Tiap saham →
   riwayat harga (Yahoo, cache 20 jam) → indikator → **skor inti V4**
   (paralel 5 worker, ±24 saham/detik).
2. **Fase 2 — V7 penuh utk finalis**: Top-60 (`--top`) diperiksa dgn rantai
   V7 lengkap (broker/asing/fundamental → Stockbit) via `scan_satu()` yang
   diimpor dari `scan_mandiri.py` — keputusan identik dengan scan terjadwal.

Kenapa 2 fase: V7 penuh butuh ±2 panggilan Stockbit per saham — kalau 975
saham langsung = ±2000 panggilan (tidak sopan & berisiko sesi). Fase 1
menyaring dgn data murah dulu; Stockbit hanya untuk finalis (±120 panggilan).

Hasil: CSV `kode,skor,mode,entry,sl,tp,catatan` (default top-20 tampil,
sinyal didahulukan) + baris `RINGKASAN` untuk aplikasi. Bukti run pertama
(16 Sep): **974 saham diperingkat · 60 finalis · 9 sinyal · 7m39s** (cache
dingin; berikutnya jauh lebih cepat).

Uji cepat: `.venv/bin/python scan_ihsg.py --limit 50 --top 10`.

Catatan: data Yahoo/Stockbit = pemakaian **NON-KOMERSIAL** (keluarga), volume
panggilan santun. Jangan publikasikan datanya.
