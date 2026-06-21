# CODE REVIEW & OPTIMIZATION REPORT — Stock Screener IDX

**Review date:** 2026-06-16  
**Repository:** `C:\Hermes_Workspace\Screener\`  
**Files reviewed:** `screener.py` (2741 lines), `indicators.py` (49), `data_fetcher.py` (153), `scoring_engine.py` (106), `shareholder_analyzer.py` (614), `build_cache.py` (112), `build_cache_v2.py` (85)  
**Total lines reviewed:** ~3,860

---

> **Prioritas Label:**
> - 🔴 **CRITICAL** — Potensi crash, data loss, atau logic error parah
> - 🟠 **MAJOR** — Inefisiensi signifikan, duplikasi, atau bug serius
> - 🟡 **MINOR** — Code smell, kebersihan, komentar usang

---

## 1. EFISIENSI PANDAS & VECTORIZATION

### 🔴 1a. `shareholder_analyzer.py` baris 457-460 — `.apply()` dengan lambda untuk reklasifikasi

**Temuan:** `data.apply(lambda r: _classify_shareholder(...), axis=1)` memanggil fungsi Python untuk setiap baris DataFrame. Untuk ribuan baris, ini lambat.

**File:** `shareholder_analyzer.py:457-460`
```python
data["_reclass"] = data.apply(
    lambda r: _classify_shareholder(r["name"], r.get("category", ""), r["_calc_pct"]),
    axis=1
)
```

**Solusi:** Gunakan vectorized approach dengan `np.select()` atau dictionary mapping. Karena `_classify_shareholder()` punya logika kompleks (keyword matching, insider detection), setidaknya pre-filter kolom yang bisa di-vectorized dulu.

**Perbaikan:**
```python
# Vectorized pre-classification: handle KSEI_CLASS_MAP and MM/Retail keywords first
def _classify_shareholder_vectorized(df: pd.DataFrame) -> pd.Series:
    """Vectorized shareholder classification."""
    # Get category from KSEI_CLASS_MAP where possible
    cat_map = df["category"].map(KSEI_CLASS_MAP)
    
    # Build text column for keyword matching
    text = (df["name"].fillna("") + " " + df["category"].fillna("")).str.lower().str.strip()
    
    # Classification conditions
    conditions = [
        cat_map.notna(),                                          # KSEI class matches directly
        text.str.contains("|".join(MM_KEYWORDS), na=False),      # MM keywords
        text.str.contains("|".join(RETAIL_KEYWORDS), na=False),  # Retail keywords
        # Insider: individual name (no entity keywords) with >5% ownership
        (~text.str.contains("|".join(["pt ", "pt.", "cv ", "yayasan", "koperasi",
            "bank", "sekuritas", "asuransi", "fund", "capital", "investment", 
            "limited", "ltd", "corp", "inc", "company", "group", "holding", "management"]), na=False)
         & (df["_calc_pct"] >= 5.0)),
        df["name"].str.split().str.len() >= 3,                    # 3+ word names → likely institution
    ]
    choices = ["MM", "MM", "RETAIL", "INSIDER", "MM"]
    
    return np.select(conditions, choices, default="RETAIL")

# Usage:
data["_reclass"] = _classify_shareholder_vectorized(data)
```

---

### 🟠 1b. `shareholder_analyzer.py` baris 131-168 — loop `iterrows()` untuk parsing CSV

**Temuan:** Dua loop `for _, row in csv_df.iterrows()` di baris 131 dan 165. `iterrows()` sangat lambat untuk DataFrame besar karena mengembalikan Series per baris.

**File:** `shareholder_analyzer.py:131-168`
```python
for _, row in csv_df.iterrows():
    try:
        ticker = str(row.get("Kode Efek", "")).strip().upper()
        # ... 30+ lines processing per row
```

**Solusi:** Gunakan `.apply()` dengan `axis=1` sekali (masih ada overhead, tapi jauh lebih cepat dari `iterrows`), atau lebih baik lagi, manipulasi kolom langsung.

**Perbaikan:**
```python
def _parse_ksei_format(df: pd.DataFrame, fname: str, file_date=None) -> pd.DataFrame:
    """Vectorized parsing of KSEI CSV format."""
    result = df.copy()
    result["ticker"] = result.get("Kode Efek", "").astype(str).str.strip().str.upper()
    result["name"] = result.get("Nama Pemegang Saham", "").astype(str).str.strip()
    
    # Find shares column
    shares_col = next((c for c in df.columns if "Kepemilikan Per" in c), None)
    if shares_col:
        result["shares"] = pd.to_numeric(
            result[shares_col].astype(str).str.replace(",", "").str.replace(".", ""),
            errors="coerce"
        ).fillna(0)
    
    # Filter invalid
    result = result[result["ticker"].notna() & (result["ticker"] != "NAN") 
                    & result["name"].notna() & (result["shares"] > 0)]
    
    # Classification
    lok_str = result.get("Status (Lokal/Asing)", "").astype(str).str.strip().str.upper()
    is_asing = lok_str.str.contains("ASING", na=False)
    is_mm_name = result["name"].str.upper().str.contains(
        "PT |PT\\.|CV |YAYASAN|KOPERASI", na=False, regex=True
    )
    result["classification"] = np.where(is_asing | is_mm_name, "MM", "RETAIL")
    result["category"] = lok_str
    result["pct"] = 0.0
    result["date"] = file_date
    result["source_file"] = fname
    
    return result[["ticker", "name", "category", "shares", "pct", "classification", "date", "source_file"]]
```

---

### 🔴 1c. `screener.py` baris 620-626 — HMA menggunakan `.rolling().apply()` dengan lambda

**Temuan:** `hma()` menggunakan `series.rolling(length).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)`. Setiap `.apply()` di rolling window memanggil fungsi Python per window — sangat lambat untuk deret panjang.

**File:** `screener.py:618-626`
```python
def _wma(series, length):
    weights = np.arange(1, length + 1, dtype=float)
    return series.rolling(length).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )
```

**Solusi:** Gunakan `.rolling().apply()` tapi dengan fungsi numpy yang sudah di-compile, atau gunakan implementasi EMA-based yang setara. Lebih baik lagi, pindahkan ke `indicators.py` sebagai fungsi vectorized.

**Perbaikan:**
```python
def _wma_vectorized(series: pd.Series, length: int) -> pd.Series:
    """Vectorized Weighted Moving Average using numpy convolution."""
    weights = np.arange(1, length + 1, dtype=float)
    weights_sum = weights.sum()
    
    # Use numpy convolve for O(n) vectorized WMA
    values = series.values
    padded = np.pad(values, (length - 1, 0), mode='constant', constant_values=np.nan)
    wma_vals = np.convolve(padded, weights[::-1], mode='valid') / weights_sum
    wma_vals[:length-1] = np.nan  # First (length-1) values are not valid
    
    return pd.Series(wma_vals, index=series.index)
```

---

### 🟡 1d. `screener.py` baris 1460 — `.apply()` pada rolling window CCI

**Temuan:** `sma_tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)` — lagi-lagi rolling apply.

**File:** `screener.py:1460`
```python
tp_mean_dev = sma_tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
```

**Solusi:** Gunakan fungsi yang sudah ada: `sma_tp.rolling(20).mad()` (Mean Absolute Deviation) — pandas built-in, sepenuhnya vectorized.

**Perbaikan:**
```python
tp_mean_dev = sma_tp.rolling(20).mad()  # Built-in MAD, vectorized
```

---

### 🟠 1e. `screener.py` baris 1049-1079 — Loop for article in news list

**Temuan:** Loop Python manual untuk scoring sentimen. DataFrame atau numpy array bisa lebih cepat.

**File:** `screener.py:1068-1079`
```python
sentiment_scores = []
for article in news[:10]:
    # ... title extraction ...
    if title and len(title) > 5:
        sentiment_scores.append(_score_title(title))
```

**Perbaikan:**
```python
titles = []
for article in news[:10]:
    if isinstance(article, dict):
        title = article.get('content', {}).get("title", "") if isinstance(article.get('content'), dict) else article.get("title", "")
    else:
        title = str(article)
    titles.append(title)

sentiment_scores = [_score_title(t) for t in titles if len(t) > 5]  # List comprehension
```
*(Masih ada, tapi list comprehension lebih cepat dari append loop manual)*

---

## 2. EDGE CASES & ERROR HANDLING

### 🔴 2a. `screener.py` baris 1299-1307 — Validasi Suspended Stocks tidak mendeteksi harga flat

**Temuan:** `validasi_data_yfinance()` hanya cek `df['Volume'].tail(10).sum() == 0`. Saham suspended biasanya juga punya harga flat (Close sama persis setiap hari). Tidak ada validasi harga flat.

**File:** `screener.py:1304-1307`
```python
# 1. Deteksi Saham Tidur (Suspend / Gocap)
# Jika volume 10 hari terakhir 0 terus, lewati.
if df['Volume'].tail(10).sum() == 0:
    return False
```

**Perbaikan:** Tambahkan deteksi harga flat:
```python
def validasi_data_yfinance(df: pd.DataFrame, ticker: str) -> bool:
    if df.empty or len(df) < 50:
        return False

    # 1. Deteksi Saham Suspend: Volume 0 terus-menerus
    if df['Volume'].tail(10).sum() == 0:
        logger.warning("[SANITIZER] %s dilewati — volume 0 selama 10 hari (mungkin suspend)", ticker)
        return False

    # 2. NEW: Deteksi harga flat (suspend / gocap)
    price_range_10d = (df['Close'].tail(10).max() - df['Close'].tail(10).min())
    if price_range_10d == 0:
        logger.warning("[SANITIZER] %s dilewati — harga flat selama 10 hari", ticker)
        return False

    # 3. Deteksi Anomali Stock Split / Right Issue (loncat > 40% dalam 1 hari)
    pct_change = df['Close'].tail(5).pct_change().abs() * 100
    anomali = pct_change[pct_change > 40.0]
    if not anomali.empty:
        logger.warning("[SANITIZER] %s dilewati — anomali data ekstrem (loncat >40%% dalam 1 hari)", ticker)
        return False

    return True
```

---

### 🔴 2b. `screener.py` baris 665 — Division by Zero di `ease_of_movement`

**Temuan:** `box_ratio = (volume / 100000000) / ((high - low).replace(0, 0.0001))`. Jika `high == low` (harga flat intraday), denominator `high - low` diganti `0.0001`, tapi setelah dividing hasilnya bisa tetap `inf` jika volume besar. Juga `distance` di numerator bisa NaN jika shift(1) menghasilkan NaN di baris pertama.

**File:** `screener.py:663-667`
```python
def ease_of_movement(high, low, volume, window=14):
    distance = ((high + low) / 2) - ((high.shift(1) + low.shift(1)) / 2)
    box_ratio = (volume / 100000000) / ((high - low).replace(0, 0.0001))
    emv = distance / box_ratio
    return emv.rolling(window).mean()
```

**Perbaikan:**
```python
def ease_of_movement(high, low, volume, window=14):
    distance = ((high + low) / 2) - ((high.shift(1) + low.shift(1)) / 2)
    # Guard division by zero: replace inf after division
    price_range = (high - low).clip(lower=0.0001)  # Minimum 0.0001
    box_ratio = (volume / 100000000) / price_range
    box_ratio = box_ratio.replace([np.inf, -np.inf], np.nan).fillna(0)
    
    emv = distance / box_ratio.replace(0, np.nan)  # Avoid division by zero in emv
    emv = emv.replace([np.inf, -np.inf], np.nan).fillna(0)
    return emv.rolling(window).mean().fillna(0)
```

---

### 🔴 2c. `screener.py` baris 669-673 — Division by Zero di `volume_oscillator`

**Temuan:** `long_ma.replace(0, np.nan)` sudah ada. Tapi jika semua volume = 0 (saham tidur yang lolos validasi), `long_ma` = 0 semua, diganti NaN semua, hasilnya NaN semua. Tidak ada fallback.

**File:** `screener.py:669-673`
```python
def volume_oscillator(volume, short_window=5, long_window=10):
    short_ma = volume.rolling(short_window).mean()
    long_ma = volume.rolling(long_window).mean()
    return ((short_ma - long_ma) / long_ma.replace(0, np.nan)) * 100
```

**Perbaikan:**
```python
def volume_oscillator(volume, short_window=5, long_window=10):
    short_ma = volume.rolling(short_window).mean()
    long_ma = volume.rolling(long_window).mean()
    # Guard: jika long_ma mendekati 0, fallback ke 0
    result = ((short_ma - long_ma) / long_ma.replace(0, np.nan)) * 100
    return result.fillna(0).replace([np.inf, -np.inf], 0)
```

---

### 🟠 2d. `screener.py` baris 673 dan 665 — NaN propagation ke indikator lain

**Temuan:** Jika salah satu indikator menghasilkan NaN/inf, nilai tersebut merambat ke scoring dan AI prediksi. Tidak ada centralized NaN guard untuk semua indikator.

**File:** `screener.py:1432-1446` (nilai indikator di-extract tanpa NaN check)
```python
adx_v = float(adx_val.iloc[-1])
macd_v = float(macd_line.iloc[-1])
# ... etc ...
```

**Perbaikan:** Buat helper untuk safe extraction:
```python
def _safe_float_value(series: pd.Series, default=0.0) -> float:
    """Extract last value from series with NaN guard."""
    if series is None or series.empty:
        return default
    val = series.iloc[-1]
    if pd.isna(val) or np.isinf(val):
        return default
    return float(val)

# Then use:
adx_v = _safe_float_value(adx_val)
macd_v = _safe_float_value(macd_line)
macd_s = _safe_float_value(macd_signal)
macd_h = _safe_float_value(macd_hist)
rsi_v = _safe_float_value(rsi)
# ... etc for all 20+ indicators
```

---

### 🟠 2e. `data_fetcher.py` baris 81-83 — Stock split/right issue tidak terdeteksi

**Temuan:** Fungsi `fetch_price_data_sync()` tidak melakukan validasi lonjakan harga. Hanya `screener.py` di `validasi_data_yfinance()` yang cek lonjakan >40%. Tapi jika ada stock split yang sudah di-adjust oleh YFinance (harga turun drastis dalam 1 hari), data volume lama jadi tidak konsisten.

**File:** `data_fetcher.py:81-108`
```python
data = tkr.history(period=period, interval=interval)
# ... hanya basic cleaning, no anomaly detection
```

**Perbaikan:** Tambahkan validasi di `fetch_price_data_sync()`:
```python
# Di dalam fetch_price_data_sync(), setelah data di-download:
if not data.empty and len(data) > 5:
    # Deteksi potensi corporate action: pre-close vs open different > 50%
    pct_changes = data['Close'].pct_change().abs() * 100
    extreme_changes = pct_changes[pct_changes > 50]
    if not extreme_changes.empty:
        logger.warning("[YFinance] %s — kemungkinan corporate action terdeteksi (pct_change > 50%% pada %s)", 
                      ticker, extreme_changes.index[0].strftime('%Y-%m-%d'))
        # Flag in data for downstream consumers
        data.attrs['corporate_action'] = True
```

---

### 🟠 2f. `screener.py` baris 1416 — fetch fundamental dan sentiment dilakukan 2 kali

**Temuan:** Baris 1375-1376 sudah fetch fundamentals & sentiment. Tapi di baris 1416 ada komentar "FIX BUG 6: fundamentals & sentiment di-fetch dua kali — hapus duplikat ini" — tapi kode duplikatnya masih ada? (Lihat baris 1375-1376 dan sekitar 1538).

```python
# Baris 1375-1376:
fundamentals = fetch_fundamental_metrics(ticker)
sentiment = fetch_news_sentiment(ticker)

# Tapi di baris 1416 sudah ada komentar tentang bug ini
# Redundant: fetch_berita_lokal(ticker) dipanggil di 1398 terpisah dari sentiment
```

**Perbaikan:** Hapus redudansi — pastikan `fetch_fundamental_metrics()` dan `fetch_news_sentiment()` hanya dipanggil SEKALI, dan hasilnya di-share ke semua komponen scoring.

---

## 3. DUPLICATED CODE

### 🔴 3a. `scoring_engine.py` vs `screener.py`: Duplikasi Logika Scoring (compute_confidence vs confidence)

**Temuan:** Ada **dua sistem scoring yang berbeda**:
1. **`scoring_engine.py:30-72`** — `compute_confidence()` dengan adaptive weights
2. **`screener.py:1701-1804`** — Logika confidence yang hampir identik di-*reimplement*

Keduanya menggunakan:
- `get_adaptive_weights(adx_v)` 
- `_normalize_score()`
- IHSG penalty (`if IHSG_CHANGE < -1.0: confidence -= 8`)
- Multi-timeframe bonus (`weekly_bullish and monthly_bullish: confidence += 5`)

Tapi `screener.py` TIDAK menggunakan `compute_confidence()` dari `scoring_engine.py` — malah mengimpor `get_adaptive_weights` dan `_normalize_score` lalu melakukan perhitungan manual.

**File:** `screener.py:1772-1784`
```python
from scoring_engine import get_adaptive_weights, _normalize_score
w_tech, w_fund, w_rs, w_sent = get_adaptive_weights(adx_v)
n_tech = _normalize_score(tech_score, 65)
# ... manual weighted calculation ...
confidence = (n_tech * w_tech + max(0, n_fund) * w_fund + n_rs * w_rs + max(0, n_sent) * w_sent)
```

**Perbaikan:** Panggil `compute_confidence()` dari `scoring_engine.py` — single source of truth:
```python
from scoring_engine import compute_confidence, get_calibrated_win_prob, get_signal

# Replace the manual block (lines 1772-1804) with:
confidence, skor, c_thresh_buy = compute_confidence(
    tech_score=tech_score, fund_score=fund_score,
    rs_score=rs_score, sent_score=sent_score,
    adx_val=adx_v, ihsg_change=IHSG_CHANGE, ihsg_trend=IHSG_TREND,
    weekly_bullish=weekly_bullish, monthly_bullish=monthly_bullish,
    pct_above_ema50=50.0
)
```

Dan hapus duplicated logic dari `screener.py` (baris 1701-1804).

---

### 🔴 3b. `shareholder_analyzer.py` vs `build_cache.py` vs `build_cache_v2.py`: Triple Duplikasi Parsing PDF

**Temuan:** Ada **TIGA implementasi** parsing PDF KSEI yang berbeda:
1. `shareholder_analyzer.py:80-284` — `_parse_all_pdfs()` + `_parse_single_pdf()`
2. `build_cache.py:25-80` — `parse_pdf()` di script standalone
3. `build_cache_v2.py:14-61` — `parse()` di script standalone v2

Ketiganya melakukan hal yang sama: extract tables dari PDF, cari format SHARE_CODE atau KODE EFEK, klasifikasikan shareholder. TAPI dengan:
- Nama fungsi berbeda
- KSEI_CLASS_MAP didefinisikan ulang (build_cache.py:9-22 vs shareholder_analyzer.py:20-38)
- Logic klasifikasi sedikit berbeda (build_cache_v2.py:39 menggunakan `len(nm.split())<=3` untuk deteksi retail, shareholder_analyzer.py lebih kompleks)
- Error handling berbeda

**Perbaikan:** Refactor ke satu modul `ksei_parser.py` yang di-share:
```python
# NEW: ksei_parser.py — Single source of truth for KSEI PDF/CSV parsing
def parse_pdf(fpath, date_extract_only=False) -> pd.DataFrame: ...
def parse_csv(fpath) -> pd.DataFrame: ...
def classify_shareholder(name, category="", pct=0.0) -> str: ...
def build_and_save_cache(pdf_dir, csv_dir, cache_path) -> int: ...

# shareholder_analyzer.py imports from here
# build_cache.py imports from here (CLI wrapper)
# build_cache_v2.py — DELETE
```

---

### 🟠 3c. `screener.py` (joblib) vs `data_fetcher.py` (pickle): Dual Caching

**Temuan:** Dua sistem cache berbeda:
1. **`data_fetcher.py:32-59`** — Cache per data fetch menggunakan `pd.read_pickle()` dan `data.to_pickle()`, expire 4 jam
2. **`screener.py:304-323`** — Cache per analysis menggunakan `joblib.load()` dan `joblib.dump()`

Keduanya menggunakan `CACHE_DIR` dan format berbeda (pickle vs joblib). `screener.py` punya `get_cache_key()` dengan timestamp, `data_fetcher.py` tanpa timestamp.

**File:** 
- `data_fetcher.py:19-21`: `get_cache_key()` = `f"{ticker}_{period}_{interval}"`
- `screener.py:284-285`: `get_cache_key()` = `f"{ticker}_{period}_{interval}_{date}"`
- `screener.py:280`: `USE_CACHE = True`
- `data_fetcher.py:16`: `USE_CACHE = True`

**Perbaikan:**
1. Buat satu modul `cache_manager.py`:
   - Gunakan satu format (pickle atau parquet — lebih aman untuk DataFrame besar)
   - Implementasikan TTL-based dan date-based caching
   - `screener.py` dan `data_fetcher.py` panggil dari sini

```python
# NEW: cache_manager.py
CACHE_DIR = "cache"
CACHE_MAX_AGE_HOURS = 4

def get_cache_path(ticker, period, interval, include_date=True):
    key = f"{ticker}_{period}_{interval}"
    if include_date:
        key += f"_{datetime.date.today().isoformat()}"
    return os.path.join(CACHE_DIR, f"{key}.parquet")

def load_from_cache(ticker, period, interval, include_date=True):
    path = get_cache_path(ticker, period, interval, include_date)
    if not os.path.exists(path):
        return None
    if include_date:  # Date-based: always fresh for today
        return pd.read_parquet(path)
    else:  # TTL-based: check age
        age_hours = (time.time() - os.path.getmtime(path)) / 3600
        if age_hours < CACHE_MAX_AGE_HOURS:
            return pd.read_parquet(path)
    return None

def save_to_cache(ticker, period, interval, data, include_date=True):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = get_cache_path(ticker, period, interval, include_date)
    data.to_parquet(path)
```

---

## 4. IMPORT & DEPENDENCY

### 🔴 4a. `from indicators import *` — Wildcard Import Berbahaya

**Temuan:** Baris 43 `from indicators import *` mengimpor SEMUA nama publik dari `indicators.py` tanpa kontrol. Jika `indicators.py` nanti menambah fungsi baru (misalnya fungsi helper yang bentrok dengan nama di screener.py), silent override terjadi.

**File:** `screener.py:43`
```python
from indicators import *
```

**Perbaikan:** Import eksplisit:
```python
from indicators import (
    calculate_sma, calculate_ema, calculate_rsi, calculate_macd,
    calculate_adx, calculate_bollinger_bands, calculate_atr,
    calculate_obv, calculate_vwap, hma, detect_support_resistance
)
```

---

### 🔴 4b. Missing Modules — 9 dari 10 modul tidak ditemukan

**Temuan:** File berikut **tidak ada** di `C:\Hermes_Workspace\Screener\`:
| Import | Baris | Status |
|--------|-------|--------|
| `security.py` | 42, 46, 132 | ❌ MISSING |
| `ai_model.py` | 44, 1929 | ❌ MISSING |
| `nlp_scraper.py` | 140, 1042 | ❌ MISSING |
| `broker_scraper.py` | 49, 1395 | ❌ MISSING |
| `foreign_flow.py` | 50 | ❌ MISSING |
| `mean_reversion.py` | 51, 1967 | ❌ MISSING |
| `monte_carlo.py` | 52, 1961 | ❌ MISSING |
| `trade_journal.py` | 53, 1974 | ❌ MISSING |
| `performance.py` | 47 | ❌ MISSING |

Hanya **`backtest.py`** yang ditemukan.

**Dampak:**
- `screener.py` akan **crash saat startup** karena semua modul di-import di module level (baris 43-53)
- Satu-satunya yang diselamatkan: `security` hanya dipakai setelah fallback, tapi import `get_env_var` di baris 132 akan gagal
- `from security import *` di baris 46 akan gagal → `ModuleNotFoundError`
- `get_ai_model` dari `ai_model.py` di baris 1929 hanya dipanggil saat ensemble model tidak ada, tapi import di baris 44 akan gagal duluan

**Perbaikan:** Gunakan lazy import untuk SEMUA modul yang mungkin tidak ada:
```python
# Module-level: hanya import yang pasti ada
from indicators import (
    calculate_sma, calculate_ema, calculate_rsi, calculate_macd,
    calculate_adx, calculate_bollinger_bands, calculate_atr,
    calculate_obv, calculate_vwap, hma, detect_support_resistance
)
from data_fetcher import fetch_macro_data, fetch_price_data_sync
import data_fetcher
from scoring_engine import compute_confidence, get_calibrated_win_prob, get_signal, get_adaptive_weights, _normalize_score

# Lazy imports — dilakukan di dalam fungsi saat pertama dipakai
# (hapus semua import module-level yang mungkin missing)
```

---

### 🟠 4c. `security.py` — missing, tapi dipakai untuk `get_env_var(DISCORD_WEBHOOK)`

**Temuan:** `from security import get_env_var` (baris 132) digunakan untuk mengambil Discord webhook. Jika file ini tidak ada, script crash.

**File:** `screener.py:131-133`
```python
# FIX: Never hardcode API keys — use .env + python-dotenv (SKILL.md safety rule)
from security import get_env_var
DISCORD_WEBHOOK = get_env_var("DISCORD_WEBHOOK", "")
```

**Perbaikan:** Implementasi fallback sederhana langsung di `screener.py`:
```python
import os

def _get_env_var(name, default=""):
    """Get env var from OS environment or .env file."""
    val = os.environ.get(name)
    if val:
        return val
    # Try loading from .env file
    try:
        from dotenv import load_dotenv
        load_dotenv()
        val = os.environ.get(name)
        if val:
            return val
    except ImportError:
        pass
    return default

DISCORD_WEBHOOK = _get_env_var("DISCORD_WEBHOOK", "")
```

---

## 5. KUALITAS KODE

### 🟡 5a. Komentar Nyasar — Tidak Relevan/Obsolete

**Temuan:** Banyak komentar "FIX BUG 1, 2, 3, 4, 5, 6" yang sudah diperbaiki tapi komentarnya masih ada — menimbulkan kebingungan.

**File:** 
- `screener.py:39` — `# FIX BUG 1: pandas harus diimport dulu sebelum dipakai di bawah`
- `screener.py:129` — `# FIX BUG 4: IHSG_TREND tidak pernah didefinisikan — tambahkan default di sini`
- `screener.py:212` — `# FIX BUG 2: yf (yfinance) dipakai di bawah tapi tidak pernah diimport`
- `screener.py:1361` — `# FIX BUG 3: stoch_k & stoch_d dipakai tapi tidak pernah dihitung`
- `screener.py:1416` — `# FIX BUG 6: fundamentals & sentiment di-fetch dua kali — hapus duplikat ini`

**Semua bug ini sudah diperbaiki. Komentar harus dihapus.**

---

### 🟡 5b. Marker Komentar Instruksional — Tidak Profesional

**Temuan:** Komentar seperti instruksi untuk diri sendiri atau pengembang sebelumnya.

**File:**
- `screener.py:1233` — `# 👇 TAMBAHKAN 3 BARIS INI 👇` ... `# 👆 --------------------- 👆`
- `screener.py:1344` — `# 🔥 Tambahan untuk Z-Score Anomaly:`
- `screener.py:1350` — `# 🔥 Update Sensor Market Breadth`
- `screener.py:1392` — `# 🔥 Taruh Sensor Asing di sini`
- `screener.py:1396` — `# 🔥 Panggil Senjata Baru v9.0`
- `screener.py:1956-1957` — `# 🔥 Hitung Manajemen Risiko DI SINI (setelah AI selesai mikir)` (dua baris identik)
- `screener.py:2191` — `# ← TAMBAHKAN INI`
- `screener.py:2505` — `# 🟢 1. TAMBAHKAN PENGHITUNG "TUNGGU" DI SINI`
- `screener.py:2521` — `# 🟢 2. TAMBAHKAN TAMPILAN "TUNGGU" DI SINI`
- `screener.py:2699` — `# 🔥 BONUS: Tambahkan "-s" untuk mode pemindaian 1 sektor spesifik`

---

### 🟡 5c. Nama Fungsi Campur Aduk (Inggris + Indonesia)

**Temuan:** Fungsi dan variabel bergantian antara Bahasa Inggris dan Indonesia dalam satu file:

| Line | Function Name | Language |
|------|--------------|----------|
| 334 | `fetch_berita_lokal` | Indonesia |
| 360 | `detect_zscore_anomaly` | Inggris |
| 368 | `hitung_kelly_sizing` | Indonesia |
| 550 | `compute_sector_momentum` | Inggris |
| 647 | `volume_analysis` | Inggris |
| 1228 | `kirim_notifikasi_discord` | Indonesia |
| 1299 | `validasi_data_yfinance` | Indonesia |
| 1322 | `analisis_saham` | Indonesia |
| 2171 | `jalankan_screener` | Indonesia |

**Juga:**
- `MACRO_PENALTY` vs `IHSG_CHANGE` — satu Indonesia, satu Inggris
- `SEKTOR_MOMENTUM` vs `SEMUA_TICKER` — campuran

---

### 🟡 5d. Global Variables Bertebaran

**Temuan:** Banyak global variables yang di-modify di berbagai fungsi — rawan race condition (terutama dengan ThreadPoolExecutor).

```python
# screener.py:57-58, 120-130
global_total_discan = 0
global_saham_uptrend = 0
IHSG_CHANGE = 0.0
SP500_CHANGE = 0.0
USD_CHANGE = 0.0
BRENT_CHANGE = 0.0
GOLD_CHANGE = 0.0
COAL_CHANGE = 0.0
MACRO_PENALTY = 0.0
IHSG_TREND = "UP"
SEKTOR_MOMENTUM = {}
```

`global_total_discan` dan `global_saham_uptrend` di-update di `analisis_saham()` (baris 1351-1354) yang dipanggil via ThreadPoolExecutor — race condition meskipun sudah pakai lock (untuk counter ini). Tapi variabel lain seperti `MACRO_PENALTY`, `IHSG_CHANGE`, dll di-update di `update_macro_globals()` — tidak ada sinkronisasi.

**Perbaikan:**
1. Bungkus dalam class `MacroContext` atau `ScreenerContext`
2. Gunakan `threading.Lock()` untuk akses baca/tulis
3. Pindahkan ke module-level singleton pattern

```python
class MacroContext:
    """Thread-safe macro data container."""
    _lock = threading.Lock()
    _instance = None
    
    def __init__(self):
        self.ihsg_change = 0.0
        self.sp500_change = 0.0
        self.macro_penalty = 0.0
        self.ihsg_trend = "UP"
        # ... etc
    
    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def update(self, macro_data: dict):
        with self._lock:
            # ... update all fields

# Usage:
macro = MacroContext.get()
penalty = macro.macro_penalty
```

---

### 🟡 5e. Docstring Bombastis

**Temuan:** Module docstring (baris 16-30) berisi klaim berlebihan:

```python
"""
============================================================
  IHSG STOCK SCREENER v10.0 - The Profit Maximizer
  ⭐⭐⭐ COMPLETE MARKET PREDICTION SYSTEM WITH AI:

  TIER 1 - ADVANCED INDICATORS (18 total)
  TIER 2 - MACHINE LEARNING INTEGRATION (Random Forest)
  TIER 3 - MARKET MAKER ANALYSIS
  ...
  TIER 8 - Liquid Neural Network + Mixture of Experts + Pipe Data 3D
  TIER 9 - Intermarket Analysis + Z-Score Detection + Local News + Foreignflow + Time Encoding + Global Commodities Sensor
============================================================
"""
```

Banyak tier yang belum diimplementasi:
- "Liquid Neural Network" — tidak ada implementasi
- "Mixture of Experts" — tidak ada
- "Pipe Data 3D" — tidak ada
- "Global Macro Sensor" — hanya macro data dasar
- "Virtual Hedge Fund" — hany paper trading sederhana di SQLite

**Perbaikan:** Ganti dengan docstring yang akurat dan profesional.

---

## RINGKASAN PRIORITAS

| # | Kategori | Severity | Baris | Deskripsi |
|---|----------|----------|-------|-----------|
| 1 | Import | 🔴 CRITICAL | 43-53 | 9 missing modules → crash on startup |
| 2 | Duplikasi | 🔴 CRITICAL | scor_engine + screener | Dual scoring logic, perlu single source of truth |
| 3 | Duplikasi | 🔴 CRITICAL | shareholder + 2x build_cache | Triple duplikasi parsing PDF |
| 4 | Edge Case | 🔴 CRITICAL | 1299-1307 | Suspended stock: tidak deteksi harga flat |
| 5 | Edge Case | 🔴 CRITICAL | 663-673 | Division by zero di EMV & volume_oscillator |
| 6 | Pandas | 🔴 CRITICAL | 457-460 | .apply(lambda) untuk jutaan baris — bisa di-vectorized |
| 7 | Pandas | 🟠 MAJOR | 131-168 | iterrows() untuk parsing CSV — 10x lebih lambat |
| 8 | Pandas | 🟠 MAJOR | 620-626 | HMA rolling apply — O(n²) complexity |
| 9 | Edge Case | 🟠 MAJOR | 1432-1446 | NaN propagation dari indikator ke scoring |
| 10 | Edge Case | 🟠 MAJOR | 81-108 | Stock split/right issue tidak terdeteksi di data_fetcher |
| 11 | Caching | 🟠 MAJOR | screener+data_fetcher | Dual cache system (joblib + pickle) tidak konsisten |
| 12 | Pandas | 🟡 MINOR | 1460 | rolling().apply(lambda) untuk CCI → ganti .mad() |
| 13 | Import | 🟡 MINOR | 43 | Wildcard import `from indicators import *` |
| 14 | Kualitas | 🟡 MINOR | 39,129,212,... | Komentar "FIX BUG 1-6" usang |
| 15 | Kualitas | 🟡 MINOR | 1233,1344,... | Marker komentar 🔥 👇 instruksional |
| 16 | Kualitas | 🟡 MINOR | various | Nama campur aduk Ing-Idn |
| 17 | Kualitas | 🟡 MINOR | 120-130 | Global variables tidak thread-safe |
| 18 | Kualitas | 🟡 MINOR | 16-30 | Docstring bombastis, tidak akurat |

---

## REKOMENDASI ARSITEKTUR (Jangka Panjang)

1. **Buat `cache_manager.py`** — single cache system dengan parquet
2. **Buat `ksei_parser.py`** — single source of truth untuk KSEI PDF/CSV parsing
3. **Refactor `screener.py`** — pisahkan concerns:
   - `indicator_engine.py` — semua kalkulasi indikator
   - `signal_generator.py` — scoring & confidence (panggil scoring_engine.py)
   - `screener.py` — orchestrator saja
4. **Hapus** `build_cache_v2.py` (redundan dengan `build_cache.py`)
5. **Implementasi lazy loading** untuk semua modul opsional
6. **Ganti global variables** dengan Singleton context atau dependency injection

---

*End of Report*
