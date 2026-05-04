import time
from mini_broker import init_broker, kirim_pesan
import random

def jalankan_producer():
    init_broker() # Nyalakan mesin broker
    print("📡 PRODUCER AKTIF: Menunggu data pasar...")

    # Simulasi loop memantau saham secara real-time
    counter = 1
    while True:
        try:
            data_baru = {
                "ticker": "BBCA",
                "harga_terakhir": 10000 + random.randint(-100, 100),
                "fitur": [
                    55.2 + random.uniform(-5, 5), # RSI goyang
                    1.5 + random.uniform(-0.5, 0.5), # MACD goyang
                    0.8 + random.uniform(-0.1, 0.1), # MM goyang
                    40000 + random.randint(-5000, 5000) # Vol goyang
                ]
            }

            kirim_pesan(topik="pasar_saham", pesan_dict=data_baru)
            print(f"🚀 [KIRIM] Data {data_baru['ticker']} Harga Rp{data_baru['harga_terakhir']} dilempar ke broker!")
            
            counter += 1
            time.sleep(5) # Lempar data tiap 5 detik
            
        except KeyboardInterrupt:
            print("\n🔌 Producer dihentikan oleh pengguna.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(2)

if __name__ == "__main__":
    jalankan_producer()