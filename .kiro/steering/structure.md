# Peta file — di mana apa

## Root (`~/screener/`)
- `scan_ihsg.py` — scan SELURUH pasar, 2 fase: fase-1 skor semua saham likuid →
  top-60 → fase-2 V7 penuh. **File yang paling sering disentuh.**
- `scan_mandiri.py` — `scan_satu()`: rantai V7 untuk 1 saham (dipakai `scan_ihsg.py`
  dan tombol Screening Mandiri di app). TANPA efek samping (tidak tulis CSV/tidak kirim).
- `cron_v3_scan.py` — scan harian 21:00 (watchlist) + kirim hasil ke app.
- `kirim_ke_risetsaham.py` — POST batch ke app RisetSaham (token dari env).
- `shadow_eval.py` — evaluasi lab bayangan (Sabtu).
- `backtest_v7_teknikal.py` — replay mesin V7 1 tahun; **alat validasi wajib** untuk
  perubahan kebijakan.
- `uji_kebijakan/` — 9 skrip analisis (TP/SL, filter entry, gate makro, breadth,
  sektor, eksekusi, exit dinamis).
- `INTEGRASI-RISETSAHAM.md` — **WAJIB BACA** (arsitektur integrasi terkini).
- `README.md` — sebagian USANG (masih menyebut Invezgo sebagai sumber utama;
  sumber terkini = RisetSaham lokal). `ringkasan_screener.md` juga ada.
- `screenerOld/` — versi lama; abaikan.

## `idx_alpha_screener/`
- `v7_scan.py` — **engine V7 inti**: THRESHOLDS, `_swing_gate`, `gate_swing_signal`,
  gate kualitas, sizing. Perubahan di sini = perubahan kebijakan → backtest dulu.
- `scoring.py` — `compute_total_score` + `quality_gate` (falling knife, low liquidity,
  no trend; aturan false-breakout DIHAPUS 19 Sep — jangan dikembalikan tanpa bukti).
- `config.yaml` — `market_mode`, watchlist, cooldown, portfolio.
- `test_v7.py` — suite unittest (~4.500 baris); jalankan tiap perubahan.
- `perf_tracker.py`, `shadow_log.py`, `weekly_report.py` — pencatatan & evaluasi.
- `market_sentiment.py`, `entry_timing.py`, `v7_exit.py` — konteks, entry, exit level.
- `data/` — cache (`cache_v7/`), `ihsg_universe.json`, CSV hasil (untracked; jangan hapus).
- `v4/`, `v5/`, `v6/` — engine lama; jangan tambah fitur di sini.

## Sisi RisetSaham (repo LAIN — jangan diubah dari sini)
- `~/risetsaham/scripts/auto_scan_ihsg.py` — scan IHSG 19:00 + notif + simpan batch.
- `~/risetsaham/app.py` — route `/screener`, `_v7_cards` (kartu visual), tabel
  `screener_batch` di `data/risetsaham.db`.
- Kalau mengubah kontrak output → koordinasi dengan sisi app (kolom yang dibaca:
  `skor/mode/entry/sl/tp/entry_ideal/bandar_sesi/bandar_3bln/catatan/tampil`).
