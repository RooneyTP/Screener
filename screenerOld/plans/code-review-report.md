# Code Review Report — Proyek Screener (IHSG) & Telegram Bot

## Ringkasan

Dua proyek utama dievaluasi:
1. **screener.py** (2742 baris) — Engine screening saham IHSG dengan 18+ indikator, AI ensemble, MM analysis
2. **telegram_bot.py** (1417 baris) — Bot Telegram interaktif v6.1
3. **scoring_engine.py** (126 baris) — Shared scoring engine v11
4. **indicators.py** (50 baris) — Wrapper indikator teknikal via `ta` library

---

## 1. BUG KRITIS — Index Shifting & Data Integrity

### 1.1 `screener.py` baris 1810 — Price Change Index Shift

```python
daily_change_pct = round((price - float(close.iloc[-2])) / float(close.iloc[-2]) * 100, 1)
```

**Masalah:** `price` adalah `float(close.iloc[-1])` (baris 1399). Perhitungan `daily_change_pct` menggunakan `close.iloc[-2]` (H-1). Tapi `abs_change` di baris 1811 digunakan untuk deteksi ARB/ARA (threshold ±20%). Jika data yfinance memiliki gap (weekend/holiday), `close.iloc[-2]` bisa jadi H-3 atau H-4, bukan H-1. Ini menyebabkan false positive ARB/ARA.

**Rekomendasi:** Gunakan `close.pct_change().iloc[-1]` yang sudah handle NaN secara otomatis:
```python
daily_change_pct = round(float(close.pct_change().iloc[-1] * 100), 1) if len(close) >= 2 else 0
```

### 1.2 `telegram_bot.py` baris 282-284 — Price Change Loop Tidak Efisien

```python
prev_close = last_close
for i in range(2, min(15, len(close))+1):
    if float(close.iloc[-i]) != last_close: prev_close = float(close.iloc[-i]); break
```

**Masalah:** Loop mencari harga berbeda dari `last_close`. Jika saham tidak bergerak beberapa hari (tidur), loop terus sampai 15 iterasi. Ini tidak efisien dan bisa menghasilkan `change_pct` yang salah.

**Rekomendasi:** Gunakan vectorized:
```python
change_pct = round(float(close.pct_change().iloc[-1] * 100), 2)
```

### 1.3 `screener.py` baris 1710 — MACD Histogram Comparison Bug

```python
if macd_h > 0 and macd_h > macd_hist.iloc[-2] if len(macd_hist) > 1 else False:
```

**Masalah:** Operator precedence salah. Python mengeksekusi sebagai:
```python
if macd_h > 0 and (macd_h > macd_hist.iloc[-2] if len(macd_hist) > 1 else False):
```
Ini sebenarnya bekerja, tapi sangat sulit dibaca dan rawan salah edit.

**Rekomendasi:**
```python
if macd_h > 0 and len(macd_hist) > 1 and macd_h > macd_hist.iloc[-2]:
```

---

## 2. BUG LOGIKA — Scoring & Signal Threshold

### 2.1 `screener.py` baris 1861-1866 — STRONG_BUY Tanpa Filter ADX

```python
elif confidence >= confidence_threshold_strong and skor >= 8.0:
    sinyal = "STRONG_BUY"  # B+
elif confidence >= 70 and skor >= 7.0:
    sinyal = "STRONG_BUY"  # B
```

**Masalah:** Tidak ada pengecekan ADX atau RRR. Saham dengan ADX < 20 (RANGING) bisa masuk STRONG_BUY. Data membuktikan: INTP (ADX=16.8), CFIN (ADX=17.4), BJTM (ADX=14.4) masuk STRONG_BUY.

**Rekomendasi:** Tambah filter ADX ≥ 25 untuk STRONG_BUY:
```python
elif confidence >= confidence_threshold_strong and skor >= 8.0 and adx_v >= 25:
    sinyal = "STRONG_BUY"; signal_strength = "B+"
elif confidence >= 70 and skor >= 7.0 and adx_v >= 20:
    sinyal = "STRONG_BUY"; signal_strength = "B"
```

### 2.2 `scoring_engine.py` baris 75-91 — Dead Code Shareholder Bonus

```python
# Task: Shareholder structure bonus
try:
    from shareholder_analyzer import get_scoring_bonus
    share_bonus, share_reason = get_scoring_bonus("")  # stub
except Exception:
    share_bonus, share_reason = 0, ""
...
try:
    from shareholder_analyzer import get_scoring_bonus
    share_bonus, _ = get_scoring_bonus("")
    if share_bonus:
        confidence += share_bonus
except Exception:
    pass
```

**Masalah:** Kode ini **DEAD CODE** — dipanggil dengan `ticker=""` (string kosong), sehingga `analyze_shareholder_structure("")` selalu return `no_data`. Bonus tidak pernah aktif. Tapi kode tetap dieksekusi setiap scoring, membuang CPU time.

**Rekomendasi:** Hapus blok ini (75-91). Shareholder bonus sudah di-handle di `telegram_bot.py` dan sudah dinonaktifkan.

### 2.3 `screener.py` baris 1824-1829 — Safety Filter Dimatikan

```python
# Safety filter: DIMATIKAN SEMENTARA (Karena data Volume/Turnover YF sering ngaco)
has_critical_risk = (
    per_val < -50 or
    pbv_val > 50 or
    fundamentals.get("bankruptcy_risk", 0) > 0.5   
)
```

**Masalah:** Filter `per_val < -50` dan `pbv_val > 50` hampir tidak pernah terpenuhi untuk saham IHSG normal. Tapi komentar mengatakan "DIMATIKAN SEMENTARA" — ini sudah berbulan-bulan. Saham dengan fundamental buruk tetap masuk scoring.

**Rekomendasi:** Aktifkan filter yang lebih ketat:
```python
has_critical_risk = (
    per_val < 0 or                    # EPS negatif
    pbv_val > 20 or                   # PBV terlalu tinggi
    fundamentals.get("bankruptcy_risk", 0) > 0.3
)
```

---

## 3. OPTIMASI PANDAS/NUMPY — Vectorization

### 3.1 `screener.py` baris 1495-1498 — Loop Korelasi Tidak Efisien

```python
corr_val = float(np.corrcoef(
    stock_ret.values[-min_len:],
    ihsg_ret.values[-min_len:]
)[0, 1])
```

**Masalah:** `np.corrcoef` menghitung matriks 2x2 penuh, tapi hanya elemen [0,1] yang dipakai. Untuk 60 data point, ini fine. Tapi dipanggil 171 kali (per ticker), jadi 171 × matriks 2x2.

**Rekomendasi:** Gunakan `pd.Series.corr()` yang lebih efisien:
```python
corr_val = stock_ret.tail(min_len).corr(ihsg_ret.tail(min_len))
```

### 3.2 `telegram_bot.py` baris 282-284 — Loop Manual untuk Price Change

Sudah dibahas di 1.2. Gunakan `pct_change()`.

### 3.3 `screener.py` baris 1544-1545 — Turnover Calculation

```python
turnover_harian = close * volume
avg_turnover = float(turnover_harian.tail(20).mean())
```

**Optimasi:** Ini sudah vectorized ✅. Tapi `avg_turnover` hanya dipakai di baris 1544-1545, tidak digunakan lagi setelahnya. **Dead code.**

---

## 4. ASYNCHRONOUS & BOT HANDLING

### 4.1 `telegram_bot.py` — Rate Limiting Hanya Per-User

**Masalah:** `_check_rate_limit()` membatasi per-user (3 request/10 detik), tapi tidak membatasi global. Jika 10 user mengirim command bersamaan, semua request diproses. `_rate_semaphore` (Semaphore(5)) sudah ada tapi **tidak digunakan** di mana pun.

**Rekomendasi:** Gunakan `_rate_semaphore` sebagai context manager di setiap command handler:
```python
async def cmd_cek(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with _rate_semaphore:
        if not await _check_rate_limit(update.effective_user.id):
            ...
```

### 4.2 `telegram_bot.py` — Memory Leak di `_indicator_cache`

**Masalah:** `_indicator_cache` terus bertambah tanpa batas. Setiap ticker baru ditambahkan, tidak pernah dihapus. Bot berjalan 24/7 → cache bisa membesar hingga ribuan entry.

**Rekomendasi:** Batasi ukuran cache (LRU):
```python
from collections import OrderedDict

class LRUCache:
    def __init__(self, maxsize=200):
        self.cache = OrderedDict()
        self.maxsize = maxsize
    
    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = value
        self.cache.move_to_end(key)
        if len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)

_indicator_cache = LRUCache(maxsize=200)
```

### 4.3 `telegram_bot.py` — Thread Safety `_user_rate`

**Masalah:** `_user_rate` diakses dari multiple async task tanpa lock. Dua request dari user yang sama di waktu bersamaan bisa menyebabkan race condition.

**Rekomendasi:** Gunakan `asyncio.Lock()`:
```python
_user_rate_lock = asyncio.Lock()

async def _check_rate_limit(user_id: int) -> bool:
    async with _user_rate_lock:
        ...
```

### 4.4 `telegram_bot.py` — `_sync_csv_to_db()` Tidak Thread-Safe

**Masalah:** `_sync_csv_to_db()` dipanggil dari watchdog thread dan juga dari main thread saat startup. `_last_synced_csv` diakses tanpa lock.

**Rekomendasi:** Gunakan `threading.Lock()`:
```python
_sync_lock = threading.Lock()

def _sync_csv_to_db():
    with _sync_lock:
        ...
```

---

## 5. BUG MINOR & CODE SMELLS

### 5.1 `screener.py` baris 1337 — `validasi_data_yfinance` Tidak Konsisten

```python
if not validasi_data_yfinance(data_daily, ticker):
    return None
```

Fungsi `validasi_data_yfinance` (baris 1300) mengembalikan `False` jika data tidak valid. Tapi di baris 1316, logger.warning dipanggil dengan `ticker` yang mungkin belum di-strip. OK.

### 5.2 `screener.py` baris 1383-1388 — Default Fundamental Tidak Realistis

```python
fundamentals["trailing_pe"] = 15.0   # default konservatif
fundamentals["book_value"] = 1.0     # proxy agar pbv_val = price/1
```

**Masalah:** Jika PE tidak tersedia, default 15 bisa sangat tidak akurat untuk saham yang rugi. PBV = price/1 bisa menghasilkan PBV 5000 untuk saham harga 5000.

**Rekomendasi:** Jangan paksakan default. Set flag `fundamentals_missing = True` dan kurangi bobot fundamental score.

### 5.3 `scoring_engine.py` baris 12 — Calibration Map Tidak Realistis

```python
CALIBRATION_MAP = {90: 0.72, 80: 0.65, 70: 0.55, 60: 0.48, 50: 0.42, 40: 0.35, 30: 0.28}
```

**Masalah:** Confidence 90 → win prob 72%. Ini berarti sistem mengklaim 90% confidence tapi hanya 72% akurat. Selisih 18% menunjukkan **overconfidence** dalam scoring.

### 5.4 `telegram_bot.py` baris 274-275 — Weekly/Monthly Fetch Skip Cache

```python
df_w = fetch_price_data_sync(tkr, period="1y", interval="1wk", skip_cache=True)
df_m = fetch_price_data_sync(tkr, period="2y", interval="1mo", skip_cache=True)
```

**Masalah:** `skip_cache=True` memaksa fetch dari yfinance setiap kali. Ini 2 request tambahan per `/cek`. Untuk 171 ticker, ini 342 request tambahan.

**Rekomendasi:** Gunakan `skip_cache=False`:
```python
df_w = fetch_price_data_sync(tkr, period="1y", interval="1wk", skip_cache=False)
df_m = fetch_price_data_sync(tkr, period="2y", interval="1mo", skip_cache=False)
```

---

## 6. PRIORITAS PERBAIKAN

| Priority | Bug | File | Dampak |
|----------|-----|------|--------|
| 🔴 P0 | STRONG_BUY tanpa filter ADX | `screener.py:1861-1866` | Sinyal palsu |
| 🔴 P0 | Dead code shareholder bonus | `scoring_engine.py:75-91` | CPU terbuang |
| 🔴 P1 | Semaphore tidak digunakan | `telegram_bot.py:65` | Rate limiting tidak efektif |
| 🟡 P2 | Memory leak indicator cache | `telegram_bot.py:46-59` | Bot crash setelah 24/7 |
| 🟡 P2 | Race condition _user_rate | `telegram_bot.py:66-78` | Rate limiting tidak akurat |
| 🟡 P2 | Weekly/monthly skip_cache=True | `telegram_bot.py:274-275` | 342 request tambahan |
| 🟢 P3 | Price change loop tidak efisien | `telegram_bot.py:282-284` | Minor |
| 🟢 P3 | MACD comparison precedence | `screener.py:1710` | Readability |
| 🟢 P3 | Default fundamental tidak realistis | `screener.py:1383-1388` | Minor |
