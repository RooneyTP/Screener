<!-- markdownlint-disable -->
<div align="center">
  <h1>IDX Alpha Screener</h1>
  <p><b>Screener saham IDX berbasis data Invezgo — engine aktif: V7 Dual Mode</b></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.11-blue" />
    <img src="https://img.shields.io/badge/data-invezgo-orange" />
    <img src="https://img.shields.io/badge/status-active-brightgreen" />
  </p>
</div>

---

## Ringkasan Eksekutif

Sistem screening saham IDX (IHSG) yang berjalan harian secara otomatis, dengan **engine aktif V7** yang memakai data **100% Invezgo** (harga, broker flow, foreign flow, fundamental) — menggantikan sumber data yfinance dari engine v3–v6.

| Aspek | Status |
|-------|--------|
| **Engine aktif** | V7 Dual Mode (swing + intraday), file `idx_alpha_screener/v7_scan.py` |
| **Sumber data** | Invezgo API (SDK `invezgo`), key dari `.env` |
| **Stack** | Python 3.11, pandas, numpy, ta, PyYAML, Invezgo SDK, Telegram Bot API |
| **Otomasi** | Scan harian 21:00 WIB, alert posisi 14:30 WIB, weekly report Sabtu 19:00 (Hermes cron + fallback `start_cron.bat`) |
| **Output** | Pesan Telegram terformat (sinyal, exit level, sizing, sentimen IHSG, AI narrative) |
| **Status produksi** | Berjalan harian sejak awal Agustus 2026 |

---

## Engine Aktif: V7

V7 = inti scoring V4 + bonus/malus dari data eksklusif Invezgo yang tidak tersedia di Yahoo Finance (`idx_alpha_screener/v7/__init__.py`):

- **Broker flow (bandarmologi)** — net buy top broker 3 hari terakhir untuk mendeteksi akumulasi/distribusi institusi per saham. Kode broker Invezgo yang dipetakan: BK=JP Morgan, AK=UBS, CC=Mandiri Sekuritas, AG=Kiwoom, ZP=Maybank, XL=Stockbit.
- **Foreign flow** — net foreign dari kode broker asing (AG, RG, DB, GS, ML, CS, UBS).
- **Fundamental quality** — PER, PBV, ROE, dividend yield.
- **Group label konglomerat** — untuk agregasi aliran dana per grup:
  - Barito: BRPT, DSSA, BUMI, ENRG
  - Bakrie: BNBR, VBID, ELTY
  - Salim: INDF, ICBP, KLBF, HMSP, BISI
  - Astra: ASII, UNTR, AKRA, CPIN, ISAT
- **Sector rotation** — agregasi arah broker flow (akumulasi vs distribusi) per grup, ditampilkan sebagai ringkasan di pesan Telegram.
- **IHSG key levels** — support/resistance IHSG + prediksi sentimen market harian (`market_sentiment.py`) sebagai konteks entry.
- **Market mode filter** — di regime BEAR/HIGH_VOLATILITY hanya STRONG_BUY yang diizinkan; RANGING mengizinkan STRONG_BUY + BUY; BULL mengizinkan semua.
- **Dual mode sinyal** — swing (horizon H+5 s/d H+20, trailing stop + TP berbasis ATR) dan intraday (H+1 s/d H+3, TP tetap + SL lebih ketat + time stop), dengan position sizing berbasis ATR dan modal acuan Rp20 juta (`v7_exit.py`).
- **Watchlist** — diambil dari `config.yaml` (grup user/barito/bakrie/salim/astra) dikurangi daftar `disabled`.

---

## Fitur Pendukung

| Fitur | Deskripsi | Modul |
|-------|-----------|-------|
| **Position tracker** | Catat posisi manual; cek SL, TP, trailing stop, dan time-stop tiap hari; alert Telegram | `position_tracker.py` (DB: `data/positions.json`) |
| **Alert posisi intraday** | Cek posisi dengan harga real-time saat jam trading (14:30 WIB); anti-spam — hanya kirim saat ada perubahan status | `position_check_intraday.py` |
| **Perf tracker (forward)** | Catat setiap sinyal ke CSV untuk mengukur win rate V7 secara forward | `perf_tracker.py` (DB: `data/perf_tracker_v7.csv`) |
| **Dedup sinyal** | Sinyal dengan harga ±1% dan usia <14 hari dianggap lanjutan (`fresh=0`), diberi label "(lanjutan)" di Telegram; cooldown 1 hari tetap berjalan sebagai lapisan terpisah | `perf_tracker.py`, `signal_manager.py` |
| **Evaluasi MFE/MAE** | Evaluasi sinyal yang sudah cukup umur (swing ≥10 hari, intraday ≥3 hari) → status WIN_TP / LOSS_SL / OPEN + mfe_pct/mae_pct | `weekly_report.py` (output: `data/evaluations_v7.csv`) |
| **AI narrative** | 1–2 kalimat konteks berbasis data untuk top 3 sinyal swing (LLM murah: DeepSeek/OpenCodeZen); jika gagal, scan tetap jalan normal | `ai_narrative.py` |
| **Market sentiment** | Prediksi arah IHSG (indikator teknikal + broker flow) + key levels | `market_sentiment.py` |
| **Entry timing** | Rekomendasi metode & rentang harga entry per saham | `entry_timing.py` |
| **Format Telegram** | Format pesan ringkas (markdown, ~3500 karakter) untuk output scan | `telegram_formatter.py` |

---

## Otomasi (Cron)

| Waktu (WIB) | Tugas | Script | Mekanisme |
|-------------|-------|--------|-----------|
| 21:00 harian | Scan V7 + kirim hasil ke Telegram | `cron_v3_scan.py` → `v7_scan.py` | Hermes cron; fallback watchdog `start_cron.bat` |
| 14:30 harian (jam trading) | Cek posisi terbuka dengan harga intraday, kirim alert jika ada perubahan status | `position_check_intraday.py` | Hermes cron |
| Sabtu 19:00 | Weekly report: evaluasi WR/MFE/MAE + ringkasan ke Telegram | `weekly_report.py` | Hermes cron |

Catatan operasional:

- `cron_v3_scan.py` membaca `TELEGRAM_BOT_TOKEN` dari `.env` (root repo), menjalankan `v7_scan.py` di environment venv Hermes dengan `PYTHONUTF8=1` (mencegah UnicodeDecodeError di Windows), lalu meneruskan stdout ke grup Telegram (default chat ID `-100XXXXXXXXXX`, bisa dioverride dengan `TELEGRAM_CHAT_ID`).
- `start_cron.bat` adalah watchdog lokal alternatif: loop `cron_v3_scan.py` setiap 24 jam + menjalankan bot `/posisi` (`telegram_positions_bot.py`).

---

## Status Metrik

**Win rate forward V7: BELUM TERUKUR.**

- Evaluasi forward baru dimulai Agustus 2026 (sinyal pertama tercatat di `idx_alpha_screener/data/perf_tracker_v7.csv` pada 3 Agustus 2026).
- Kesimpulan awal membutuhkan minimal ±30 sampel yang sudah selesai dievaluasi (sinyal swing dievaluasi setelah berumur ≥10 hari, intraday ≥3 hari, lewat `weekly_report.py`).
- Backtest V7 tidak dimungkinkan karena data broker flow Invezgo tidak memiliki histori — satu-satunya ukuran kinerja yang valid adalah evaluasi forward di atas.
- Dokumentasi ini sengaja tidak mencantumkan angka win rate/return V7 apa pun, karena belum ada bukti terukur.

---

## Histori Engine (v3–v6)

Semua engine di bawah ini **arsip / tidak aktif**. V7 adalah engine produksi satu-satunya.

| Versi | Pendekatan | Catatan |
|-------|------------|---------|
| **v3** | Scoring 7 indikator + binary swing gate + ADX filter | Terlalu strict — 0 sinyal BUY di market real |
| **v4** | 8 faktor conviction + 6 sumber confluence + soft penalties; threshold dikalibrasi dari 2.680 sinyal | Backtest (histori, data yfinance): SB≥62 **WR 53.3%** (107 sinyal, 30 ticker, 18 bulan, fee 0.4%), avg return **+0.46%** setelah fee, edge vs random **+3%** (`backtest_v4.py`, `backtest_vs_random.py`) |
| **v5** | 3 profil adaptif (MOMENTUM/REVERSAL/VALUE) + momentum of score + dynamic percentile | Tidak pernah menjadi engine produksi utama |
| **v6** | V4 + universe terbatas konglomerat | Backtest (histori): WR 47.8% (konglomerat) vs 44.0% (campuran); superseded oleh V7 |

> **Penting:** Angka backtest di atas adalah **hasil historis** dari engine lama berbasis data yfinance. Itu bukan ukuran kinerja engine aktif V7, dan tidak menjamin hasil di masa depan.

Arsip kode terkait: `idx_alpha_screener/main.py` (entry point v2–v6), `idx_alpha_screener/v4/`, `v5/`, `v6/`, `idx_alpha_screener/backtest.py`, `screenerOld/` (arsip lengkap + dokumen lama).

---

## Setup

### 1. File `.env` (root repo)

| Variabel | Wajib? | Fungsi |
|----------|--------|--------|
| `INVEZGO_API_KEY` | Ya | Akses data Invezgo (harga, broker flow, foreign flow, fundamental) |
| `TELEGRAM_BOT_TOKEN` | Ya (untuk Telegram) | Kirim hasil scan & alert ke grup Telegram |
| `TELEGRAM_CHAT_ID` | Tidak | Override chat ID default di `cron_v3_scan.py` |
| `DEEPSEEK_API_KEY` / `OPENCODE_ZEN_API_KEY` | Tidak | Backend LLM untuk AI narrative (opsional; jika tidak ada, narrative dilewati) |

### 2. Dependensi

```bash
pip install -r idx_alpha_screener/requirements.txt   # yfinance, pandas, numpy, ta, pyyaml
pip install invezgo python-dotenv requests           # runtime tambahan
```

Environment produksi saat ini = venv Hermes (`C:\Users\yanli\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`), yang sudah memuat semua dependensi di atas.

### 3. Menjalankan

```bash
# Scan manual (dari root repo) — menjalankan v7_scan.py lalu kirim ke Telegram
python cron_v3_scan.py

# Scan langsung tanpa kirim Telegram
cd idx_alpha_screener && python v7_scan.py

# Cek posisi dengan harga intraday (uji tanpa kirim: tambahkan --dry-run)
python idx_alpha_screener/position_check_intraday.py --dry-run

# Weekly report evaluasi (evaluasi saja tanpa kirim: tambahkan --no-send)
python idx_alpha_screener/weekly_report.py --no-send

# Bot Telegram untuk command /posisi
python idx_alpha_screener/telegram_positions_bot.py
```

### 4. Otomasi

- Jadwal utama diatur lewat Hermes cron (21:00 scan, 14:30 alert posisi, Sabtu 19:00 weekly report).
- Alternatif tanpa Hermes cron: jalankan `start_cron.bat` (watchdog lokal).

---

## Struktur Folder

```
Screener/
├── README.md                      ← Dokumentasi ini
├── ringkasan_screener.md          ← Ringkasan teknis arsitektur
├── cron_v3_scan.py                ← Cron wrapper scan V7 (21:00 WIB)
├── start_cron.bat                 ← Watchdog lokal (loop harian + bot /posisi)
├── backtest_v4.py                 ← Backtest v4 (histori, arsip)
├── backtest_vs_random.py          ← Uji edge vs random v4 (histori, arsip)
├── .env                           ← Kredensial (TIDAK di-commit)
├── utils/
│   └── telegram_sender.py         ← Helper kirim pesan Telegram
├── idx_alpha_screener/            ← Engine utama (V7)
│   ├── v7_scan.py                 ← Entry point scan V7 (dual mode)
│   ├── data_invezgo.py            ← Integrasi Invezgo API (InvezgoProvider)
│   ├── v7/__init__.py             ← Engine V7: broker flow, foreign, fundamental
│   ├── v7_exit.py                 ← Exit strategy swing/intraday + sizing
│   ├── market_sentiment.py        ← Sentimen IHSG + key levels
│   ├── entry_timing.py            ← Rekomendasi entry per saham
│   ├── telegram_formatter.py      ← Format pesan Telegram
│   ├── ai_narrative.py            ← AI narrative top 3 sinyal (opsional)
│   ├── position_tracker.py        ← Tracker posisi (SL/TP/trailing/time-stop)
│   ├── position_check_intraday.py ← Alert posisi intraday (14:30 WIB)
│   ├── perf_tracker.py            ← Log sinyal + dedup (perf_tracker_v7.csv)
│   ├── weekly_report.py           ← Evaluasi WR/MFE/MAE (Sabtu 19:00)
│   ├── signal_manager.py          ← Cooldown sinyal
│   ├── data.py, regime.py, scoring.py, swing_filters.py, risk.py,
│   │   slippage.py, portfolio.py  ← Modul inti scoring (dipakai V7)
│   ├── main.py, v4/, v5/, v6/, backtest.py ← Engine lama (arsip, tidak aktif)
│   ├── config.yaml                ← Semua threshold, watchlist, parameter
│   ├── data/                      ← Runtime: perf_tracker_v7.csv, signal_cooldown.json,
│   │                                screener.log, position_check_intraday.log
│   │                                (positions.json & evaluations_v7.csv dibuat otomatis)
│   └── cache/                     ← Cache data v7 per ticker (v7_*.csv, _IHSG_.csv)
└── screenerOld/                   ← Arsip kode & dokumen lawas
```

---

## Disclaimer

- **Sistem ini adalah alat bantu analisis, bukan robot trading otomatis, dan bukan rekomendasi jual/beli.** Semua keputusan investasi tetap tanggung jawab pengguna.
- **Backtest ≠ jaminan.** Seluruh angka backtest di bagian Histori Engine berasal dari engine lama (v4, data yfinance) dan tidak mencerminkan kinerja V7. Win rate forward V7 belum terukur (lihat Status Metrik).
- Data broker flow dan fundamental berasal dari pihak ketiga (Invezgo) — akurasi dan kelengkapan tidak dijamin.
- Trading saham mengandung risiko kehilangan modal. Tidak ada jaminan profit.

---

<div align="center">
  <sub>Built with Hermes Agent · Nous Research</sub>
  <br>
  <sub>© 2026 — IDX Alpha Screener</sub>
</div>
