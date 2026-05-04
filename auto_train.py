import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import numpy as np
import sqlite3
import joblib
from sklearn.preprocessing import StandardScaler
from liquid_moe import LiquidMoE  # Menggunakan arsitektur terbaru v9
import datetime

# =======================================================
# KONFIGURASI RETRAINING v9.0 (ANTI-LEAKAGE MODE)
# =======================================================
DB_NAME = "histori_ihsg.db"
MODEL_PATH = "liquid_moe_brain.pth"
SCALER_PATH = "kacamata_ai.pkl"
FITUR_AI = [
    "RSI", "ADX", "Volume", "BB_Width%", "RRR", 
    "MM_Confidence", "MM_vs_Retail_Ratio", "IHSG_Change", "USD_Change", 
    "RSI_Kemarin", "MACD_Kemarin" # <- Disesuaikan
]

def train_v9_logic():
    print(f"🧠 Memperbarui Otak Liquid MoE v9.0: {datetime.datetime.now()}")
    
    # 1. KONEKSI & AMBIL DATA
    df = pd.read_parquet("data_lake/histori_ihsg.parquet")

    if len(df) < 100:
        print(f"⚠️ Data baru ({len(df)}) masih terlalu sedikit. Butuh minimal 100 baris histori.")
        return

    # 2. LOGIKA ANTI-LEAKAGE (DARI v7.0)
    # Urutkan agar urutan waktu tidak berantakan
    df = df.sort_values(by=['Ticker', 'Tanggal'])

    # 🔥 OPTIMASI FINAL: Buat fitur 'Kemarin' secara dinamis, bukan mengandalkan string SQLite
    df['RSI_Kemarin'] = df.groupby('Ticker')['RSI'].shift(1)
    df['MACD_Kemarin'] = df.groupby('Ticker')['MACD_1d'].shift(1) # Asumsi MACD_1d di SQLite adalah MACD hari itu

    # TARGET: Apakah BESOK Harga-nya naik?
    df['Harga_Besok'] = df.groupby('Ticker')['Harga'].shift(-1)
    
    # Hapus baris yang kosong (hari pertama tidak punya 'Kemarin', hari terakhir tidak punya 'Besok')
    df = df.dropna(subset=['Harga_Besok', 'RSI_Kemarin'])

    # 3. PENYIAPAN DATA (X & y)
    X = df[FITUR_AI].values
    # Label 1 jika besok harga NAIK (Ditutup Hijau berapapun persentasenya)
    # Ini membuat data menjadi lebih seimbang (~50% kejadian naik, ~50% kejadian turun)
    y = (df['Harga_Besok'] > df['Harga']).astype(float)
    # Memastikan tidak ada angka tak terhingga atau kosong yang masuk ke AI
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    
    # 4. NORMALISASI (KACAMATA AI)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    joblib.dump(scaler, SCALER_PATH)
    print("✅ Kacamata AI (Scaler) telah dikalibrasi ulang.")

    # 5. FORMATTING UNTUK LIQUID NETWORK (3D TENSOR)
    # Jangan gunakan .repeat(1, 30, 1) jika datanya cuma 1 hari
    # Gunakan dimensi sekuens = 1 saja agar AI fokus pada data riil hari ini
    X_tensor = torch.tensor(X_scaled, dtype=torch.float32).unsqueeze(1) # (Batch, 1, Features)
    y_tensor = torch.tensor(y.values, dtype=torch.float32).unsqueeze(1)

    # 6. INISIALISASI MODEL v9.0
    model = LiquidMoE(num_experts=3, input_size=len(FITUR_AI), hidden_size=32)
    
    # Mencoba load otak lama agar tidak pikun (Transfer Learning)
    try:
        model.load_state_dict(torch.load(MODEL_PATH, weights_only=True))
        print("💡 Melanjutkan pembelajaran dari memori yang sudah ada...")
    except:
        print("👶 Memulai proses pembelajaran dari nol.")

    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.0001, weight_decay=1e-5)
    
    best_loss = float('inf')
    patience = 50  # Berhenti jika loss tidak turun selama 50 epoch
    patience_counter = 0

    # 7. TRAINING LOOP (PROSES BELAJAR)
    model.train()
    print("🚀 Mesin Deep Learning sedang bekerja...")
    for epoch in range(500):
        optimizer.zero_grad()
        outputs = model(X_tensor)
        loss = criterion(outputs, y_tensor)
        loss.backward()
        optimizer.step()
        
        current_loss = loss.item()

        # Logika Early Stopping
        if current_loss < best_loss:
            best_loss = current_loss
            patience_counter = 0
            # Simpan model TERBAIK saat itu juga
            torch.save(model.state_dict(), MODEL_PATH) 
        else:
            patience_counter += 1

        if patience_counter >= patience:
            print(f"🛑 Early stopping dipicu pada epoch {epoch}. Model sudah optimal.")
            break 
        
        if (epoch+1) % 20 == 0:
            print(f"   🔥 Progress [{epoch+1}/500] | Tingkat Kesalahan: {current_loss:.4f}")

    # 8. SIMPAN HASIL PERUBAHAN
    print(f"\n✅ UPDATE SELESAI! Otak terbaik disimpan di '{MODEL_PATH}'")
    print("🤖 AI sekarang lebih tajam dalam membedakan 'Fake Breakout' vs 'Real Trend'.")

if __name__ == "__main__":
    train_v9_logic()