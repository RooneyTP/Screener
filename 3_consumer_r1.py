import time
import requests
import numpy as np
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO
from mini_broker import baca_pesan, init_broker

# 🔥 PASTE WEBHOOK URL DISCORD KAMU DI SINI:
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1497448578312835082/L_lkCmGrKEByeKwHeRaoycT9JS2QGjU_Mln6sekuzEvhBlOgkiwgfi8_NBww0iHgrD8G"

def kirim_notif_automaton(pesan_teks):
    """Fungsi penembak pesan langsung ke server Discord"""
    if DISCORD_WEBHOOK_URL == "https://discord.com/api/webhooks/1497448578312835082/L_lkCmGrKEByeKwHeRaoycT9JS2QGjU_Mln6sekuzEvhBlOgkiwgfi8_NBww0iHgrD8G":
        return # Abaikan jika URL belum diisi
    
    payload = {
        "content": pesan_teks,
        "username": "Automaton RL-Engine"
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"Gagal mengirim ke Discord: {e}")

class IHSGEnv(gym.Env):
    def __init__(self):
        super(IHSGEnv, self).__init__()
        self.action_space = spaces.Discrete(3) 
        self.observation_space = spaces.Box(low=0, high=np.inf, shape=(3,), dtype=np.float32)
        self.sisa_modal = 10_000_000.0

    def step(self, action):
        return np.array([10000, 75.0, self.sisa_modal], dtype=np.float32), 1.0, True, False, {}

    def reset(self, seed=None):
        return np.array([10000, 50.0, self.sisa_modal], dtype=np.float32), {}

def jalankan_rl_agent():
    init_broker()
    print("🤖 RL EXECUTOR AKTIF: Menyiapkan agen...")
    env = IHSGEnv()
    
    print("⏳ Melatih agen PPO baru (Simulasi singkat)...")
    model_rl = PPO("MlpPolicy", env, verbose=0)
    model_rl.learn(total_timesteps=100)
    print("✅ Agen RL Siap Eksekusi!\n")

    while True:
        sinyal = baca_pesan(topik="sinyal_ai")
        
        if sinyal:
            obs = np.array([sinyal['harga'], sinyal['prob_cuan'], env.sisa_modal], dtype=np.float32)
            
            action, _states = model_rl.predict(obs)
            action = int(action)
            aksi_teks = ["HOLD (Diam) ⏸️", "BELI (Inject Modal) 🟢", "JUAL (Amankan Profit) 🔴"][action]
            
            # Format pesan yang akan dikirim ke Discord
            keputusan = (
                f"🚨 **AUTOMATON EXECUTION SIGNAL** 🚨\n"
                f"**Ticker:** {sinyal['ticker']}\n"
                f"**Harga:** Rp{sinyal['harga']}\n"
                f"**Probabilitas AI:** {sinyal['prob_cuan']:.1f}%\n"
                f"**Keputusan RL:** {aksi_teks}\n"
                f"----------------------------------------"
            )
            
            print(keputusan)
            kirim_notif_automaton(keputusan) # Tembak ke Discord!
        else:
            time.sleep(1)

if __name__ == "__main__":
    jalankan_rl_agent()