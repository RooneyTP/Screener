import sqlite3
import time
import logging
import pandas as pd
from datetime import datetime, timedelta
from ai_model import get_ai_model
try:
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator
except ImportError:
    import os
    os.system("pip install ta pandas")
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator

DB_NAME            = "histori_ihsg.db"
POLL_FAST          = 1.0   
POLL_IDLE          = 3.0   
COOLDOWN_MENIT     = 5     

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

def init_db(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS sinyal_trading (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker  TEXT,
            harga   REAL,
            sinyal  TEXT,
            tp      REAL,
            sl      REAL,
            waktu   DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS consumer_state (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()

def analisis_scalping_kilat(ticker: str, cursor: sqlite3.Cursor):
    waktu_sekarang = datetime.now()
    is_pagi = (waktu_sekarang.hour == 9 and waktu_sekarang.minute < 30)

    cursor.execute("SELECT harga, volume FROM histori_ihsg WHERE ticker = ? ORDER BY id DESC LIMIT 60", (ticker,))
    rows = cursor.fetchall()
    
    if len(rows) < 3:
        return None
        
    harga_list = [row[0] for row in reversed(rows)]
    vol_list = [row[1] for row in reversed(rows)]
    
    df = pd.DataFrame({"Close": harga_list, "Volume": vol_list})
    close = df["Close"]
    volume = df["Volume"]
    
    harga_now = close.iloc[-1]
    vol_now = volume.iloc[-1]
    
    # 🟢 1. FILTER LIKUIDITAS: Anti Saham Sepi / Gorengan Mati
    # Hitung nilai transaksi menit terakhir (Harga x Volume)
    # Jika transaksinya di bawah Rp 50 Juta per menit, HINDARI!
    nilai_transaksi = harga_now * vol_now
    if nilai_transaksi < 50000000:
        return None # Lewati saham ini secara diam-diam
        
    # 🟢 2. MENGHITUNG VWAP (Harga Rata-Rata Bandar dalam 60 Menit)
    # VWAP = Total(Harga * Volume) / Total(Volume)
    df['Cumulative_Vol'] = df['Volume'].cumsum()
    df['Cumulative_Vol_Price'] = (df['Close'] * df['Volume']).cumsum()
    vwap_60 = (df['Cumulative_Vol_Price'] / df['Cumulative_Vol']).iloc[-1]

    sinyal = "HINDARI"
    
    # ==========================================
    # 🌅 OTAK PAGI (09:00 - 09:30 WIB)
    # ==========================================
    if is_pagi:
        if len(rows) < 5: return None 
            
        harga_open = close.iloc[0] 
        vol_sma3 = volume.rolling(window=3).mean().iloc[-2] 
        
        # 🟢 PERKETAT: Harga harus di atas harga buka DAN di atas VWAP
        is_breakout = (harga_now > harga_open) and (harga_now > vwap_60)
        # 🟢 PERKETAT: Volume harus meledak 2.5x lipat (bukan 1.5x)
        is_vol_spike_pagi = vol_now > (vol_sma3 * 2.5)
        
        if is_breakout and is_vol_spike_pagi:
            sinyal = "ULTRA_BUY"
            
    # ==========================================
    # ☀️ OTAK SIANG (09:30 - 16:00 WIB)
    # ==========================================
    else:
        if len(rows) < 30: return None 
            
        rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
        ema9 = EMAIndicator(close, window=9).ema_indicator().iloc[-1]
        vol_sma10 = volume.rolling(window=10).mean().iloc[-1]
        
        # 🟢 PERKETAT: Volume spike siang harus 2.0x lipat (200%)
        is_volume_spike = vol_now > (vol_sma10 * 2.0)
        
        # 🟢 PERKETAT: Syarat VWAP ditambahkan ke dalam logika Trend
        if harga_now > ema9 and harga_now > vwap_60 and 40 <= rsi <= 70 and is_volume_spike:
            sinyal = "ULTRA_BUY"
            
        if rsi > 50: # Radar Siang
            status_vol = "🔥 MELEDAK" if is_volume_spike else "Sepi"
            print(f"☀️ [OTAK SIANG] {ticker} | VWAP: Rp{vwap_60:,.0f} | RSI: {rsi:.1f} | Vol: {status_vol} --> {sinyal}")

    # ==========================================
    # 🤖 PREDIKSI KECERDASAN BUATAN (AI SCALPER)
    # ==========================================
    if sinyal in ["ULTRA_BUY", "STRONG_BUY"]:
        # Proxy (jembatan data) intraday 1-menit
        rsi_val = rsi if 'rsi' in locals() else 50
        vol_ratio = (vol_now / vol_sma10) * 50 if 'vol_sma10' in locals() and vol_sma10 > 0 else 50
        
        # 🟢 UPGRADE PHASE 2: 3 Fitur Tambahan
        rsi_vol_interaction = rsi_val * (vol_ratio / 50)
        try:
            rolling_vol_20 = float(close.pct_change().rolling(20).std().iloc[-1])
        except:
            rolling_vol_20 = 0.0
        sector_corr = 0.0 # Diabaikan untuk scalping super cepat
        
        # Susunan 14 Fitur untuk Otak Phase 2:
        fitur_kilat = [
            rsi_val, 30.0, vol_ratio, 5.0, 2.0, 60.0, 50.0, 0.0, 0.0, rsi_val, 0.0,
            rsi_vol_interaction, rolling_vol_20, sector_corr
        ]
        
        ai_scalper = get_ai_model(model_type="scalping")
        win_rate = ai_scalper.predict_win_probability(fitur_kilat)
        
        # VETO AI: Sinyal dibatalkan jika AI merasa peluang menangnya di bawah 60%
        if win_rate < 60.0:
            print(f"  🤖 AI VETO: {ticker} dibatalkan! Win Rate cuma {win_rate}%")
            return None
            
        print(f"  🤖 AI APPROVED: {ticker} di-acc dengan Win Rate {win_rate}%!")

        # 🎯 EXIT STRATEGY SCALPING (Cepat Masuk, Cepat Keluar)
        tp = harga_now * 1.015  # Target Cuan 1.5%
        sl = harga_now * 0.990  # Cutloss ketat 1%
        return {"Sinyal": sinyal, "Target_1": tp, "Stop_Loss": sl}
    
    return None

def sudah_ada_sinyal_baru(cur: sqlite3.Cursor, ticker: str) -> bool:
    batas_waktu = datetime.now() - timedelta(minutes=COOLDOWN_MENIT)
    cur.execute("SELECT COUNT(*) FROM sinyal_trading WHERE ticker = ? AND waktu >= ?", 
                (ticker, batas_waktu.strftime("%Y-%m-%d %H:%M:%S")))
    return cur.fetchone()[0] > 0

def jalankan_otak_ai():
    log.info("🧠 OTAK AI SCALPING v4.0 (DUAL-BRAIN) AKTIF")

    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        init_db(conn)

        cur.execute("SELECT MAX(id) FROM histori_ihsg")
        last_processed_id = cur.fetchone()[0] or 0

        try:
            while True:
                cur.execute("SELECT id, ticker, harga FROM histori_ihsg WHERE id > ? ORDER BY id ASC LIMIT 10", (last_processed_id,))
                rows = cur.fetchall()

                if not rows:
                    time.sleep(POLL_IDLE)
                    continue

                for row in rows:
                    id_baris, ticker, harga_live = row[0], row[1], row[2]
                    last_processed_id = id_baris
                    
                    hasil = analisis_scalping_kilat(ticker, cur)
                    
                    if hasil and not sudah_ada_sinyal_baru(cur, ticker):
                        sinyal = hasil["Sinyal"]
                        target_1 = hasil["Target_1"]
                        stop_loss = hasil["Stop_Loss"]
                        
                        cur.execute("INSERT INTO sinyal_trading (ticker, harga, sinyal, tp, sl) VALUES (?, ?, ?, ?, ?)",
                                    (ticker, harga_live, sinyal, target_1, stop_loss))
                        conn.commit()
                        log.info(f"🚀 SINYAL {sinyal} | {ticker} di Rp{harga_live:,.0f} | TP: {target_1:,.0f} | SL: {stop_loss:,.0f}")
                
                time.sleep(POLL_FAST)

        except KeyboardInterrupt:
            log.info("AI Brain dimatikan.")

if __name__ == "__main__":
    jalankan_otak_ai()