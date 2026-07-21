import sqlite3

def tampilkan_rapor_hari_ini():
    conn = sqlite3.connect("portofolio_virtual.db")
    cursor = conn.cursor()
    
    # 1. Hitung Total PnL dari semua trade yang sudah selesai hari ini
    cursor.execute("SELECT SUM(pnl) FROM histori_trade")
    total_pnl = cursor.fetchone()[0] or 0
    
    # 2. Cek Saldo Kas saat ini
    cursor.execute("SELECT saldo_cash FROM akun")
    saldo_sekarang = cursor.fetchone()[0]
    
    # 3. Cek Saham yang masih nyangkut/di-hold
    cursor.execute("SELECT COUNT(*) FROM posisi")
    jumlah_posisi = cursor.fetchone()[0]
    
    print("="*30)
    print(f"📊 RAPOR TRADING HARI INI")
    print("="*30)
    print(f"💰 Total Net PnL : Rp{total_pnl:,.2f}")
    print(f"🏦 Saldo Kas     : Rp{saldo_sekarang:,.2f}")
    print(f"📦 Saham di-hold : {jumlah_posisi} Saham")
    print("="*30)
    
    conn.close()

if __name__ == "__main__":
    tampilkan_rapor_hari_ini()
