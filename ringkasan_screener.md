# IDX Alpha Screener

**Sistem screening saham IDX (IHSG) berbasis scoring engine dengan multiple strategi.**

## 📊 Ringkasan untuk Claude

Halo, saya minta saran untuk program screener saham IDX saya. Berikut posisi terkini:

### Repo
`RooneyTP/Screener` — Python, berjalan di Windows via git-bash

### Arsitektur

```
idx_alpha_screener/
├── main.py              — Entry point (argparse: --v4, --v5, --top, --telegram, dll)
├── config.yaml          — Semua threshold & parameter
├── data.py              — Fetch yfinance + compute_all_indicators()
├── scoring.py           — v3 scoring (7 indikator, rbobot per regime)
├── regime.py            — Deteksi BULL/BEAR/RANGING/HIGH_VOLATILITY
├── swing_filters.py     — Swing gate (trend alignment + volume breakout)
├── signal_manager.py    — Cooldown 5 hari + sector cap
├── risk.py              — ATR-based stop loss & position sizing
├── slippage.py          — 4-tier slippage (Large/Mid/Small/Micro)
├── portfolio.py         — Portfolio heat (max 5 posisi, max 2/sektor)
├── perf_tracker.py      — Track sinyal & exit
├── backtest.py          — Backtest engine
├── v4/
│   ├── conviction.py    — 8 faktor conviction scoring (trend, volume, RS, VWAP, RSI, MACD, weekly, S/R)
│   ├── confluence.py    — 6 sumber confluence gate (daily trend, weekly, volume, OBV, donchian, EMA50)
│   └── __init__.py      — Toggle + A/B test
└── v5/
    ├── engine.py        — Master engine: profil detection → scoring → momentum → classify
    ├── dynamic_threshold.py — Percentile-based adaptive thresholds
    ├── momentum_score.py    — Track perubahan skor 5-10 hari
    └── __init__.py      — 3 profile config + detection
```

### Scoring Evolution

| Versi | Approach | Hasil Backtest |
|-------|----------|---------------|
| **v3** | Scoring 7 indikator + binary swing gate + ADX cutoff | SB≥62: WR 60%, +0.09% (tapi 0 BUY di market real) |
| **v4** | 8 faktor conviction + soft penalties + 6 confluence | SB≥62: WR 53.3%, +0.46% (107 sinyal, 30 ticker, 18 bulan) |
| **v5** | 3 profil (Momentum/Reversal/Value) + momentum of score + dynamic percentile | Baru build, belum full backtest |

### Masalah Terkini
1. **Return masih kecil** — 0.46% per sinyal after fee 0.4%. Dengan modal Rp 20jt, hasil ~Rp 250rb/bln
2. **Threshold calibration** — v4 optimal cutoff ≥62, tapi distribusi score real mean 37, max 56 — harusnya pake percentile
3. **v5 baru** — belum di-backtest, masih perlu validasi
4. **Cooldown & sector cap** — sudah jalan, mencegah overtrading
5. **Data fundamental** — yfinance sering rate limited, fundamental banyak yang kosong

### Yang Dicari Saran
1. **Strategi alternatif** untuk IDX dengan modal kecil (Rp 20jt) — apa lebih baik ETF, IPO flipping, atau sesuatu lain?
2. **Cara meningkatkan return per sinyal** — apakah trailing stop, pyramiding, atau filter tambahan?
3. **Apakah v5 approach (3 profile + momentum of score) secara akademis masuk akal** untuk IDX?
4. **Evaluasi apakah pendekatan ini ada harapan** atau lebih baik banting setir ke strategi lain?

Terima kasih!
