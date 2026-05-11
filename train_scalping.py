import pandas as pd
from ai_model import get_ai_model
import os

def train_ai_scalping():
    print("⚡ Memulai Pelatihan Otak SCALPING (Fast Trade)...")
    
    try:
        # 1. Baca data dari Data Lake
        # Idealnya scalping menggunakan data intraday (1m/5m). 
        # Jika belum punya, kita pinjam data daily dulu untuk membentuk refleks dasarnya.
        file_path = "data_lake/histori_ihsg.parquet"
        
        if not os.path.exists(file_path):
            print(f"❌ Data Parquet '{file_path}' kosong. Jalankan mesin waktu dulu.")
            return

        df = pd.read_parquet(file_path)
        print(f"📊 Menemukan {len(df)} baris data historis.")
        
        # 2. URUTKAN WAKTU (Mutlak Diperlukan)
        df = df.sort_values(by=['Ticker', 'Tanggal'])
        
        # ==========================================================
        # 3. LOGIKA KEMENANGAN SCALPING (LABELING SUPER KETAT)
        # ==========================================================
        # Scalping butuh profit cepat. Kita hanya intip 1-2 periode ke depan.
        df['Harga_Future'] = df.groupby('Ticker')['Harga'].shift(-2)
        
        # Buang baris yang masa depannya belum terjadi
        df = df.dropna(subset=['Harga_Future'])
        
        # 🟢 UPGRADE: Target Profit Scalping 1% (Sudah cover fee broker 0.4%)
        # AI hanya melabeli MENANG jika saham naik minimal 1% dengan cepat
        y = (df['Harga_Future'] > (df['Harga'] * 1.01)).astype(int)
        
        total_data = len(df)
        win_count = y.sum()
        print(f"🎯 Dari {total_data} data, terdapat {win_count} peluang WIN cepat dan {total_data - win_count} peluang LOSS/Lambat.")

        # ==========================================================
        # 4. MAKANAN AI (FITUR)
        # ==========================================================
        fitur = ['RSI', 'ADX', 'Volume', 'BB_Width%', 'RRR', 
                 'MM_Confidence', 'MM_vs_Retail_Ratio', 
                 'IHSG_Change', 'USD_Change', 'RSI_1d', 'MACD_1d']
                 
        X = df[fitur].fillna(0)
        
        # ==========================================================
        # 5. EKSEKUSI PELATIHAN NEURAL NETWORK
        # ==========================================================
        print("⏳ Sedang menyuntikkan refleks kilat ke dalam Otak SCALPER AI...")
        
        # 🟢 BARIS SAKTI: Memanggil otak SCALPING dari ai_model.py
        ai_scalper = get_ai_model(model_type="scalping")
        ai_scalper.train_model(X, y)
        
        print("✅ SUKSES! Otak SCALPING selesai dilatih dan siap jadi Sniper Kilat!")

    except Exception as e:
        print(f"❌ Terjadi kesalahan: {e}")

if __name__ == "__main__":
    train_ai_scalping()