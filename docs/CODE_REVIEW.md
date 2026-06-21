# 🔍 CODE REVIEW: Producer-Consumer Pipeline
**Date:** 4 Mei 2026 | **Status:** ⚠️ Medium Priority Issues Found

---

## 📋 RINGKASAN EKSEKUTIF

| File | Status | Severity | Isu Utama |
|------|--------|----------|-----------|
| **1_producer_data.py** | ⚠️ Stabil | Medium | Rate limiting, error logging kurang detail |
| **2_consumer_ai.py** | ⚠️ Stabil | Medium | Race condition potensial, error handling |
| **3_consumer_r1.py** | 🔴 Rentan | **HIGH** | No error handling, risk management minimal |

---

## 🔴 FILE 3: 3_consumer_r1.py (PRIORITAS TERTINGGI)

### Bug Kritis Ditemukan:

#### 1. **❌ TIDAK ADA ERROR HANDLING DI MAIN LOOP**
```python
# SEKARANG (BERBAHAYA):
while True:
    cursor_hist.execute("SELECT...")
    # Jika execute() gagal → program crash & stop
```
**Dampak:** Program berhenti tanpa warning, trading stalled.

**Solusi:**
```python
while True:
    try:
        cursor_hist.execute("SELECT...")
        # ...
    except sqlite3.Error as e:
        log.error(f"DB error: {e}, retry dalam 5 detik...")
        time.sleep(5)
        continue
    except Exception as e:
        log.error(f"Unexpected error: {e}")
        time.sleep(5)
        continue
```

---

#### 2. **❌ TIDAK ADA VALIDASI sl & tp SEBELUM EKSEKUSI**
```python
# SEKARANG:
if sinyal in ["ULTRA_BUY", "STRONG_BUY"]:
    eksekusi_beli(ticker, harga, sl, tp)  # sl dan tp bisa None!
```
**Dampak:** Bisa insert posisi dengan SL/TP invalid → rugi tanpa stop.

**Solusi:**
```python
if sinyal in ["ULTRA_BUY", "STRONG_BUY"]:
    if harga and sl and tp and sl < tp and sl > 0:
        eksekusi_beli(ticker, harga, sl, tp)
    else:
        log.warning(f"Invalid SL/TP untuk {ticker}, skip")
```

---

#### 3. **⚠️ SHARES CALCULATION BISA MENGHASILKAN 0**
```python
# SEKARANG:
shares_to_buy = int(max_beli / harga)
if shares_to_buy >= 100 and saldo >= (shares_to_buy * harga):
    # shares_to_buy bisa 0 jika harga sangat tinggi!
```
**Dampak:** Trading tidak jadi tapi tidak ada error message.

**Solusi:**
```python
shares_to_buy = int(max_beli / harga)
if shares_to_buy < 100:
    log.warning(f"Shares {shares_to_buy} < minimum lot (100), skip")
    return
```

---

#### 4. **🔄 TIDAK ADA HANDLING UNTUK DUPLICATE SIGNALS**
```python
# Jika last_signal_id gagal di-update karena crash,
# bisa memproses sinyal yang sama 2x → beli double
```
**Solusi:** Add `processed_signals` table:
```python
CREATE TABLE IF NOT EXISTS processed_signals (
    signal_id INTEGER PRIMARY KEY,
    status TEXT,
    timestamp DATETIME
)
```

---

#### 5. **❌ DATABASE CONNECTION TIDAK DITUTUP PROPERLY**
```python
# SEKARANG:
finally:
    conn_hist.close()  # Tapi conn_portofolio di eksekusi_beli() tidak di-close!
```
**Dampak:** Connection leak, bisa cause "database locked" error.

---

#### 6. **⚠️ MISMATCH DATE TYPE**
```python
datetime.date.today()  # Tapi database schema berharap DATETIME
```
**Solusi:**
```python
datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
```

---

#### 7. **❌ TIDAK ADA POSITION MANAGEMENT / EXIT LOGIC**
- Membeli tapi tidak ada logic untuk sell/exit
- Portfolio akan terus accumulate posisi tanpa close
- Tidak bisa measure P&L

---

#### 8. **⚠️ TIDAK ADA VALIDATION SEBELUM BUY**
- Tidak check apakah harga masih valid (bisa stale data)
- Tidak check apakah saldo cukup untuk commission/fees
- Tidak check slippage protection

---

## 🟡 FILE 2: 2_consumer_ai.py (MEDIUM PRIORITY)

### Bug & Improvement Opportunities:

#### 1. **⚠️ RACE CONDITION DI SAVE_LAST_ID**
```python
# Process row:
hasil = analisis_saham(ticker)      # BISA TIMEOUT/ERROR DI SINI
last_processed_id = id_baris        # Jika timeout, row tidak pernah di-process ulang
save_last_id(cur, last_processed_id)
```
**Dampak:** Row terlewat jika timeout terjadi.

**Solusi:** Save ID hanya setelah analisis BERHASIL & hasil VALID:
```python
try:
    hasil = analisis_saham(ticker)
    if not validasi_hasil(hasil):
        log.warning(f"Invalid result, NOT marking as processed")
        continue  # Jangan update last_id
    # Process hasil...
finally:
    save_last_id(cur, id_baris)
```

---

#### 2. **⚠️ ARBITRARY LIMIT 5 DI QUERY**
```python
"SELECT id, ticker, harga FROM histori_ihsg WHERE id > ? ORDER BY id ASC LIMIT 5"
```
**Kenapa 5?** Tidak ada penjelasan. Bisa cause bottleneck atau miss data.

**Solusi:** Parametrize atau dokumentasikan:
```python
BATCH_SIZE = 5  # Process 5 data points per cycle untuk balance CPU/memory
```

---

#### 3. **⚠️ IMPORT ERROR HANDLING KURANG ROBUST**
```python
try:
    from screener import analisis_saham
except ImportError:
    raise SystemExit(1)
```
**Dampak:** Program crash jika screener.py belum ready, tidak bisa retry.

**Solusi:** 
```python
try:
    from screener import analisis_saham
    ANALYZER_READY = True
except ImportError:
    log.warning("screener.py belum tersedia, tunggu...")
    ANALYZER_READY = False

# Di main loop:
if not ANALYZER_READY:
    try:
        from screener import analisis_saham
        ANALYZER_READY = True
    except:
        time.sleep(5)
        continue
```

---

#### 4. **⚠️ TIDAK ADA RETRY LOGIC UNTUK FAILED ANALYSIS**
```python
hasil = analisis_saham(ticker)  # Gagal → skip row selamanya
```

---

#### 5. **⚠️ VALIDASI RESULT TIDAK CHECK NEGATIVE/ZERO**
```python
def validasi_hasil(hasil: dict) -> bool:
    if hasil["Stop_Loss"] >= hasil["Target_1"]:
        return False
    return True
    # Tapi tidak check jika keduanya NEGATIF atau 0!
```

---

## 🟡 FILE 1: 1_producer_data.py (MEDIUM PRIORITY)

### Bug & Improvement Opportunities:

#### 1. **⚠️ RATE LIMITING DETECTION TAPI TIDAK ADA FALLBACK**
```python
if pct < 50:
    log.warning("⚠️  Success rate < 50% — kemungkinan IP diblokir!")
    # Tapi program terus jalan dengan success rate jelek!
```
**Dampak:** Data quality menurun tanpa tindakan.

**Solusi:** Implement exponential backoff:
```python
if pct < 50:
    log.error("⚠️  IP kemungkinan blocked, exponential backoff aktif")
    CYCLE_INTERVAL *= 2  # Double wait time
    if CYCLE_INTERVAL > 300:  # Max 5 menit
        log.error("Backoff max reached, pausing producer...")
        await asyncio.sleep(300)
        CYCLE_INTERVAL = 60  # Reset
```

---

#### 2. **⚠️ GENERIC EXCEPTION HANDLING TIDAK INFORMATIF**
```python
except Exception as e:
    err = str(e)  # Apa penyebabnya? Network? JSON? Timeout?
```

**Solusi:** 
```python
except asyncio.TimeoutError:
    err = f"Timeout ({TIMEOUT_SECS}s)"
except aiohttp.ClientConnectorError:
    err = "Connection refused (IP blocked?)"
except json.JSONDecodeError:
    err = "Invalid JSON response"
except KeyError as e:
    err = f"Missing field in response: {e}"
except Exception as e:
    err = f"Unexpected: {type(e).__name__}: {e}"
```

---

#### 3. **⚠️ TIDAK ADA LOGGING UNTUK SETIAP RETRY ATTEMPT**
```python
for attempt in range(1, MAX_RETRIES + 2):
    # Jika retry terjadi, tidak ada log!
```

**Dampak:** Sulit debug jika ticker consistency issue.

**Solusi:**
```python
for attempt in range(1, MAX_RETRIES + 2):
    try:
        # ...
    except Exception as e:
        if attempt <= MAX_RETRIES:
            log.debug(f"{ticker} attempt {attempt}/{MAX_RETRIES}: {err}, retry...")
```

---

#### 4. **⚠️ HARGA 0 DI-FILTER TAPI BUKAN PADA SOURCE**
```python
valid_prices = [p for p in close_list if p is not None]
if valid_prices:
    harga = valid_prices[-1]
    if harga <= 0:
        return ticker, None  # Terlalu late, sudah insert log_error
```

**Solusi:** Filter lebih early:
```python
valid_prices = [p for p in close_list if p and p > 0]
```

---

#### 5. **⚠️ PRAGMA WAL MODE BISA CAUSE ISSUE**
```python
cur.execute("PRAGMA journal_mode=WAL;")
```

**Dampak:** Jika crash tanpa proper shutdown, WAL files tidak di-cleanup.

**Solusi:** Clear WAL files saat startup:
```python
def cleanup_wal():
    import os
    for ext in ["-wal", "-shm"]:
        if os.path.exists(DB_NAME + ext):
            try:
                os.remove(DB_NAME + ext)
                log.info(f"Cleaned {DB_NAME + ext}")
            except: pass

# Di init_db:
cleanup_wal()
cur.execute("PRAGMA journal_mode=WAL;")
```

---

#### 6. **⚠️ TICKER LIST TIDAK DIVALIDASI**
- 200+ ticker tanpa verifikasi valid
- Jika ticker tidak ada di Yahoo → gagal terus

**Solusi:** Maintain `ticker_status` table:
```python
CREATE TABLE IF NOT EXISTS ticker_status (
    ticker TEXT PRIMARY KEY,
    valid INTEGER,  -- 1=valid, 0=invalid
    last_check DATETIME
)
```

---

## 🟢 RECOMMENDATIONS (PRIORITAS IMPLEMENTASI)

### Immediate (Minggu ini):
1. ✅ **File 3:** Add error handling & validation di main loop
2. ✅ **File 3:** Implement exit/position management
3. ✅ **File 2:** Fix race condition di save_last_id

### Short-term (1-2 minggu):
4. ✅ **File 1:** Add retry logging & better error categorization
5. ✅ **File 2:** Implement retry logic untuk failed analysis
6. ✅ **File 3:** Add processed_signals tracking

### Medium-term (1 bulan):
7. ✅ Implement centralized logging untuk semua 3 file
8. ✅ Add monitoring dashboard (sinyal count, success rate, P&L)
9. ✅ Implement health check endpoints
10. ✅ Add circuit breaker pattern untuk rate limiting

### Long-term (1-3 bulan):
11. ✅ Migrate ke message queue (RabbitMQ/Redis) instead of polling
12. ✅ Implement proper position management & portfolio tracking
13. ✅ Add ML-based signal validation
14. ✅ Implement webhook untuk real trading integration

---

## 📊 SEVERITY MATRIX

```
                    LIKELIHOOD  |  IMPACT   |  PRIORITY
1. File 3 no error handling     |   HIGH    |   HIGH    |  ⚠️⚠️⚠️
2. File 3 no validation         |   MEDIUM  |   HIGH    |  ⚠️⚠️
3. File 3 no position mgmt      |   HIGH    |   HIGH    |  ⚠️⚠️⚠️
4. File 2 race condition        |   MEDIUM  |   MEDIUM  |  ⚠️
5. File 1 rate limiting         |   MEDIUM  |   MEDIUM  |  ⚠️
```

---

## 📝 QUICK FIX CHECKLIST

- [ ] Add `try-except` wrapper di File 3 main loop
- [ ] Add data validation sebelum `eksekusi_beli()`
- [ ] Add `processed_signals` table tracking
- [ ] Fix date type mismatch
- [ ] Add connection close handlers
- [ ] Document magic numbers (LIMIT 5, 20% allocation, etc)
- [ ] Add exit/position management logic
- [ ] Test dengan historical data first!

---

**Next Step:** Lihat file detail fixes yang sudah disiapkan di `CODE_REVIEW_FIXES.md`
