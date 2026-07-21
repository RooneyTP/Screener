<!-- markdownlint-disable -->
<div align="center">
  <h1>📈 IDX Alpha Screener</h1>
  <p><b>Multi-Strategy Stock Screening Engine untuk Bursa Efek Indonesia</b></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.11-blue" />
    <img src="https://img.shields.io/badge/yfinance-1.4.1-green" />
    <img src="https://img.shields.io/badge/license-MIT-orange" />
    <img src="https://img.shields.io/badge/status-active-brightgreen" />
  </p>
</div>

---

## 🎯 Ringkasan Eksekutif

Sistem screening saham IHSG berbasis **multiple scoring engines**. Dari v3 (binary filters) → v4 (conviction scoring) → v5 (adaptive profile-based).

| Metrik | v4 (Stabil) |
|--------|:----------:|
| **Win Rate** (SB≥62) | **53.3%** |
| **Avg Return** (after fee) | **+0.46%** |
| **Fee Model** | 4-tier slippage (0.35%–3.10% round-trip) |
| **Sample Size** | 107 sinyal, 30 ticker, 18 bulan |

---

## 🧠 Engine Versions

### 🔷 v3 — Swing Gate + ADX Filter
- Scoring: 7 indikator dengan bobot per regime
- Filter: binary swing gate (pass/fail)
- ADX cutoff: < 15 → HOLD
- **Masalah:** terlalu strict → 0 BUY sinyal di market real.

### 🔶 v4 — Confluence Gate + Dynamic Conviction ✅ (Aktif)
- 8 faktor conviction scoring: trend, volume, relative strength vs IHSG, VWAP, RSI, MACD, weekly trend, S/R proximity
- 6 sumber confluence: daily trend, weekly trend, volume breakout, OBV, Donchian, EMA50
- Soft penalties gradual (bukan binary cutoff)
- Threshold terkalibrasi dari 2,680 sinyal

### 🟢 v5 — 3 Profile Adaptive Scoring (Terbaru)
- **3 Profil strategi:** MOMENTUM PRO, REVERSAL PRO, VALUE PRO
- **Momentum of Score:** bonus/penalty dari perubahan skor 5 hari
- **Dynamic Percentile:** threshold adaptif (top 5%=SB, 15%=BUY)
- Bobot indikator berbeda tiap profil

---

## ⚙️ Cara Pakai

```bash
# Install
pip install -r idx_alpha_screener/requirements.txt

# Mode v3 (default)
python idx_alpha_screener/main.py --top 20

# Mode v4 — Confluence Gate + Dynamic Conviction
python idx_alpha_screener/main.py --top 125 --v4

# Mode v5 — 3 Profile Adaptive Scoring (Terbaru!)
python idx_alpha_screener/main.py --top 125 --v5

# Dengan Telegram
python idx_alpha_screener/main.py --top 125 --no-ihsg --telegram --v4

# Full scan harian
python idx_alpha_screener/main.py --top 125 --no-ihsg --v4 --quiet
```

### Arguments

| Flag | Fungsi |
|------|--------|
| `--top N` | Scan N saham paling liquid (default 10) |
| `--ticker BBCA.JK` | Scan spesifik ticker |
| `--v4` | Gunakan v4 engine |
| `--v5` | Gunakan v5 engine (terbaru) |
| `--telegram` | Kirim hasil ke Telegram |
| `--no-ihsg` | Skip IHSG alignment (lebih cepat) |
| `--force` | Paksa scan walau IHSG bearish |
| `--parallel` | Multi-threaded fetch |
| `--quiet` | Minimal output |

---

## 📁 Struktur Project

```
Screener/
├── idx_alpha_screener/     ← Engine utama
│   ├── main.py             ← Entry point
│   ├── config.yaml         ← Semua threshold & parameter
│   ├── data.py             ← Fetch yfinance + indikator
│   ├── scoring.py          ← v3 scoring engine
│   ├── regime.py           ← Market regime detection
│   ├── swing_filters.py    ← Swing gate confirmation
│   ├── signal_manager.py   ← Cooldown + sector cap
│   ├── risk.py             ← ATR stop loss & sizing
│   ├── slippage.py         ← 4-tier slippage model
│   ├── portfolio.py        ← Portfolio heat management
│   ├── perf_tracker.py     ← Signal & exit tracking
│   ├── backtest.py         ← Backtest engine
│   ├── v4/                 ← v4: Confluence Gate + Conviction
│   └── v5/                 ← v5: 3 Profile Adaptive Scoring
├── cron_v3_scan.py         ← Cron wrapper (21:00 WIB)
├── screenerOld/            ← Arsip kode lawas
├── utils/telegram_sender.py ← Telegram API
└── .gitignore
```

---

## 📊 Hasil Backtest

### v4 — 30 Ticker IHSG Liquid (18 Bulan)

| Threshold | N | WR | Avg Return (after fee) |
|:---------:|:-:|:--:|:---------------------:|
| ≥ **62** | 107 | **53.3%** | **+0.46%** ✅ |
| ≥ 60 | 187 | 52.4% | +0.14% |
| ≥ 58 | 266 | 47.7% | -0.31% |
| ≥ 55 | 466 | 42.5% | -0.82% |
| ≥ 50 | 1,068 | 42.3% | -0.79% |

> **Hanya STRONG_BUY (≥62) yang profitable setelah fee 0.4%.**

### Backtest Tooling

```bash
# Backtest v4 threshold (30 ticker, 18 bulan)
python idx_alpha_screener/backtest.py

# Ad-hoc verification
cd idx_alpha_screener && python -m pytest test_screener.py -v
```

---

## 🔧 Configuration

Semua threshold terkonsentrasi di `config.yaml`:

```yaml
scoring:
  thresholds:           # v3 threshold per regime
  adx_filter:           # ADX cutoff
  entry_zone:           # Harga entry ideal/good/max

v4:
  enabled: false        # Aktifkan v4 engine
  thresholds:           # 5-level: [SB, BUY, WB, HOLD, SELL]
  confluence_bonus_multiplier: 0.5

v5:
  enabled: false        # Aktifkan v5 engine
  dynamic_percentile: true
  percentile_sb_target: 5    # Top 5% = STRONG_BUY
  percentile_buy_target: 15  # Top 15% = BUY
```

---

## 🚀 Automation (Cron)

Cron job berjalan setiap **21:00 WIB** (setelah market close):

```bash
# Script: cron_v3_scan.py
# Scheduled via Hermes cron: v4-screener-2100
# Deliver: Telegram + local log

python main.py --top 125 --no-ihsg --telegram --v4
```

Hasil otomatis dikirim ke Telegram @QuantYan_bot.

---

## 📦 Requirements

- Python 3.11+
- `yfinance`, `pandas`, `numpy`, `requests`, `pyyaml`
- `matplotlib` (opsional, untuk chart backtest)
- `python-dotenv` (untuk Telegram token)

---

## 📈 Roadmap

| Status | Fitur |
|--------|-------|
| ✅ | v3: Swing Gate + ADX Filter |
| ✅ | v4: Confluence Gate + Dynamic Conviction |
| ✅ | v5: 3 Profile Adaptive Scoring |
| 🟡 | Backtest v5 full universe |
| ⬜ | Trailing stop logic |
| ⬜ | Real-time signal via Telegram bot |
| ⬜ | Portfolio tracker + P&L report |

---

## ⚠️ Disclaimer

**Sistem ini adalah alat bantu analisis teknis, bukan robot trading otomatis.**
- Seluruh backtest menggunakan data historis — performa masa lalu tidak menjamin hasil masa depan
- Fee 0.4% sudah termasuk dalam perhitungan (broker fee + VAT + slippage)
- Tidak ada jaminan profit — trading saham mengandung risiko kehilangan modal
- Untuk edukasi dan referensi, bukan rekomendasi jual/beli

---

<div align="center">
  <sub>Built with Hermes Agent · Nous Research</sub>
  <br>
  <sub>© 2026 — IDX Alpha Screener</sub>
</div>
