import torch
import torch.nn as nn
from ncps.torch import CfC # Closed-form Continuous-time (Varian Liquid NN yang sangat cepat)

# =====================================================================
# 1. BAGIAN MIKRO: SANG PAKAR (LIQUID EXPERT)
# =====================================================================
class LiquidExpert(nn.Module):
    def __init__(self, input_size, hidden_size):
        super().__init__()
        # Ini adalah otaknya. "CfC" memungkinkan bobot/weight berubah 
        # secara dinamis (cair) saat menerima data rentetan waktu (time-series).
        self.lnn = CfC(input_size, hidden_size)
        
        # Lapisan akhir untuk mengubah pemikiran AI menjadi 1 angka probabilitas
        self.fc = nn.Linear(hidden_size, 1) 

    def forward(self, x):
        # x menerima data runtun waktu (misal: 60 hari ke belakang)
        out, _ = self.lnn(x)
        # Kita hanya mengambil kesimpulan di hari terakhir (out[:, -1, :])
        return self.fc(out[:, -1, :])

# =====================================================================
# 2. BAGIAN MAKRO: RAPAT DIREKSI (MIXTURE OF EXPERTS)
# =====================================================================
class LiquidMoE(nn.Module):
    def __init__(self, num_experts, input_size, hidden_size):
        super().__init__()
        self.num_experts = num_experts
        
        # Merekrut beberapa Pakar Liquid sekaligus (Misal: 3 Pakar)
        self.experts = nn.ModuleList([LiquidExpert(input_size, hidden_size) for _ in range(num_experts)])
        
        # Merekrut Sang Manajer (Gating Network)
        # Tugasnya HANYA melihat data hari ini, lalu menentukan persentase 
        # kepercayaan kepada masing-masing Pakar.
        self.gate = nn.Sequential(
            nn.Linear(input_size, 16),
            nn.ReLU(),
            nn.Linear(16, num_experts),
            nn.Softmax(dim=-1) # Memastikan total bobot kepercayaan persis 1.0 (100%)
        )
    def forward(self, x):
    # 1. Sang Manajer melihat indikator hari ini (baris terakhir)
            gate_input = x[:, -1, :] 
            weights = self.gate(gate_input) # Output contoh: [0.1, 0.8, 0.1]
            
            # Tempat penampung jawaban akhir
            final_output = torch.zeros(x.size(0), 1, device=x.device)

            # 2. Rapat Dimulai: Gabungkan pendapat semua pakar
            for i, expert in enumerate(self.experts):
                # Biarkan si Pakar memproses data secara "cair"
                expert_prediction = expert(x) 
                
                # Kalikan prediksi Pakar dengan bobot kepercayaan dari Manajer
                # Jika Manajer memberi bobot 0.8 (80%), suara Pakar ini sangat dominan
                final_output += weights[:, i].unsqueeze(1) * expert_prediction

            return final_output

            # =====================================================================
            # 3. UJI COBA MESIN (SIMULASI ALIRAN DATA)
            # =====================================================================
            if __name__ == "__main__":
                # Konfigurasi: 16 Indikator (seperti radarmu), mengingat 30 hari ke belakang
                batch_size = 64
                sequence_length = 30
                num_features = 16
                
                # Membuat tumpukan data simulasi
                dummy_data = torch.rand(batch_size, sequence_length, num_features)
                
                # Menghidupkan Model: 3 Pakar Liquid dengan 32 memori tersembunyi
                model = LiquidMoE(num_experts=3, input_size=num_features, hidden_size=32)
                
                # Mengeksekusi tebakan
                prediksi = model(dummy_data)
                
                print("🔥 Arsitektur Liquid MoE Berhasil Dihidupkan!")
                print(f"Bentuk Output Prediksi: {prediksi.shape}")    