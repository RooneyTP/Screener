import subprocess
import os
import time
from datetime import datetime
import sys

# Konfigurasi
DB_NAME = "histori_ihsg.db"
FILES_TO_RUN = ["1_producer_data.py", "2_consumer_ai.py", "3_consumer_r1.py"]

def is_market_open():
    """Mengecek apakah saat ini adalah jam kerja bursa (Senin-Jumat, 08:55 - 16:15 WIB)."""
    now = datetime.now()
    
    # Cek Hari: 0 = Senin, 4 = Jumat, 5 = Sabtu, 6 = Minggu
    if now.weekday() > 4:
        return False
        
    # Cek Jam: Kita buka 5 menit sebelum bursa (08:55) dan tutup sedikit setelah penutupan (16:15)
    market_start = now.replace(hour=8, minute=55, second=0, microsecond=0)
    market_end = now.replace(hour=16, minute=15, second=0, microsecond=0)
    
    return market_start <= now <= market_end

def clean_database():
    """
    Menghapus database HANYA jika itu adalah data sisa kemarin.
    Mencegah Amnesia Data jika bot tidak sengaja ter-restart di tengah jam bursa.
    """
    if os.path.exists(DB_NAME):
        # Mengecek kapan file database terakhir kali dimodifikasi
        file_time = datetime.fromtimestamp(os.path.getmtime(DB_NAME))
        hari_ini = datetime.now().date()
        
        if file_time.date() < hari_ini:
            try:
                os.remove(DB_NAME)
                print(f"🧹 [CLEANUP] Database '{DB_NAME}' sisa kemarin berhasil dihapus.")
            except Exception as e:
                print(f"⚠️ [ERROR] Gagal menghapus '{DB_NAME}': {e}")
        else:
            print(f"✨ [SAFE] Database '{DB_NAME}' adalah data hari ini. Aman dari penghapusan!")
    else:
        print(f"✨ [CLEANUP] Database belum ada, siap dibuat baru.")

def main():
    print("==================================================")
    print("  🤖 SUPER MANAGER: BOT SCALPING OTOMATIS AKTIF  ")
    print("==================================================")
    
    # 1. Bersihkan sisa data secara cerdas sebelum mulai
    clean_database()
    
    processes = []
    
    try:
        while True:
            if is_market_open():
                # Jika bursa BUKA tapi bot belum menyala
                if not processes:
                    print(f"\n🟢 [START] Jam bursa aktif! Menyalakan {len(FILES_TO_RUN)} mesin tempur...")
                    for file in FILES_TO_RUN:
                        if os.path.exists(file):
                            # 🟢 UPGRADE: Tambahkan "-u" (Unbuffered) agar log terminal muncul real-time!
                            p = subprocess.Popen([sys.executable, "-u", file])
                            processes.append(p)
                            time.sleep(1) # Jeda 1 detik antar file biar rapi
                            print(f"  --> {file} ... ON")
                        else:
                            print(f"  ❌ [ERROR] File {file} tidak ditemukan!")
                    
                    print("✅ [STATUS] Seluruh pasukan telah diterjunkan ke pasar.\n")
                
                # Jika sudah menyala, diam saja (biarkan bot bekerja)
                pass
                
            else:
                # Jika bursa TUTUP tapi bot masih menyala
                if processes:
                    print("\n🔴 [STOP] Jam bursa telah usai. Menarik mundur semua mesin...")
                    for p in processes:
                        p.terminate() # Matikan paksa subprocess
                    processes = []
                    print("💤 [STATUS] Mesin sedang beristirahat.")
                    
                    # Bersihkan DB lagi setelah tutup biar besok pagi bersih
                    clean_database()
                
                # Tampilkan status menunggu (update di baris yang sama)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Menunggu bursa buka (Senin-Jumat, 08:55 WIB)...   ", end="\r")
            
            # Cek kondisi waktu setiap 30 detik
            time.sleep(30)
            
    except KeyboardInterrupt:
        # Jika kamu menekan CTRL+C di terminal
        print("\n\n🛑 [SHUTDOWN] Mematikan Super Manager secara manual...")
        for p in processes:
            p.terminate()
        print("👋 Sampai jumpa, Bos!")

if __name__ == "__main__":
    main()