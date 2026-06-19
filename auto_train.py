import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import numpy as np
import joblib
from sklearn.preprocessing import StandardScaler
from liquid_moe import LiquidMoE  # Menggunakan arsitektur terbaru v9
import datetime
import os

# =======================================================
# KONFIGURASI RETRAINING v10.0 (ANTI-OVERFITTING MODE)
# =======================================================
DB_NAME = "histori_ihsg.db"
MODEL_PATH = "liquid_moe_brain.pth"
SCALER_PATH = "kacamata_ai.pkl"
FITUR_AI = [
    "RSI", "ADX", "Volume", "BB_Width%", "RRR", 
    "MM_Confidence", "MM_vs_Retail_Ratio", "IHSG_Change", "USD_Change", 
    "RSI_Kemarin", "MACD_Kemarin" # <- Created dynamically at line 40-41 via shift()
]

def train_v9_logic():
    print(f"🧠 Memperbarui Otak Liquid MoE v10.0 (ANTI-OVERFITTING): {datetime.datetime.now()}")
    
    # 1. KONEKSI & AMBIL DATA
    parquet_path = "data_lake/histori_ihsg.parquet"
    if not os.path.exists(parquet_path):
        print(f"❌ File {parquet_path} tidak ditemukan. Jalankan screener dulu.")
        return

    df = pd.read_parquet(parquet_path)

    if len(df) < 200:
        print(f"⚠️ Data baru ({len(df)}) masih terlalu sedikit. Butuh minimal 200 baris histori.")
        return

    # 2. LOGIKA ANTI-LEAKAGE (DARI v7.0)
    # Urutkan agar urutan waktu tidak berantakan
    df = df.sort_values(by=['Ticker', 'Tanggal'])

    # 🔥 OPTIMASI FINAL: Buat fitur 'Kemarin' secara dinamis
    df['RSI_Kemarin'] = df.groupby('Ticker')['RSI'].shift(1)
    df['MACD_Kemarin'] = df.groupby('Ticker')['MACD_1d'].shift(1)

    # TARGET: Apakah BESOK Harga-nya naik?
    df['Harga_Besok'] = df.groupby('Ticker')['Harga'].shift(-1)
    
    # Hapus baris yang kosong (hari pertama tidak punya 'Kemarin', hari terakhir tidak punya 'Besok')
    df = df.dropna(subset=['Harga_Besok', 'RSI_Kemarin'])

    # 3. TIME-BASED CHRONOLOGICAL SPLIT (Anti-Overfitting)
    # Urutkan global berdasarkan tanggal agar split kronologis
    df = df.sort_values('Tanggal').reset_index(drop=True)
    
    # 70% awal → train, 15% berikutnya → validation, 15% terakhir → holdout test
    n_total = len(df)
    n_train = int(n_total * 0.70)
    n_val   = int(n_total * 0.85)  # train + val = 85%
    
    df_train = df.iloc[:n_train]
    df_val   = df.iloc[n_train:n_val]
    df_test  = df.iloc[n_val:]
    
    print(f"📊 Split Kronologis (NO shuffle):")
    print(f"   Train: {len(df_train)} baris (70%) — data PALING LAMA")
    print(f"   Val  : {len(df_val)} baris (15%)")
    print(f"   Test : {len(df_test)} baris (15%) — data PALING BARU ← holdout")
    
    # 4. PENYIAPAN DATA (X & y) — TRAIN ONLY
    X_train_raw = df_train[FITUR_AI].values
    y_train = (df_train['Harga_Besok'] > df_train['Harga']).astype(float).values
    
    X_val_raw = df_val[FITUR_AI].values
    y_val = (df_val['Harga_Besok'] > df_val['Harga']).astype(float).values
    
    X_test_raw = df_test[FITUR_AI].values
    y_test = (df_test['Harga_Besok'] > df_test['Harga']).astype(float).values
    
    # Bersihkan NaN/Inf
    X_train_raw = np.nan_to_num(X_train_raw, nan=0.0, posinf=0.0, neginf=0.0)
    X_val_raw   = np.nan_to_num(X_val_raw, nan=0.0, posinf=0.0, neginf=0.0)
    X_test_raw  = np.nan_to_num(X_test_raw, nan=0.0, posinf=0.0, neginf=0.0)
    
    # [FIX #2] Scaler fit HANYA pada training data
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train_raw)
    X_val_scaled   = scaler.transform(X_val_raw)
    X_test_scaled  = scaler.transform(X_test_raw)
    joblib.dump(scaler, SCALER_PATH)
    print("✅ Kacamata AI (Scaler) dikalibrasi HANYA pada data training.")
    
    # 5. FORMATTING UNTUK LIQUID NETWORK (3D TENSOR)
    X_train_tensor = torch.tensor(X_train_scaled, dtype=torch.float32).unsqueeze(1)
    y_train_tensor = torch.tensor(y_train, dtype=torch.float32).unsqueeze(1)
    
    X_val_tensor   = torch.tensor(X_val_scaled, dtype=torch.float32).unsqueeze(1)
    y_val_tensor   = torch.tensor(y_val, dtype=torch.float32).unsqueeze(1)

    X_test_tensor  = torch.tensor(X_test_scaled, dtype=torch.float32).unsqueeze(1)
    y_test_tensor  = torch.tensor(y_test, dtype=torch.float32).unsqueeze(1)
    
    # 6. INISIALISASI MODEL v10.0
    model = LiquidMoE(num_experts=3, input_size=len(FITUR_AI), hidden_size=32)
    
    # Mencoba load otak lama agar tidak pikun (Transfer Learning)
    try:
        model.load_state_dict(torch.load(MODEL_PATH, weights_only=True))
        print("💡 Melanjutkan pembelajaran dari memori yang sudah ada...")
    except Exception:
        print("👶 Memulai proses pembelajaran dari nol.")

    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.0001, weight_decay=1e-5)
    
    best_val_loss = float('inf')
    patience = 50  # Berhenti jika val loss tidak turun selama 50 epoch
    patience_counter = 0
    best_model_state = None

    # 7. TRAINING LOOP DENGAN VALIDATION MONITORING
    model.train()
    print("🚀 Mesin Deep Learning sedang bekerja (dengan validation monitoring)...")
    for epoch in range(500):
        optimizer.zero_grad()
        outputs = model(X_train_tensor)
        loss = criterion(outputs, y_train_tensor)
        loss.backward()
        optimizer.step()
        
        # ── Validation check setiap epoch ──
        model.eval()
        with torch.no_grad():
            val_outputs = model(X_val_tensor)
            val_loss = criterion(val_outputs, y_val_tensor).item()
        model.train()
        
        current_loss = val_loss  # Gunakan validation loss untuk early stopping

        # Logika Early Stopping berbasis VALIDATION loss
        if current_loss < best_val_loss:
            best_val_loss = current_loss
            patience_counter = 0
            best_model_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            patience_counter += 1

        if patience_counter >= patience:
            print(f"🛑 Early stopping dipicu pada epoch {epoch}. Val loss tidak membaik selama {patience} epoch.")
            break 
        
        if (epoch+1) % 20 == 0:
            train_loss_val = loss.item()
            print(f"   🔥 Epoch [{epoch+1:4d}/500] | Train Loss: {train_loss_val:.4f} | Val Loss: {val_loss:.4f}")

    # ── Restore best model ──
    if best_model_state is not None:
        model.load_state_dict(best_model_state)
    
    # 8. EVALUASI PADA HOLDOUT TEST SET (data PALING BARU)
    model.eval()
    with torch.no_grad():
        test_outputs = model(X_test_tensor)
        test_loss = criterion(test_outputs, y_test_tensor).item()
        test_preds = (torch.sigmoid(test_outputs) > 0.5).float()
        test_acc = (test_preds == y_test_tensor).float().mean().item()
    
    print(f"\n📈 HASIL EVALUASI PADA HOLDOUT (Data Paling Baru — {len(df_test)} baris):")
    print(f"   Test Loss    : {test_loss:.4f}")
    print(f"   Test Accuracy: {test_acc*100:.2f}%  ← INI AKURASI JUJUR")
    print(f"   Best Val Loss: {best_val_loss:.4f}")

    # 9. SIMPAN MODEL TERBAIK
    torch.save(model.state_dict(), MODEL_PATH)
    print(f"\n✅ UPDATE SELESAI! Otak terbaik disimpan di '{MODEL_PATH}'")
    print(f"🤖 AI sekarang lebih tajam dalam membedakan 'Fake Breakout' vs 'Real Trend'.")
    print(f"📊 Akurasi jujur (holdout): {test_acc*100:.2f}% — tidak ada data leakage.")

if __name__ == "__main__":
    train_v9_logic()
