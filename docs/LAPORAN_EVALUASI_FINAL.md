═══════════════════════════════════════════════════════════════
            LAPORAN EKSEKUTIF — HERMES-PRIME
═══════════════════════════════════════════════════════════════
📌 TOPIK         : Evaluasi Komprehensif Stock Screener IDX (Python)
📅 TIMESTAMP     : 2026-06-16
🔁 TASK ID       : TASK-20260616-001
👤 DIMINTA OLEH  : Principal
🤖 DIKERJAKAN    : DEV (Code Review & Optimization), QUANT (Trading Logic Verification)
📊 STATUS FINAL  : ⚠️ SELESAI — DENGAN TEMUAN KRITIS

───────────────────────────────────────────────────────────────
🔍 RINGKASAN EKSEKUTIF
───────────────────────────────────────────────────────────────
Evaluasi komprehensif terhadap Stock Screener IDX v10.0 (7 file, ~3.860 baris)
telah selesai. Ditemukan 18+ isu kode dan 20+ masalah logika trading.

**Skor Keandalan Sistem Keseluruhan: 4.5/10**
Sistem memiliki potensi arsitektur yang bagus (multi-indikator, integrasi makro,
adaptive weights), tetapi terkendala: (a) data palsu di bandarmologi, 
(b) inkonsistensi logika scoring, dan (c) duplikasi kode yang parah.

Temuan paling kritis: **Program TIDAK BISA BERJALAN** karena 9 dari 10 modul
yang di-import TIDAK ADA di folder workspace (ModuleNotFoundError saat startup).

───────────────────────────────────────────────────────────────
⚙️ LAPORAN DEV — CODE REVIEW & OPTIMIZATION
───────────────────────────────────────────────────────────────
File dievaluasi: screener.py, scoring_engine.py, shareholder_analyzer.py,
indicators.py, data_fetcher.py, build_cache.py, build_cache_v2.py

───────────────────────────────────────────────────────────────
🧠 LAPORAN QUANT — TRADING LOGIC VERIFICATION
───────────────────────────────────────────────────────────────
File dievaluasi: screener.py, scoring_engine.py, indicators.py, 
shareholder_analyzer.py, broker_scraper.py

───────────────────────────────────────────────────────────────
🔥 DAFTAR BUG / INEFISIENSI — PRIORITAS PERBAIKAN
───────────────────────────────────────────────────────────────

═══ 🚨 CRITICAL — Harus Diperbaiki SEBELUM Run ═══

┌──────────────────────────────────────────────────────────────┐
│ [C1] 9 MODUL IMPORT HILANG → CRASH SAAT STARTUP              │
├──────────────────────────────────────────────────────────────┤
│ Lokasi  : screener.py baris 42-53 (module-level imports)     │
│ Dampak  : ModuleNotFoundError pada security.py, ai_model.py,  │
│           nlp_scraper.py, broker_scraper.py, foreign_flow.py, │
│           mean_reversion.py, monte_carlo.py, trade_journal.py,│
│           performance.py — HANYA backtest.py yang ada.        │
│ Perbaikan: Ganti semua import missing dengan LAZY IMPORT      │
│            (impor di dalam fungsi, bukan module-level)        │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ [C2] DATA BROKSUM 100% PALSU (broker_scraper.py)             │
├──────────────────────────────────────────────────────────────┤
│ Rating    : 1/10 — BUKAN REAL SCRAPING                       │
│ Lokasi  : broker_scraper.py (seluruh file)                   │
│ Detail  : "top_buyers" dan "top_sellers" adalah data          │
│           hardcoded (ZP=50K, BK=30K, YP=40K, PD=25K).       │
│           Setiap saham return data SAMA. Logika dibaliknya   │
│           bagus (hingga rating 7/10), tapi data input PALSU. │
│ Dampak  : Semua keputusan bandarmologi berdasarkan data ini  │
│           memberikan sinyal acak/konstan ke semua saham.     │
│ Perbaikan: Nonaktifkan atau beri label "✱ DATA SIMULASI ✱"  │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ [C3] DUAL SCORING LOGIC — Duplikasi Kode Total               │
├──────────────────────────────────────────────────────────────┤
│ File     : scoring_engine.py vs screener.py baris 1772-1804  │
│ Detail  : Kedua file mengimplementasikan fungsi scoring yang │
│           HAMPIR IDENTIK: normalisasi, adaptive weights,     │
│           IHSG penalty, multi-timeframe bonus.               │
│           screener.py meng-import fungsi scoring_engine.py   │
│           TAPI TIDAK MENGGUNAKANNYA — malah duplikasi.       │
│ Dampak  : Maintenance hazard — perbaikan di satu file tidak  │
│           otomatis berefek di file lain.                     │
│ Perbaikan: Panggil compute_confidence() langsung, hapus      │
│            duplikasi logika dari screener.py                 │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ [C4] ICHIMOKU HANYA 1 DARI 5 KOMPONEN                       │
├──────────────────────────────────────────────────────────────┤
│ Rating    : 3/10                                             │
│ Lokasi  : screener.py baris 967                              │
│ Kode    : cloud_signal = "BULLISH" if close > senkou_a ...   │
│           else "BEARISH"                                     │
│ Masalah : Hanya cek Close vs Senkou_A. TIDAK cek:            │
│           • Senkou_A vs Senkou_B (kumo shape)                │
│           • Tenkan vs Kijun (TK cross)                       │
│           • Chikou Span vs harga 26 periode lalu             │
│           • Future cloud (displacement 26 hari ke depan)     │
│           • Senkou standar di-shift 26 hari ke depan         │
│ Dampak  : Sinyal Ichimoku sangat misleading — bisa BULLISH   │
│           padahal cloud merah (Senkou_A < Senkou_B)           │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ [C5] RSI RANGE INKONSISTEN — 40-65 vs 30-50                 │
├──────────────────────────────────────────────────────────────┤
│ Rating    : 4/10                                             │
│ Lokasi  : screener.py baris 1675 vs baris 1708               │
│ Detail  : Baris 1675: 40 ≤ RSI ≤ 65 → skor +1.0             │
│           Baris 1708: 30 ≤ RSI ≤ 50 → tech_score +15        │
│ Masalah : Range 40-65 mencakup 50-65 (mendekati overbought) │
│           yang BERTENTANGAN dengan definisi "good entry"     │
│           di range 30-50. Dua standar berbeda untuk indikator│
│           yang sama, tanpa dokumentasi.                      │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ [C6] SUSPENDED STOCKS — Tidak Deteksi Harga Flat            │
├──────────────────────────────────────────────────────────────┤
│ Rating    : ⚠️ Potensi data invalid                         │
│ Lokasi  : screener.py baris 1299-1307                       │
│ Masalah : validasi_data_yfinance() hanya cek volume=0        │
│           selama 10 hari. Saham suspended biasanya juga punya│
│           harga flat (Close sama persis tiap hari).          │
│           Juga tidak ada deteksi anomali stock split/right   │
│           issue yang bisa merusak indikator teknikal.         │
└──────────────────────────────────────────────────────────────┘

═══ 🟠 MAJOR — Perbaiki Setelah Critical ═══

[M7] DIVISION BY ZERO:
     • ease_of_movement() — box_ratio bisa 0 (high=low intraday)
     • volume_oscillator() — long_ma bisa 0 (saham tidur)
     Lokasi: screener.py baris 663-673

[M8] NAN PROPAGATION:
     • Tidak ada centralized NaN guard untuk 20+ indikator
     • Satu indikator NaN → merusak scoring & AI prediction
     Lokasi: screener.py baris 1432-1446

[M9] .apply(lambda) PANDAS — Lambat:
     • shareholder_analyzer.py baris 457-460: apply(lambda, axis=1)
     • shareholder_analyzer.py baris 131-168: iterrows() 2x
     • screener.py baris 620-626: HMA rolling.apply(lambda)
     • screener.py baris 1460: rolling.apply(lambda) untuk CCI

[M10] DUAL CACHE SYSTEM:
      • data_fetcher.py: pickle-based (TTL 4 jam)
      • screener.py: joblib-based
      • Keduanya menyimpan data yang SAMA dengan format BERBEDA →
        boros disk, tidak konsisten

[M11] TRIPLIKASI KSEI PDF PARSING:
      • shareholder_analyzer.py: _parse_all_pdfs() + _parse_single_pdf()
      • build_cache.py: parse_pdf()
      • build_cache_v2.py: parse()
      → Ketiganya melakukan hal yang sama, KSEI_CLASS_MAP didefinisikan
      ulang 2x, logika klasifikasi sedikit berbeda

[M12] KELLY CRITERION RRR FIXED:
      • Kelly menggunakan RRR=2.0 HARDCODED (baris 376)
      • Tapi RRR aktual dihitung di baris 1699 — tidak diteruskan
      • Error: jika RRR=1.5, Kelly overestimates ukuran posisi 44%

[M13] IHSG_TREND 3 HARI:
      • last3 = ihsg_data.tail(3); ALL DIFF > 0 → UP else DOWN
      • Rating: 2/10 — sangat noise-prone
      • Dalam random walk, 3 hari hijau = 12.5% prob — sering kebetulan

[M14] BOBOT_SKOR — DOBEL COUNTING?
      • Baris 1654 langsung skor += 2.0 untuk EMA_Aligned
      • Tapi BOBOT_SKOR["EMA_Aligned"] = 2.0 juga (tidak pernah dipanggil)
      → Belum tentu dobel counting, tapi kode tidak konsisten

═══ 🟡 MINOR — Code Quality ═══

[M15] Wildcard import: `from indicators import *` (baris 43)
[M16] 6+ komentar "FIX BUG 1-6" sudah obsolete
[M17] 10+ marker 🔥/👇 instruksional (tidak profesional)
[M18] Nama fungsi campur Inggris-Indonesia
[M19] Global variables tidak thread-safe (ThreadPoolExecutor!)
[M20] Docstring bombastis — klaim "Liquid Neural Network" dll tidak ada
[M21] `rolling().apply(lambda)` untuk CCI → ganti `.mad()` (built-in)

───────────────────────────────────────────────────────────────
📊 EVALUASI KEANDALAN LOGIKA TRADING — PER KOMPONEN
───────────────────────────────────────────────────────────────

┌──────────────────────────────────────────────────────────────┐
│ TEKNIKAL INDICATORS                                        │
├────────────┬──────────┬─────────────────────────────────────┤
│ Komponen   │ Rating   │ Catatan                             │
├────────────┼──────────┼─────────────────────────────────────┤
│ EMA         │ 7/10     │ Chained comparison benar            │
│ Alignment   │          │ Komentar misleading                 │
├────────────┼──────────┼─────────────────────────────────────┤
│ MACD        │ 8/10     │ Operator precedence benar           │
│             │          │ Komentar "FIX 1.3" misleading       │
├────────────┼──────────┼─────────────────────────────────────┤
│ RSI         │ 4/10 ❌  │ Range 40-65 vs 30-50 inkonsisten    │
├────────────┼──────────┼─────────────────────────────────────┤
│ Stochastics │ 6/10     │ Kurang deteksi crossover            │
├────────────┼──────────┼─────────────────────────────────────┤
│ ADX         │ 4/10 ❌  │ 4 threshold berbeda (25/30/35/40)    │
├────────────┼──────────┼─────────────────────────────────────┤
│ VCP Pattern │ 5/10     │ Konsep benar, hanya 1 bar           │
├────────────┼──────────┼─────────────────────────────────────┤
│ Ichimoku    │ 3/10 ❌  │ Sangat tidak lengkap — hanya 1/5   │
└────────────┴──────────┴─────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ BANDARMOLOGI                                                │
├────────────────────┬──────┬─────────────────────────────────┤
│ Komponen          │ Rtg  │ Catatan                         │
├────────────────────┼──────┼─────────────────────────────────┤
│ MM Activity        │ 7/10 │ Multi-indicator baik            │
│ Detection          │      │ Mixed signals tidak dihandle    │
├────────────────────┼──────┼─────────────────────────────────┤
│ MM Position        │ 3/10 │ Spekulasi murni                │
│ Estimate           │      │ Semua parameter heuristic       │
├────────────────────┼──────┼─────────────────────────────────┤
│ Retail vs MM       │ 2/10 │ 35% muncul 2x, Russian-doll    │
│ Comparison         │      │ overlap kalkulasi              │
├────────────────────┼──────┼─────────────────────────────────┤
│ Wyckoff Absorption │ 6/10 │ Konsep benar, perlu multiday    │
├────────────────────┼──────┼─────────────────────────────────┤
│ Broksum            │ 1/10 │ ❌ DATA PALSU (hardcoded mock)  │
└────────────────────┴──────┴─────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ RISK MANAGEMENT                                             │
├────────────────────┬──────┬─────────────────────────────────┤
│ Komponen          │ Rtg  │ Catatan                         │
├────────────────────┼──────┼─────────────────────────────────┤
│ Stop Loss Level   │ 5/10 │ Trend widening bisa dibela       │
├────────────────────┼──────┼─────────────────────────────────┤
│ Stop Loss Trailing │ 4/10 │ BUKAN trailing (recalculate)    │
├────────────────────┼──────┼─────────────────────────────────┤
│ Kelly Criterion    │ 4/10 │ RRR=2.0 fixed, typo "harga"     │
├────────────────────┼──────┼─────────────────────────────────┤
│ Position Sizing    │ 7/10 │ Logika oke, tidak ada validasi  │
└────────────────────┴──────┴─────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ SCORING ENGINE & CONSTANTS                                  │
├────────────────────┬──────┬─────────────────────────────────┤
│ Komponen          │ Rtg  │ Catatan                         │
├────────────────────┼──────┼─────────────────────────────────┤
│ compute_confidence │ 3/10 │ Duplikasi total (lihat C3)      │
├────────────────────┼──────┼─────────────────────────────────┤
│ typical_max        │ 5/10 │ Heuristic, sent_score too low    │
├────────────────────┼──────┼─────────────────────────────────┤
│ Calibration Map    │ 4/10 │ Konstruktif, diskontinu         │
├────────────────────┼──────┼─────────────────────────────────┤
│ BOBOT_SKOR         │ 5/10 │ Tidak ada backtest              │
├────────────────────┼──────┼─────────────────────────────────┤
│ MACRO_PENALTY      │ 4/10 │ Simplifikasi emas              │
├────────────────────┼──────┼─────────────────────────────────┤
│ IHSG_TREND 3 hari  │ 2/10 │ ❌ Sangat noise-prone           │
└────────────────────┴──────┴─────────────────────────────────┘

───────────────────────────────────────────────────────────────
✅ POTONGAN KODE YANG SUDAH DIPERBAIKI & DIOPTIMASI
───────────────────────────────────────────────────────────────

--- [FIX C1] Lazy Import untuk Semua Modul Opsional ---

```python
# MODULE-LEVEL: hanya import yang PASTI ADA
from indicators import (
    calculate_sma, calculate_ema, calculate_rsi, calculate_macd,
    calculate_adx, calculate_bollinger_bands, calculate_atr,
    calculate_obv, calculate_vwap, hma, detect_support_resistance
)
from data_fetcher import fetch_macro_data, fetch_price_data_sync
import data_fetcher
from scoring_engine import compute_confidence, get_calibrated_win_prob, get_signal

# LAZY IMPORT — di dalam fungsi saat pertama dipakai
def _get_security_module():
    """Get security module with graceful fallback."""
    import os
    try:
        from security import get_env_var
        return get_env_var
    except ImportError:
        # Fallback: os.environ + .env
        from dotenv import load_dotenv
        load_dotenv()
        return lambda name, default="": os.environ.get(name, default)
```

--- [FIX C2] Validasi Suspended Stocks + Harga Flat + Anomali ---

```python
def validasi_data_yfinance(df: pd.DataFrame, ticker: str) -> bool:
    """Comprehensive data quality validation."""
    if df.empty or len(df) < 50:
        return False

    # 1. Deteksi Saham Suspend: Volume 0 terus-menerus
    if df['Volume'].tail(10).sum() == 0:
        logger.warning("[SANITIZER] %s dilewati — volume=0 10 hari (suspend)", ticker)
        return False

    # 2. Deteksi harga flat (suspend / gocap / error data)
    price_range_10d = (df['Close'].tail(10).max() - df['Close'].tail(10).min())
    if price_range_10d == 0:
        logger.warning("[SANITIZER] %s dilewati — harga flat 10 hari", ticker)
        return False

    # 3. Deteksi anomali stock split / corporate action
    pct_change = df['Close'].tail(5).pct_change().abs() * 100
    anomali = pct_change[pct_change > 40.0]
    if not anomali.empty:
        logger.warning("[SANITIZER] %s dilewati — anomali >40%% dalam 1 hari", ticker)
        return False

    return True
```

--- [FIX C3] Single Source of Truth untuk Scoring ---

```python
# Ganti seluruh blok manual (baris 1772-1804) dengan:
from scoring_engine import compute_confidence as compute_confidence_engine

confidence, skor, c_thresh_buy = compute_confidence_engine(
    tech_score=tech_score, fund_score=fund_score,
    rs_score=rs_score, sent_score=sent_score,
    adx_val=adx_v, ihsg_change=IHSG_CHANGE, ihsg_trend=IHSG_TREND,
    weekly_bullish=weekly_bullish, monthly_bullish=monthly_bullish,
    pct_above_ema50=pct_above_ema50
)

# Hapus baris 1701-1804 (logika manual yang diduplikasi)
```

--- [FIX C6] Safe Float Extraction dengan NaN Guard ---

```python
def _safe_float_value(series: pd.Series, default: float = 0.0) -> float:
    """Extract last value from series with NaN/Inf guard."""
    if series is None or series.empty:
        return default
    val = series.iloc[-1]
    if pd.isna(val) or np.isinf(val):
        return default
    return float(val)

# Penggunaan:
adx_v = _safe_float_value(adx_val)
macd_v = _safe_float_value(macd_line)
macd_s = _safe_float_value(macd_signal)
macd_h = _safe_float_value(macd_hist)
rsi_v = _safe_float_value(rsi)
stoch_v = _safe_float_value(stoch_k)
stoch_d_v = _safe_float_value(stoch_d)
# ... dan seterusnya untuk semua 20+ indikator
```

--- [FIX M7] Division by Zero Guard ---

```python
def ease_of_movement(high, low, volume, window=14):
    distance = ((high + low) / 2) - ((high.shift(1) + low.shift(1)) / 2)
    price_range = (high - low).clip(lower=0.0001)  # Minimum
    box_ratio = (volume / 100000000) / price_range
    box_ratio = box_ratio.replace([np.inf, -np.inf], np.nan).fillna(0)
    emv = distance / box_ratio.replace(0, np.nan)
    emv = emv.replace([np.inf, -np.inf], np.nan).fillna(0)
    return emv.rolling(window).mean().fillna(0)

def volume_oscillator(volume, short_window=5, long_window=10):
    short_ma = volume.rolling(short_window).mean()
    long_ma = volume.rolling(long_window).mean()
    result = ((short_ma - long_ma) / long_ma.replace(0, np.nan)) * 100
    return result.fillna(0).replace([np.inf, -np.inf], 0)
```

--- [FIX M9a] Vectorized Shareholder Classification ---

```python
def _classify_shareholder_vectorized(df: pd.DataFrame) -> pd.Series:
    """Vectorized shareholder classification — ganti apply(lambda, axis=1)."""
    cat_map = df["category"].map(KSEI_CLASS_MAP)
    text = (df["name"].fillna("") + " " + df["category"].fillna("")).str.lower().str.strip()

    conditions = [
        cat_map.notna(),
        text.str.contains("|".join(["pt ", "pt.", "cv ", "yayasan", "koperasi",
            "bank", "sekuritas", "asuransi", "fund", "capital", "investment",
            "limited", "ltd", "corp", "inc", "company", "group", "holding"]), na=False),
        text.str.contains("|".join(["individual", "perorangan"]), na=False),
        (~text.str.contains("pt |pt.", na=False) & (df["_calc_pct"] >= 5.0)),
    ]
    choices = ["MM", "MM", "RETAIL", "INSIDER"]
    return np.select(conditions, choices, default="RETAIL")

# Penggunaan:
data["_reclass"] = _classify_shareholder_vectorized(data)
```

--- [FIX M9b] Vectorized WMA (ganti rolling.apply) ---

```python
def _wma_vectorized(series: pd.Series, length: int) -> pd.Series:
    """Vectorized Weighted Moving Average — O(n) via numpy convolve."""
    weights = np.arange(1, length + 1, dtype=float)
    values = series.values
    padded = np.pad(values, (length - 1, 0), mode='constant', constant_values=np.nan)
    wma_vals = np.convolve(padded, weights[::-1], mode='valid') / weights.sum()
    wma_vals[:length-1] = np.nan
    return pd.Series(wma_vals, index=series.index)
```

───────────────────────────────────────────────────────────────
📋 RENCANA TINDAK LANJUT (6 Langkah Prioritas)
───────────────────────────────────────────────────────────────

│ #  │ Langkah                        | Estimasi │ Dampak            │
│────│────────────────────────────────│──────────│───────────────────│
│ 1  │ Fix lazy imports — hapus       │ 30 menit  │ Program bisa RUN │
│    │ 9 missing module-level imports │           │                   │
│ 2  │ Nonaktifkan broker_scraper     │ 10 menit  │ Hentikan data     │
│    │ atau beri label simulasi       │           │ palsu             │
│ 3  │ Single source of truth scoring │ 1 jam     │ Hapus duplikasi   │
│    │ panggil compute_confidence()   │           │ scoring           │
│ 4  │ Fix Ichimoku: implementasi     │ 2 jam     │ Sinyal akurat     │
│    │ 4+ komponen lengkap            │           │                   │
│ 5  │ Standardisasi RSI & ADX        │ 30 menit  │ Konsistensi       │
│    │ threshold                      │           │ scoring           │
│ 6  │ NaN guard + division by zero   │ 30 menit  │ Robust terhadap   │
│    │ + validasi suspended           │           │ error data        │

═══════════════════════════════════════════════════════════════
📁 DAFTAR FILE OUTPUT DI WORKSPACE
═══════════════════════════════════════════════════════════════
• C:\Hermes_Workspace\Screener\CODE_REVIEW_REPORT.md
     — Laporan Full Code Review (DEV) — 773 baris, 18 temuan

• C:\Hermes_Workspace\Screener\verification_report.md
     — Laporan Full Trading Logic Verification (QUANT) — 698 baris

• C:\Hermes_Workspace\Screener\LAPORAN_EVALUASI_FINAL.md
     — Laporan Sintesis Final ini

═══════════════════════════════════════════════════════════════
