import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
import joblib # <--- TAMBAHKAN INI

from liquid_moe import LiquidMoE
from data_pipeline import MarketDataset

def train_ai():
    print("🎓 Membuka Sekolah AI (Liquid MoE Training)...")
    
    # 1. Siapkan Buku Pelajaran
    dataset = MarketDataset(sequence_length=30)
    dataloader = DataLoader(dataset, batch_size=64, shuffle=True)
    
    # EKSTRAK KACAMATA SCALER-NYA KE FILE!
    joblib.dump(dataset.scaler, "kacamata_ai.pkl")
    print("👓 [INFO] Kacamata Scaler berhasil disave sebagai 'kacamata_ai.pkl'")
    
    # 2. Panggil Siswanya (Inisialisasi Model)
    # PERHATIKAN: input_size wajib 14 karena terminalmu tadi bilang fiturnya ada 14
    model = LiquidMoE(num_experts=3, input_size=14, hidden_size=32)
    
    # 3. Siapkan Guru Penilai
    # Pakai BCEWithLogitsLoss karena AI kita menebak probabilitas (1 atau 0)
    criterion = nn.BCEWithLogitsLoss() 
    # Optimizer AdamW adalah algoritma dewa untuk memperbaiki bobot sel saraf AI
    optimizer = optim.AdamW(model.parameters(), lr=0.001)
    
    epochs = 10 # Kita mulai dengan 10 putaran belajar (bisa dinaikkan nanti)
    
    print(f"\n🚀 Memulai Proses Belajar ({epochs} Epochs)...")
    
    model.train() # Menyalakan mode belajar (Weight bisa berubah)
    for epoch in range(epochs):
        total_loss = 0
        
        for X_batch, y_batch in dataloader:
            # a. Bersihkan sisa ingatan kesalahan dari soal sebelumnya
            optimizer.zero_grad()
            
            # b. AI mencoba menebak (Ujian)
            prediksi = model(X_batch)
            
            # c. Guru menilai seberapa jauh tebakan AI dari kunci jawaban
            loss = criterion(prediksi, y_batch)
            # d. BACKPROPAGATION: AI mengoreksi sel saraf otaknya!
            loss.backward()
            optimizer.step()
            
            total_loss += loss.item()
            
        rata_rata_loss = total_loss / len(dataloader)
        print(f"Epoch [{epoch+1}/{epochs}] | Error Rate (Loss): {rata_rata_loss:.4f}")
        
    # 4. Lulus Sekolah -> Simpan Otaknya!
    torch.save(model.state_dict(), "liquid_moe_brain.pth")
    print("\n🎉 LULUS! Otak AI telah diekstrak dan disave sebagai 'liquid_moe_brain.pth'")

if __name__ == "__main__":
    train_ai()