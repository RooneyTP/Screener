# 🛠️ DETAILED FIXES & IMPLEMENTATION GUIDE

---

## FILE 3: 3_consumer_r1.py - CRITICAL FIXES

### FIX #1: Add Error Handling & Logging

**BEFORE:**
```python
import sqlite3
import time
import datetime

def jalankan_rl_agent():
    print("🤖 RL EXECUTOR AKTIF...")
    # ... setup code ...
    while True:
        cursor_hist.execute("SELECT id, ticker, harga, sinyal, tp, sl FROM sinyal_trading WHERE id > ? LIMIT 1", (last_signal_id,))
        # No error handling!
```

**AFTER:**
```python
import sqlite3
import time
import datetime
import logging
from datetime import datetime as dt

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

def jalankan_rl_agent():
    log.info("🤖 RL EXECUTOR AKTIF: Menyiapkan agen...")
    inisialisasi_portofolio()
    
    conn_hist = sqlite3.connect(DB_NAME)
    cursor_hist = conn_hist.cursor()
    
    cursor_hist.execute("""
        CREATE TABLE IF NOT EXISTS sinyal_trading (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            harga REAL,
            sinyal TEXT,
            tp REAL,
            sl REAL,
            waktu DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn_hist.commit()
    
    last_signal_id = 0
    failed_attempts = 0
    MAX_RETRIES = 3
    
    log.info("✅ Agen RL Siap Eksekusi! Menunggu sinyal dari Otak AI...")

    try:
        while True:
            try:
                cursor_hist.execute(
                    "SELECT id, ticker, harga, sinyal, tp, sl FROM sinyal_trading WHERE id > ? LIMIT 1",
                    (last_signal_id,)
                )
                data = cursor_hist.fetchone()

                if data:
                    failed_attempts = 0
                    sig_id, ticker, harga, sinyal, tp, sl = data
                    log.info(f"📩 Sinyal Diterima: {sinyal} {ticker} di Rp{harga:,.0f}")
                    
                    # VALIDATE before execution
                    if validasi_signal(ticker, harga, sinyal, tp, sl):
                        if sinyal in ["ULTRA_BUY", "STRONG_BUY"]:
                            eksekusi_beli(ticker, harga, sl, tp)
                        last_signal_id = sig_id
                    else:
                        log.warning(f"⚠️  Signal validation failed untuk {ticker}")
                        last_signal_id = sig_id
                else:
                    log.debug("⏳ Menunggu sinyal baru...")
                    time.sleep(2)

            except sqlite3.DatabaseError as e:
                failed_attempts += 1
                log.error(f"❌ Database error (attempt {failed_attempts}/{MAX_RETRIES}): {e}")
                if failed_attempts >= MAX_RETRIES:
                    log.critical("Max retries reached, pausing for 30s...")
                    time.sleep(30)
                    failed_attempts = 0
                else:
                    time.sleep(5)
                continue
            except Exception as e:
                failed_attempts += 1
                log.error(f"❌ Unexpected error (attempt {failed_attempts}/{MAX_RETRIES}): {type(e).__name__}: {e}")
                if failed_attempts >= MAX_RETRIES:
                    time.sleep(30)
                    failed_attempts = 0
                else:
                    time.sleep(5)
                continue

    except KeyboardInterrupt:
        log.info("🛑 RL Agent dihentikan oleh user")
    finally:
        try:
            conn_hist.close()
            log.info("Database connection closed")
        except:
            pass
```

---

### FIX #2: Add Signal Validation Function

**ADD:**
```python
def validasi_signal(ticker, harga, sinyal, tp, sl) -> bool:
    """
    Validasi sebelum eksekusi trading.
    Return True jika valid, False jika ada issue.
    """
    # Check null values
    if not ticker or harga is None or sinyal is None or tp is None or sl is None:
        log.warning(f"⚠️  Null values detected: ticker={ticker}, harga={harga}, sinyal={sinyal}, tp={tp}, sl={sl}")
        return False
    
    # Check harga valid (> 0)
    if harga <= 0:
        log.warning(f"⚠️  Invalid harga: {harga} (must be > 0)")
        return False
    
    # Check SL < current price < TP (logical ordering)
    if not (sl < harga < tp):
        log.warning(f"⚠️  Invalid SL/TP ordering: SL={sl} < Price={harga} < TP={tp} is {sl < harga < tp}")
        return False
    
    # Check SL validity (must be positive)
    if sl <= 0:
        log.warning(f"⚠️  Invalid Stop Loss: {sl} (must be > 0)")
        return False
    
    # Check minimum risk-reward ratio (TP-Price)/(Price-SL) >= 1.5
    if tp > 0 and sl > 0:
        risk_reward = (tp - harga) / (harga - sl) if (harga - sl) > 0 else 0
        if risk_reward < 1.5:
            log.warning(f"⚠️  Poor R:R ratio: {risk_reward:.2f} (minimum 1.5)")
            return False
    
    # Check sinyal valid
    if sinyal not in ["ULTRA_BUY", "STRONG_BUY", "BUY", "SELL", "STRONG_SELL"]:
        log.warning(f"⚠️  Unknown sinyal type: {sinyal}")
        return False
    
    log.info(f"✅ Signal validation passed: {ticker} | R:R = {risk_reward:.2f}")
    return True
```

---

### FIX #3: Improved eksekusi_beli() with Error Handling

**BEFORE:**
```python
def eksekusi_beli(ticker, harga, sl, tp):
    conn = sqlite3.connect(PORTFOLIO_DB)
    cursor = conn.cursor()
    
    cursor.execute("SELECT saldo_cash FROM akun")
    saldo = cursor.fetchone()[0]
    
    max_beli = saldo * 0.20
    shares_to_buy = int(max_beli / harga)
    
    if shares_to_buy >= 100 and saldo >= (shares_to_buy * harga):
        total_biaya = shares_to_buy * harga
        saldo_baru = saldo - total_biaya
        
        cursor.execute("UPDATE akun SET saldo_cash = ?", (saldo_baru,))
        cursor.execute("INSERT INTO posisi VALUES (?, ?, ?, ?, ?, ?)", 
                       (ticker, harga, sl, tp, shares_to_buy, str(datetime.date.today())))
        
        conn.commit()
        print(f"🛒 [TRADE] BELI {ticker}: {shares_to_buy} @ Rp{harga}")
    else:
        print(f"❌ Dana tidak cukup untuk {ticker}")
    
    conn.close()
```

**AFTER:**
```python
def eksekusi_beli(ticker, harga, sl, tp):
    """
    Eksekusi pembelian dengan risk management dan error handling.
    """
    conn = None
    try:
        conn = sqlite3.connect(PORTFOLIO_DB)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check table exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS akun (
                id INTEGER PRIMARY KEY,
                saldo_cash REAL CHECK(saldo_cash >= 0)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posisi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                harga_beli REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                shares INTEGER NOT NULL,
                tanggal TEXT NOT NULL,
                status TEXT DEFAULT 'OPEN',
                harga_jual REAL,
                tanggal_jual TEXT
            )
        """)
        conn.commit()
        
        # Get current balance
        cursor.execute("SELECT SUM(saldo_cash) as total FROM akun")
        result = cursor.fetchone()
        saldo = result["total"] if result and result["total"] else 0
        
        if saldo <= 0:
            log.error(f"❌ No balance available: {saldo}")
            return False
        
        # Calculate shares (max 20% allocation)
        max_beli = saldo * 0.20
        estimated_cost = max_beli  # Include estimated commission
        shares_to_buy = int(estimated_cost / harga)
        
        # Minimum 100 shares (1 lot)
        if shares_to_buy < 100:
            log.warning(f"⚠️  Insufficient funds: {shares_to_buy} shares < 100 lot minimum")
            log.info(f"   Required: Rp{100 * harga:,.0f} | Available: Rp{max_beli:,.0f}")
            return False
        
        total_biaya = shares_to_buy * harga
        
        # Check if balance sufficient (with safety margin for commission)
        commission = total_biaya * 0.001  # 0.1% commission estimate
        total_dengan_komisi = total_biaya + commission
        
        if total_dengan_komisi > saldo:
            log.warning(f"❌ Insufficient balance with commission")
            log.info(f"   Cost: Rp{total_dengan_komisi:,.0f} | Available: Rp{saldo:,.0f}")
            return False
        
        # Execute trade
        saldo_baru = saldo - total_dengan_komisi
        tanggal_str = dt.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("UPDATE akun SET saldo_cash = ?", (saldo_baru,))
        cursor.execute(
            "INSERT INTO posisi (ticker, harga_beli, sl, tp, shares, tanggal, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticker, float(harga), float(sl), float(tp), shares_to_buy, tanggal_str, "OPEN")
        )
        conn.commit()
        
        log.info(f"🛒 [TRADE] BERHASIL BELI {ticker}")
        log.info(f"   Lembar: {shares_to_buy} @ Rp{harga:,.0f}")
        log.info(f"   Total: Rp{total_biaya:,.0f} + Komisi Rp{commission:,.0f}")
        log.info(f"   SL: Rp{sl:,.0f} | TP: Rp{tp:,.0f}")
        log.info(f"   Sisa Saldo: Rp{saldo_baru:,.0f}")
        
        return True
        
    except sqlite3.IntegrityError as e:
        log.error(f"❌ Integrity error (check constraints): {e}")
        return False
    except sqlite3.OperationalError as e:
        log.error(f"❌ Database operational error: {e}")
        return False
    except Exception as e:
        log.error(f"❌ Unexpected error: {type(e).__name__}: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass
```

---

### FIX #4: Add Position Exit Logic (CRITICAL)

**ADD:**
```python
def cek_exit_position():
    """
    Check posisi terbuka dan lihat apakah sudah hit SL atau TP.
    Seharusnya terintegrasi dengan harga real-time dari File 1.
    """
    conn = sqlite3.connect(PORTFOLIO_DB)
    cursor = conn.cursor()
    
    # Get harga terbaru untuk semua ticker dengan posisi terbuka
    cursor.execute("""
        SELECT p.id, p.ticker, p.harga_beli, p.sl, p.tp, h.harga
        FROM posisi p
        LEFT JOIN (
            SELECT DISTINCT ticker, harga FROM histori_ihsg
            WHERE (ticker, waktu) IN (
                SELECT ticker, MAX(waktu) FROM histori_ihsg GROUP BY ticker
            )
        ) h ON p.ticker = h.ticker
        WHERE p.status = 'OPEN'
    """)
    
    posisi_list = cursor.fetchall()
    
    for pos_id, ticker, harga_beli, sl, tp, harga_current in posisi_list:
        if harga_current is None:
            log.warning(f"⚠️  No current price for {ticker}, skipping")
            continue
        
        # Check Stop Loss
        if harga_current <= sl:
            log.warning(f"🛑 STOP LOSS HIT: {ticker} at Rp{harga_current}")
            close_position(pos_id, ticker, harga_current, "SL_HIT")
        
        # Check Take Profit
        elif harga_current >= tp:
            log.info(f"✅ TAKE PROFIT HIT: {ticker} at Rp{harga_current}")
            close_position(pos_id, ticker, harga_current, "TP_HIT")
    
    conn.close()


def close_position(pos_id, ticker, harga_jual, reason):
    """Close posisi dan update portfolio."""
    conn = sqlite3.connect(PORTFOLIO_DB)
    cursor = conn.cursor()
    
    try:
        # Get position details
        cursor.execute("SELECT harga_beli, shares FROM posisi WHERE id = ?", (pos_id,))
        row = cursor.fetchone()
        if not row:
            log.error(f"Position {pos_id} not found")
            return
        
        harga_beli, shares = row
        total_nilai = shares * harga_jual
        total_biaya = shares * harga_beli
        pl = total_nilai - total_biaya
        pl_pct = (pl / total_biaya * 100) if total_biaya > 0 else 0
        
        # Update position
        tanggal_str = dt.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "UPDATE posisi SET status = ?, harga_jual = ?, tanggal_jual = ? WHERE id = ?",
            ("CLOSED", harga_jual, tanggal_str, pos_id)
        )
        
        # Add cash back
        cursor.execute("UPDATE akun SET saldo_cash = saldo_cash + ?", (total_nilai,))
        
        conn.commit()
        
        status_icon = "✅" if pl > 0 else "❌"
        log.info(f"{status_icon} POSITION CLOSED: {ticker} | Reason: {reason}")
        log.info(f"   Buy: Rp{harga_beli:,.0f} | Sell: Rp{harga_jual:,.0f}")
        log.info(f"   P/L: Rp{pl:,.0f} ({pl_pct:.2f}%)")
        
    except Exception as e:
        log.error(f"Error closing position: {e}")
    finally:
        conn.close()


# Add di main loop sebelum sleep:
def jalankan_rl_agent():
    # ... existing code ...
    while True:
        try:
            # ... fetch signal ...
            
            # Check existing positions for exit
            cek_exit_position()
            
            # ... execute buy ...
            
            time.sleep(2)
        except Exception as e:
            # ... error handling ...
```

---

### FIX #5: Track Processed Signals to Avoid Duplicates

**ADD TABLE:**
```python
def inisialisasi_portofolio():
    """Menyiapkan database portofolio virtual"""
    conn = sqlite3.connect(PORTFOLIO_DB)
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS akun (
        id INTEGER PRIMARY KEY,
        saldo_cash REAL CHECK(saldo_cash >= 0)
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS posisi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        harga_beli REAL,
        sl REAL,
        tp REAL,
        shares INTEGER,
        tanggal TEXT,
        status TEXT DEFAULT 'OPEN',
        harga_jual REAL,
        tanggal_jual TEXT
    )''')
    
    # NEW: Track processed signals
    cursor.execute('''CREATE TABLE IF NOT EXISTS processed_signals (
        signal_id INTEGER PRIMARY KEY,
        ticker TEXT,
        harga REAL,
        sinyal TEXT,
        status TEXT,  -- 'PROCESSED', 'REJECTED'
        reason TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    cursor.execute("SELECT COUNT(*) FROM akun")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO akun (saldo_cash) VALUES (100000000.0)")
    
    conn.commit()
    conn.close()


# Di jalankan_rl_agent main loop:
if data:
    sig_id, ticker, harga, sinyal, tp, sl = data
    
    # Check if already processed
    cursor_hist.execute("SELECT COUNT(*) FROM processed_signals WHERE signal_id = ?", (sig_id,))
    if cursor_hist.fetchone()[0] > 0:
        log.warning(f"Signal {sig_id} sudah diproses, skip")
        last_signal_id = sig_id
        time.sleep(0.1)
        continue
    
    log.info(f"📩 Sinyal Baru #{sig_id}: {sinyal} {ticker} di Rp{harga:,.0f}")
    
    if validasi_signal(ticker, harga, sinyal, tp, sl):
        if sinyal in ["ULTRA_BUY", "STRONG_BUY"]:
            if eksekusi_beli(ticker, harga, sl, tp):
                cursor_hist.execute(
                    "INSERT INTO processed_signals (signal_id, ticker, harga, sinyal, status, reason) VALUES (?, ?, ?, ?, ?, ?)",
                    (sig_id, ticker, harga, sinyal, "PROCESSED", "BUY_EXECUTED")
                )
            else:
                cursor_hist.execute(
                    "INSERT INTO processed_signals (signal_id, ticker, harga, sinyal, status, reason) VALUES (?, ?, ?, ?, ?, ?)",
                    (sig_id, ticker, harga, sinyal, "REJECTED", "INSUFFICIENT_BALANCE")
                )
        last_signal_id = sig_id
    else:
        cursor_hist.execute(
            "INSERT INTO processed_signals (signal_id, ticker, harga, sinyal, status, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (sig_id, ticker, harga, sinyal, "REJECTED", "VALIDATION_FAILED")
        )
        last_signal_id = sig_id
    
    cursor_hist.commit()
```

---

## FILE 2: 2_consumer_ai.py - IMPROVEMENTS

### FIX: Race Condition in Processing

**BEFORE:**
```python
try:
    hasil = analisis_saham(ticker)  # Bisa timeout/crash di sini
except Exception as e:
    log.error(f"Error: {e}")
finally:
    last_processed_id = id_baris  # ALWAYS save, even jika error!
    save_last_id(cur, last_processed_id)
```

**AFTER:**
```python
analisis_success = False
try:
    hasil = analisis_saham(ticker)
    
    if not hasil:
        log.info(f"No result for {ticker}")
        analisis_success = False
    elif not validasi_hasil(hasil):
        log.warning(f"Validation failed for {ticker}")
        analisis_success = False
    else:
        # Process hasil...
        analisis_success = True
        
except Exception as e:
    log.error(f"Analysis error for {ticker}: {e}")
    analisis_success = False
finally:
    # Only mark as processed jika BERHASIL atau REJECTED (not retry-able)
    if analisis_success or max_retries_reached:
        last_processed_id = id_baris
        save_last_id(cur, last_processed_id)
    else:
        log.info(f"Retrying {ticker} later...")
```

---

### FIX: Better Import Handling

**ADD:**
```python
# Global state untuk retry
ANALYZER_READY = False
ANALYZER_RETRY_COUNT = 0
MAX_IMPORT_RETRIES = 5

def coba_import_analyzer():
    global ANALYZER_READY
    global analisis_saham
    
    try:
        from screener import analisis_saham
        ANALYZER_READY = True
        log.info("✅ Analyzer ready")
        return True
    except ImportError as e:
        log.warning(f"Analyzer not available ({ANALYZER_RETRY_COUNT}/{MAX_IMPORT_RETRIES}): {e}")
        return False
    except Exception as e:
        log.error(f"Unexpected error loading analyzer: {e}")
        return False

# Di main loop:
while True:
    if not ANALYZER_READY:
        ANALYZER_RETRY_COUNT += 1
        if ANALYZER_RETRY_COUNT > MAX_IMPORT_RETRIES:
            log.error("Max import retries reached, exiting")
            break
        if not coba_import_analyzer():
            time.sleep(5)
            continue
    
    # ... rest of processing ...
```

---

## FILE 1: 1_producer_data.py - IMPROVEMENTS

### FIX: Better Error Categorization

**BEFORE:**
```python
except asyncio.TimeoutError:
    err = "Timeout"
except Exception as e:
    err = str(e)  # Too generic!
```

**AFTER:**
```python
except asyncio.TimeoutError:
    err = f"Timeout ({TIMEOUT_SECS}s) - Yahoo tidak merespons"
except aiohttp.ClientConnectorError as e:
    err = f"Connection error - IP mungkin diblokir"
except aiohttp.ClientError as e:
    err = f"HTTP client error: {e}"
except asyncio.CancelledError:
    err = "Request cancelled"
except json.JSONDecodeError:
    err = "Invalid JSON response from Yahoo"
except KeyError as e:
    err = f"Missing field in response: {e}"
except ValueError as e:
    if "HTTP" in str(e):
        err = f"HTTP error from Yahoo: {e}"
    else:
        err = f"Value error: {e}"
except Exception as e:
    err = f"Unexpected {type(e).__name__}: {e}"

# Log retry attempts
log.debug(f"   Attempt {attempt}/{MAX_RETRIES + 1} failed: {err}")
```

---

### FIX: Exponential Backoff for Rate Limiting

**ADD:**
```python
async def mata_dewa_producer():
    log.info("⚡ MATA DEWA (ASYNC PRODUCER) v2.0 AKTIF ⚡")
    
    backoff_multiplier = 1.0
    consecutive_failures = 0
    
    with sqlite3.connect(DB_NAME) as conn:
        init_db(conn)
        cur = conn.cursor()

        siklus = 0
        while True:
            siklus += 1
            waktu_mulai = time.time()
            
            async with aiohttp.ClientSession() as session:
                tasks = [fetch_price_with_retry(session, t) for t in TICKERS]
                results = await asyncio.gather(*tasks)

            berhasil, gagal = 0, 0
            for ticker, harga in results:
                if harga is not None:
                    cur.execute(
                        "INSERT INTO histori_ihsg (ticker, harga) VALUES (?, ?)",
                        (ticker, float(harga))
                    )
                    berhasil += 1
                else:
                    cur.execute(
                        "INSERT INTO log_error (ticker, pesan) VALUES (?, ?)",
                        (ticker, "Gagal fetch setelah semua retry")
                    )
                    gagal += 1

            conn.commit()

            durasi = time.time() - waktu_mulai
            pct = berhasil / len(TICKERS) * 100
            
            # Adaptive backoff
            if pct < 50:
                consecutive_failures += 1
                if consecutive_failures == 1:
                    log.warning(f"⚠️  Success rate < 50%, activating exponential backoff")
                backoff_multiplier = min(backoff_multiplier * 1.5, 8.0)  # Max 8x
                log.warning(f"   Backoff x{backoff_multiplier:.1f}, retry dalam {CYCLE_INTERVAL * backoff_multiplier:.0f}s")
            else:
                consecutive_failures = 0
                backoff_multiplier = 1.0
            
            status = "✅" if pct >= 80 else ("⚠️" if pct >= 50 else "🔴")
            log.info(f"{status} #{siklus} | Berhasil: {berhasil}/{len(TICKERS)} ({pct:.0f}%) | Durasi: {durasi:.2f}s")
            
            # Apply backoff
            sleep_time = max(0, CYCLE_INTERVAL * backoff_multiplier - durasi)
            log.info(f"Tidur {sleep_time:.1f}s...")
            await asyncio.sleep(sleep_time)
```

---

## 📋 TESTING CHECKLIST

```
[ ] Test File 3 error handling dengan broken database
[ ] Test File 3 signal validation dengan invalid data
[ ] Test File 3 position exit logic
[ ] Test File 2 analyzer import retry
[ ] Test File 1 rate limiting backoff
[ ] Test all 3 files untuk race conditions dengan concurrent access
[ ] Test dengan historical data sebelum live trading
[ ] Load test dengan 200+ ticker
[ ] Network failure simulation (disconnect, timeout, slow responses)
[ ] Database corruption recovery
```

---

## 🚀 DEPLOYMENT CHECKLIST

Before going live:
1. [ ] Backup existing database
2. [ ] Test all fixes dengan small capital (Rp 1 juta)
3. [ ] Run for 24 jam tanpa intervensi
4. [ ] Monitor logs untuk errors
5. [ ] Verify P&L calculation accuracy
6. [ ] Test exit logic dengan manual price injection
7. [ ] Prepare rollback plan
8. [ ] Document all changes
9. [ ] Get approval dari team lead

---

**Priority:** 
- 🔴 Immediate (hari ini): File 3 error handling
- 🟡 Urgent (minggu ini): File 3 validation & exit logic
- 🟢 Soon (2 minggu): File 1 & 2 improvements
