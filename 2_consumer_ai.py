import time
import torch
import torch.nn as nn
import os
from mini_broker import baca_pesan, kirim_pesan, init_broker

# Konfigurasi File Memori AI
MODEL_PATH = "otak_ai_saham.pth"

# 1. Arsitektur Neural Network Sederhana
class DummyMoE(nn.Module):
    def __init__(self):
        super().__init__()
        self.layer = nn.Linear(4, 1) # 4 Fitur input (RSI, MACD, MM, Vol)
        # Inisialisasi bobot agar lebih merata (Xavier Initialization)
        nn.init.xavier_uniform_(self.layer.weight)
        
    def forward(self, x):
        return self.layer(x)

def jalankan_consumer_ai():
    # Pastikan infrastruktur database siap
    init_broker()
    
    model = DummyMoE()
    
    # --- FITUR AUTO-LOAD (Memuat Memori) ---
    if os.path.exists(MODEL_PATH):
        print("🧠 Memuat memori AI dari sesi sebelumnya...")
        try:
            model.load_state_dict(torch.load(MODEL_PATH))
            print("✅ Memori berhasil dipulihkan.")
        except:
            print("⚠️ Gagal memuat memori, memulai dari nol.")
    else:
        print("🧠 AI Baru Terdeteksi: Memulai proses pembelajaran dari awal...")

    # Optimizer dengan Learning Rate yang stabil
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.BCEWithLogitsLoss()

    print("🧠 AI CONSUMER AKTIF: Menunggu umpan data...")
    
    count_data = 0
    while True:
        # Cek apakah ada pesan baru di topik 'pasar_saham'
        pesan = baca_pesan(topik="pasar_saham")
        
        if pesan:
            # --- NORMALISASI DATA (Mencegah Angka Meledak) ---
            fitur = pesan['fitur']
            # Skala fitur disesuaikan agar model tidak "silau" oleh angka besar
            fitur_norm = [
                fitur[0] / 100.0,    # RSI (0-100) -> 0.0-1.0
                fitur[1] / 10.0,     # MACD -> Skala lebih kecil
                fitur[2],            # MM Ratio -> Sudah kecil
                fitur[3] / 100000.0  # Volume (Besar) -> Skala 0.x
            ]
            fitur_tensor = torch.tensor(fitur_norm, dtype=torch.float32)
            
            # 1. Tebak Probabilitas (Mode Evaluasi)
            model.eval()
            with torch.no_grad():
                prediksi_raw = model(fitur_tensor)
                # Gunakan Sigmoid untuk mengubah angka mentah jadi 0% - 100%
                prob_cuan = torch.sigmoid(prediksi_raw).item() * 100
                
            print(f"\n⚡ AI Menganalisis {pesan['ticker']} | Probabilitas Cuan: {prob_cuan:.2f}%")

            # 2. Continuous Learning (Mode Training)
            # Target realistis (0.6) agar AI tidak terlalu percaya diri (overfit)
            target_realistis = torch.tensor([0.6]) 
            
            model.train()
            optimizer.zero_grad()
            loss = criterion(model(fitur_tensor), target_realistis)
            loss.backward()
            
            # Gradient Clipping agar bobot tidak melompat terlalu jauh
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            
            optimizer.step()
            
            # --- FITUR AUTO-SAVE (Simpan setiap 10 data) ---
            count_data += 1
            if count_data % 10 == 0:
                torch.save(model.state_dict(), MODEL_PATH)
                print(f"💾 Progres ke-{count_data}: Memori AI berhasil diamankan.")

            # 3. Teruskan sinyal ke Agen RL di Terminal 3
            sinyal = {
                "ticker": pesan['ticker'], 
                "harga": pesan['harga_terakhir'], 
                "prob_cuan": prob_cuan
            }
            kirim_pesan(topik="sinyal_ai", pesan_dict=sinyal)
        else:
            # Istirahat sejenak agar tidak membebani CPU
            time.sleep(1)

if __name__ == "__main__":
    jalankan_consumer_ai()