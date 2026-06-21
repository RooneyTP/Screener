"""
broker_scraper.py — Analisis Broksum IHSG
⚠️ STATUS: SIMULASI — data top_buyers/top_sellers adalah HARDCODED
   untuk keperluan testing / prototyping. Belum terhubung ke API riil.
"""
import requests

# Klasifikasi Broker IHSG
BROKER_ASING_INSTITUSI = ["ZP", "BK", "CS", "RX", "CG", "AK", "KZ", "DX"]
BROKER_RITEL = ["YP", "PD", "NI", "CC", "XC", "SQ", "XL"]

# ⚠️ FLAG SIMULASI: Set True untuk mode data palsu, False jika sudah ada API riil
SIMULASI_DATA = True

def analisis_broksum(ticker: str) -> dict:
    """
    ⚠️ DATA SIMULASI — BELUM TERHUBUNG KE API RIIL ⚠️
    
    Menarik data Top 3 Buyer dan Top 3 Seller dari portal broksum.
    (Ganti URL_API_BROKSUM dengan endpoint / URL API dari sekuritas atau penyedia data)
    """
    ticker_bersih = ticker.replace(".JK", "")
    hasil = {
        "status_bandar": "NEUTRAL",
        "akumulasi_bersih": 0,
        "rasio_top3": 0.0,
        "catatan": "⚠️ DATA SIMULASI — gunakan hanya untuk prototyping"
    }
    
    try:
        # CONTOH PENARIKAN DATA (Disesuaikan dengan API yang kamu miliki nanti)
        # url = f"https://api.penyedia-data.com/v1/broksum/{ticker_bersih}?date=today"
        # response = requests.get(url, headers={"Authorization": "Bearer TOKEN_KAMU"})
        # data = response.json()
        
        if SIMULASI_DATA:
            # --- SIMULASI DATA RESPONS API ---
            data = {
                "top_buyers": [{"broker": "ZP", "net_vol": 50000}, {"broker": "BK", "net_vol": 30000}],
                "top_sellers": [{"broker": "YP", "net_vol": 40000}, {"broker": "PD", "net_vol": 25000}]
            }
        else:
            # --- REAL API SCRAPING (placeholder) ---
            # url = f"https://api.penyedia-data.com/v1/broksum/{ticker_bersih}?date=today"
            # resp = requests.get(url, timeout=10)
            # data = resp.json()
            data = {
                "top_buyers": [],
                "top_sellers": []
            }
        
        total_vol_buyer = sum(b['net_vol'] for b in data['top_buyers'])
        total_vol_seller = sum(s['net_vol'] for s in data['top_sellers'])
        
        pembeli_asing = sum(1 for b in data['top_buyers'] if b['broker'] in BROKER_ASING_INSTITUSI)
        penjual_ritel = sum(1 for s in data['top_sellers'] if s['broker'] in BROKER_RITEL)
        
        # LOGIKA BANDARMOLOGI:
        # Jika Top Buyer adalah Institusi Asing dan Top Seller adalah Ritel
        # (Artinya bandar tampung barang ritel yang panik/take profit)
        if pembeli_asing >= 1 and penjual_ritel >= 1 and (total_vol_buyer > total_vol_seller):
            hasil["status_bandar"] = "BIG_ACCUMULATION"
            hasil["akumulasi_bersih"] = total_vol_buyer - total_vol_seller
        elif penjual_ritel == 0 and pembeli_asing == 0 and (total_vol_seller > total_vol_buyer):
             hasil["status_bandar"] = "DISTRIBUTION"
             
        hasil["rasio_top3"] = total_vol_buyer / total_vol_seller if total_vol_seller > 0 else 1.0

    except Exception as e:
        hasil["status_bandar"] = "ERROR"
        hasil["catatan"] = f"⚠️ Gagal scraping: {e}"
        
    return hasil
