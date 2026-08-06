# IDX Alpha Screener — Ringkasan Teknis

Ringkasan arsitektur dan status pengembangan sistem screening saham IDX. **Engine aktif: V7** (data 100% Invezgo). Dokumen ini adalah ringkasan teknis netral — untuk panduan penggunaan lengkap, lihat `README.md`.

## Arsitektur Terkini (V7)

```
Screener/
├── cron_v3_scan.py                 ← Cron wrapper: jalankan v7_scan.py + kirim ke Telegram
├── start_cron.bat                  ← Watchdog lokal (loop harian + bot /posisi)
├── .env                            ← Kredensial (INVEZGO_API_KEY, TELEGRAM_BOT_TOKEN, dll)
└── idx_alpha_screener/             ← Engine utama
    ├── v7_scan.py                  ← Entry point scan (dual mode: swing + intraday)
    ├── data_invezgo.py             ← InvezgoProvider (satu-satunya sumber data)
    ├── v7/__init__.py              ← Engine V7: broker flow, foreign flow, fundamental
    ├── v7_exit.py                  ← Exit strategy + position sizing
    ├── market_sentiment.py         ← Sentimen IHSG + key levels
    ├── entry_timing.py             ← Rekomendasi entry
    ├── telegram_formatter.py       ← Format pesan Telegram
    ├── ai_narrative.py             ← AI narrative top 3 sinyal (opsional)
    ├── position_tracker.py         ← Tracker posisi (SL/TP/trailing/time-stop)
    ├── position_check_intraday.py  ← Alert posisi intraday (14:30 WIB)
    ├── perf_tracker.py             ← Log sinyal + dedup
    ├── weekly_report.py            ← Evaluasi WR/MFE/MAE
    ├── signal_manager.py           ← Cooldown sinyal
    ├── config.yaml                 ← Semua parameter & watchlist
    ├── data/                       ← Runtime DB (CSV/JSON/log)
    └── cache/                      ← Cache harga per ticker (v7_*.csv, _IHSG_.csv)
```

Engine lama (v2–v6: `main.py`, `v4/`, `v5/`, `v6/`, `backtest.py`) dan `screenerOld/` adalah arsip — tidak aktif.

## Alur Data Invezgo

1. `InvezgoProvider` (`data_invezgo.py`) membaca `INVEZGO_API_KEY` dari `.env` root repo dan membuka koneksi SDK `invezgo`.
2. `v7_scan.py` mengambil IHSG (cache `cache/_IHSG_.csv`) → deteksi market regime → hitung sentimen market & key levels.
3. Untuk setiap ticker watchlist (union grup di `config.yaml` dikurangi daftar `disabled`): ambil harga 1 tahun → `compute_all_indicators()` → align ke IHSG → skor inti V4 (`compute_total_score`) → faktor V7 dari Invezgo:
   - `get_broker_summary(ticker, days=3)` → broker flow (akumulasi/distribusi institusi) + foreign flow (kode broker asing: AG, RG, DB, GS, ML, CS, UBS)
   - fundamental quality (PER, PBV, ROE, dividend yield)
4. Filter sinyal: swing (skor ≥50, atau akumulasi + skor ≥48, tanpa distribusi) dan intraday (skor ≥48 dan vol_ratio ≥1.0); market mode filter per regime; cooldown 1 hari.
5. Setiap sinyal lolos: hitung exit (SL/TP/trailing/time-stop) + sizing (modal acuan Rp20 juta) → log ke `data/perf_tracker_v7.csv` (dedup ±1% harga / <14 hari → `fresh=0` = sinyal lanjutan).
6. Output: `telegram_formatter.format_message()` → dicetak ke stdout → `cron_v3_scan.py` mengirim ke grup Telegram.
7. Paralel: `position_check_intraday.py` (14:30 WIB, harga real-time) mengecek posisi terbuka; `weekly_report.py` (Sabtu 19:00) mengevaluasi sinyal yang sudah cukup umur → status WIN_TP/LOSS_SL/OPEN + MFE/MAE.

## Modul Aktif (per File)

| File / Folder | Deskripsi |
|---------------|-----------|
| `v7_scan.py` | Entry point scan harian V7; dual mode swing + intraday; orkestrasi semua modul di atas; output ke stdout untuk cron |
| `data_invezgo.py` | Integrasi Invezgo API: `InvezgoProvider` (historical OHLCV, broker summary, intraday); pengganti data yfinance |
| `v7/__init__.py` | Engine V7 = skor inti V4 + bonus/malus data Invezgo (broker flow, foreign flow, fundamental, KSEI sentiment) |
| `v7_exit.py` | Exit strategy: swing (H+5–H+20, trailing + TP ATR) & intraday (H+1–H+3, TP tetap + SL ketat + time stop); `position_sizing()` |
| `market_sentiment.py` | Prediksi arah IHSG ("besok merah?") dari indikator teknikal + broker flow; `compute_ihsg_key_levels()` |
| `entry_timing.py` | Rekomendasi metode entry & rentang harga per saham (dipakai di tiap sinyal) |
| `telegram_formatter.py` | Format output scan jadi pesan Telegram ringkas (markdown, ~3500 char) |
| `ai_narrative.py` | 1–2 kalimat konteks berbasis data untuk top 3 sinyal swing; LLM murah (DeepSeek/OpenCodeZen); gagal → scan tetap jalan |
| `position_tracker.py` | Simpan posisi manual (`data/positions.json`); cek SL/TP/trailing/time-stop; `check_positions()` dipakai scan 21:00 & alert 14:30 |
| `position_check_intraday.py` | Cek posisi saat jam trading pakai harga intraday Invezgo; anti-spam (kirim hanya saat perubahan status); flag `--dry-run` |
| `perf_tracker.py` | Log tiap sinyal ke `data/perf_tracker_v7.csv` (kolom: date, ticker, mode, score, signal, entry_price, sl, tp, lots, cost, fresh); dedup persist ±1%/<14 hari; dasar ukur WR forward V7 |
| `weekly_report.py` | Evaluasi sinyal cukup umur (swing ≥10 hari, intraday ≥3 hari) → WIN_TP/LOSS_SL/OPEN + mfe_pct/mae_pct; output `data/evaluations_v7.csv` + ringkasan Telegram; flag `--no-send` |
| `signal_manager.py` | Cooldown tracker (`data/signal_cooldown.json`, default 1 hari) |
| `v7/` | Paket engine V7 (saat ini satu file `__init__.py`) |
| `data/` | Runtime DB: `perf_tracker_v7.csv`, `signal_cooldown.json`, `screener.log`, `position_check_intraday.log`; `positions.json` & `evaluations_v7.csv` dibuat otomatis saat pertama dipakai |
| `cache/` | Cache harga Invezgo per ticker (`v7_<TICKER>_1y.csv`) dan IHSG (`_IHSG_.csv`) |

Modul pendukung lain (dipakai V7, inti scoring dari era v3–v4): `data.py` (indikator teknikal), `regime.py` (deteksi regime), `scoring.py` (skor inti), `swing_filters.py`, `risk.py`, `slippage.py` (model biaya 4 tier), `portfolio.py` (heat management), `utils/telegram_sender.py` (helper Telegram).

## Konfigurasi Utama (`config.yaml`)

| Bagian | Isi |
|--------|-----|
| `scoring` | Threshold sinyal per regime, risk-reward (SL/TP multiplier ATR), ADX filter, swing gate, entry zone, IHSG market filter |
| `cooldown` | Enabled, 1 hari, `data/signal_cooldown.json` |
| `market_mode` | Filter regime: BEAR/HIGH_VOLATILITY → hanya STRONG_BUY; RANGING → SB+BUY; BULL → semua |
| `sector` | Maks 2 sinyal per sektor |
| `telegram` | Jumlah sinyal yang ditampilkan (top buy 10, top overall 5) |
| `v4` / `v5` / `v6` | Konfigurasi engine lama — `enabled: false` (arsip, tidak dipakai V7) |
| `exit_strategy` | Hard stop -15%, max hold 15 hari, flat exit, trailing stop ATR 2.5 |
| `portfolio` | Maks 5 posisi, maks 2/sektor, maks 40% eksposur per sektor |
| `watchlist` | Grup ticker: `user`, `barito`, `bakrie`, `salim`, `astra` + daftar `disabled` (ticker WR rendah dari backtest v4) |
| `slippage` | Model biaya 4 tier (large/mid/small/micro) |
| `perf_tracker` | CSV path, weekly report on, minimal 5 sinyal untuk laporan |

## Status Pengembangan

- **Produksi harian**: scan 21:00 WIB (Hermes cron, `cron_v3_scan.py`), alert posisi 14:30 WIB (`position_check_intraday.py`), weekly report Sabtu 19:00 (`weekly_report.py`); watchdog alternatif `start_cron.bat`.
- **Evaluasi forward**: baru dimulai Agustus 2026 — win rate V7 **belum terukur** (butuh minimal ±30 sampel selesai dari `weekly_report.py`). Backtest V7 tidak mungkin karena data broker flow Invezgo tidak punya histori.
- **Arsip**: v3/v4/v5/v6 dan `screenerOld/` tidak aktif; angka backtest v4 (SB≥62: WR 53.3%, 107 sinyal, fee 0.4%) adalah histori engine lama, bukan status aktif.
- **Catatan operasional Windows**: cron menjalankan Python dengan `PYTHONUTF8=1` untuk menghindari UnicodeDecodeError (cp1252) saat membaca CSV — penyebab kegagalan cron 03–05/08/2026 yang sudah diperbaiki di `cron_v3_scan.py`.
