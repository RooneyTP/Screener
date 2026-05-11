import sqlite3
import time
import datetime
import requests  # Library baru untuk mengirim pesan ke internet

DB_NAME = "histori_ihsg.db"
PORTFOLIO_DB = "portofolio_virtual.db"
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1497448578312835082/L_lkCmGrKEByeKwHeRaoycT9JS2QGjU_Mln6sekuzEvhBlOgkiwgfi8_NBww0iHgrD8G" # 🟢 MASUKKAN URL DISINI
# 🟢 STANDAR FEE & PAJAK SEKURITAS INDONESIA
FEE_BELI = 0.0015  # 0.15%
FEE_JUAL = 0.0025  # 0.25% (Sudah termasuk Pajak PPh Final 0.1%)
def kirim_discord(judul, pesan, warna):
    """Fungsi khusus untuk menembak pesan ke Discord via Webhook."""
    # 🟢 Cukup pastikan URL-nya tidak kosong, sisanya gas kirim!
    if not DISCORD_WEBHOOK_URL:
        return
    
    data = {
        "embeds": [{
            "title": judul,
            "description": pesan,
            "color": warna,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=data)
    except Exception as e:
        print(f"⚠️ Gagal mengirim pesan ke Discord: {e}")

def inisialisasi_portofolio():
    conn = sqlite3.connect(PORTFOLIO_DB)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS akun (saldo_cash REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS posisi (ticker TEXT, harga_beli REAL, sl REAL, tp REAL, shares INTEGER, tanggal TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS histori_trade (ticker TEXT, pnl REAL, status TEXT, tanggal TEXT)''')
    
    cursor.execute("SELECT saldo_cash FROM akun")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO akun (saldo_cash) VALUES (100000000.0)")
    conn.commit()
    conn.close()

def pantau_dan_exit(cursor_hist):
    conn_port = sqlite3.connect(PORTFOLIO_DB)
    cur_port = conn_port.cursor()
    
    cur_port.execute("SELECT rowid, ticker, harga_beli, sl, tp, shares FROM posisi")
    posisi_open = cur_port.fetchall()
    
    for pos in posisi_open:
        rowid, tkr, h_beli, sl, tp, shares = pos
        
        cursor_hist.execute("SELECT harga FROM histori_ihsg WHERE ticker = ? ORDER BY id DESC LIMIT 1", (tkr,))
        res = cursor_hist.fetchone()
        
        if res:
            harga_live = res[0]
            status_jual = None
            warna_embed = 16711680 # Merah
            
            if harga_live >= tp:
                status_jual = "TAKE PROFIT 🟢"
                warna_embed = 65280 # Hijau
            elif harga_live <= sl:
                status_jual = "CUT LOSS 🔴"
                
            if status_jual:
                # 🟢 PERHITUNGAN PAJAK & FEE KETIKA JUAL
                nilai_jual_kotor = harga_live * shares
                biaya_jual_dan_pajak = nilai_jual_kotor * FEE_JUAL
                uang_masuk_bersih = nilai_jual_kotor - biaya_jual_dan_pajak
                
                # Hitung PnL Bersih (Net Profit/Loss)
                modal_awal_kotor = h_beli * shares
                biaya_beli_awal = modal_awal_kotor * FEE_BELI
                total_modal_keluar = modal_awal_kotor + biaya_beli_awal
                
                pnl_bersih = uang_masuk_bersih - total_modal_keluar
                
                cur_port.execute("SELECT saldo_cash FROM akun")
                saldo_lama = cur_port.fetchone()[0]
                saldo_baru = saldo_lama + uang_masuk_bersih
                
                cur_port.execute("UPDATE akun SET saldo_cash = ?", (saldo_baru,))
                cur_port.execute("DELETE FROM posisi WHERE rowid = ?", (rowid,))
                cur_port.execute("INSERT INTO histori_trade VALUES (?, ?, ?, ?)", (tkr, pnl_bersih, status_jual, str(datetime.datetime.now())))
                conn_port.commit()
                
                print(f"\n⚡ EKSEKUSI JUAL: {status_jual} | {tkr} terjual di Rp{harga_live:,.0f} | Net PnL: Rp{pnl_bersih:,.0f}")
                
                pesan = f"**Terjual di:** Rp{harga_live:,.0f}\n**Net PnL:** Rp{pnl_bersih:,.0f}\n*(Pajak & Fee Jual: Rp{biaya_jual_dan_pajak:,.0f})*\n**Sisa Saldo:** Rp{saldo_baru:,.0f}"
                kirim_discord(f"⚡ EXIT: {tkr} ({status_jual})", pesan, warna_embed)

    conn_port.close()

def eksekusi_beli(ticker, harga, sl, tp):
    conn = sqlite3.connect(PORTFOLIO_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT saldo_cash FROM akun")
    saldo = cursor.fetchone()[0]
    
    max_beli = saldo * 0.20
    jumlah_lot = int((max_beli / harga) / 100)
    shares_to_buy = jumlah_lot * 100
    
    if shares_to_buy >= 100: # Minimal beli 1 lot (100 lembar)
        modal_saham = shares_to_buy * harga
        biaya_fee = modal_saham * FEE_BELI
        total_keluar = modal_saham + biaya_fee # 🟢 Saldo dipotong harga saham + fee broker
        
        if saldo >= total_keluar:
            saldo_baru = saldo - total_keluar
            
            cursor.execute("UPDATE akun SET saldo_cash = ?", (saldo_baru,))
            cursor.execute("INSERT INTO posisi VALUES (?, ?, ?, ?, ?, ?)", (ticker, harga, sl, tp, shares_to_buy, str(datetime.datetime.now())))
            conn.commit()
            
            print(f"\n🛒 BOT BELI: {ticker} | {shares_to_buy} lbr @ Rp{harga:,.0f} | Total Biaya: Rp{total_keluar:,.0f}")
            
            pesan = f"**Harga Beli:** Rp{harga:,.0f}\n**Target (TP):** Rp{tp:,.0f}\n**Stop Loss:** Rp{sl:,.0f}\n**Volume:** {shares_to_buy} lembar\n**Fee Beli:** Rp{biaya_fee:,.0f}"
            kirim_discord(f"🛒 NEW POSITION: {ticker}", pesan, 3447003)

    conn.close()

def jalankan_rl_agent():
    print("🤖 RL EXECUTOR AKTIF: Menyiapkan agen (Beli & Jual)...")
    inisialisasi_portofolio()
    
    conn_hist = sqlite3.connect(DB_NAME)
    cursor_hist = conn_hist.cursor()
    cursor_hist.execute("CREATE TABLE IF NOT EXISTS sinyal_trading (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, harga REAL, sinyal TEXT, tp REAL, sl REAL, waktu DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn_hist.commit()
    
    cursor_hist.execute("SELECT MAX(id) FROM sinyal_trading")
    last_signal_id = cursor_hist.fetchone()[0] or 0
    
    print("✅ Agen Siap! Menunggu komando AI dan memantau posisi...")

    # 🟢 TAMBAHKAN BARIS INI (Test Ping ke Discord saat bot pertama kali menyala)
    kirim_discord("🟢 BOT SCALPING ONLINE", "Sistem berhasil menyala dan terhubung! Bos, saya siap berburu saham hari ini.", 65280) # 65280 adalah kode warna Hijau

    try:
        while True:
            pantau_dan_exit(cursor_hist)
            
            cursor_hist.execute("SELECT id, ticker, harga, sinyal, tp, sl FROM sinyal_trading WHERE id > ? LIMIT 1", (last_signal_id,))
            data = cursor_hist.fetchone()

            if data:
                sig_id, ticker, harga, sinyal, tp, sl = data
                
                conn_port = sqlite3.connect(PORTFOLIO_DB)
                cur_port = conn_port.cursor()
                cur_port.execute("SELECT * FROM posisi WHERE ticker = ?", (ticker,))
                sudah_punya = cur_port.fetchone()
                conn_port.close()
                
                if not sudah_punya and sinyal in ["ULTRA_BUY", "STRONG_BUY"]:
                    eksekusi_beli(ticker, harga, sl, tp)
                
                last_signal_id = sig_id
            
            time.sleep(1) 

    except KeyboardInterrupt:
        print("\nRL Agent dimatikan.")
    finally:
        conn_hist.close()

if __name__ == "__main__":
    jalankan_rl_agent()