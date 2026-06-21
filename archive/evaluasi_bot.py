import sqlite3
import pandas as pd
import yfinance as yf
import datetime
from colorama import Fore, Style, init

# Inisialisasi warna terminal
init(autoreset=True)

def evaluasi_performa():
    print(f"{Fore.CYAN}{Style.BRIGHT}📊 MEMULAI AUDIT PERFORMA AI & SCREENER...{Style.RESET_ALL}\n")
    
    # 1. Ambil data dari Database
    try:
        conn = sqlite3.connect("histori_ihsg.db")
        df_histori = pd.read_sql("SELECT * FROM hasil_screener", conn)
        conn.close()
    except Exception as e:
        print(f"{Fore.RED}❌ Gagal membaca database: {e}")
        return

    if df_histori.empty:
        print(f"{Fore.YELLOW}⚠️ Database masih kosong. Jalankan screener dulu!")
        return

    # [PERBAIKAN ZONA WAKTU]
    df_histori['Tanggal'] = pd.to_datetime(df_histori['Tanggal']).dt.tz_localize(None)
    
    # 2. Ambil data 7 hari terakhir untuk diaudit
    minggu_lalu = pd.Timestamp.now().tz_localize(None) - datetime.timedelta(days=7)
    df_audit = df_histori[df_histori['Tanggal'] >= minggu_lalu].copy()
    
    if df_audit.empty:
        print(f"{Fore.YELLOW}⚠️ Tidak ada data baru dalam 7 hari terakhir untuk diaudit.")
        return

    print(f"🔎 Mengaudit {len(df_audit)} sinyal dari seminggu terakhir...")

    # 3. Ambil data asli dari Yahoo Finance (BATCH DOWNLOAD)
    tickers = df_audit['Ticker'].unique()
    tickers_full = [tkr if tkr.endswith(".JK") else f"{tkr}.JK" for tkr in tickers]
    
    try:
        data_raw = yf.download(tickers_full, period="10d", progress=False)
        
        if not data_raw.empty:
            if len(tickers_full) == 1:
                tkr_tunggal = tickers_full[0]
                data_high = pd.DataFrame({tkr_tunggal: data_raw['High']})
                data_low = pd.DataFrame({tkr_tunggal: data_raw['Low']})
            else:
                data_high = data_raw['High']
                data_low = data_raw['Low']
                
            data_high.index = data_high.index.tz_localize(None)
            data_low.index = data_low.index.tz_localize(None)
        else:
            data_high, data_low = pd.DataFrame(), pd.DataFrame()
            
    except Exception as e:
        print(f"{Fore.RED}❌ Gagal download data: {e}")
        data_high, data_low = pd.DataFrame(), pd.DataFrame()

    # 4. Hitung Skor Realita dengan Persentase Cuan/Loss
    hasil_final = []
    for _, row in df_audit.iterrows():
        tkr = row['Ticker']
        tgl_sinyal = row['Tanggal']
        
        try:
            target_val = float(row['Target_1'])
            sl_val = float(row['Stop_Loss'])
            # Ambil harga beli (Entry) untuk hitung persentase
            entry_val = float(row.get('Harga', target_val * 0.95)) # Fallback aman
        except (ValueError, KeyError, TypeError):
            continue  
        
        prob_ai = row.get('AI_Win_Prob%', 0)
        verdict_ai = row.get('AI_Verdict', 'TIDAK DIUJI')

        full_tkr = tkr if tkr.endswith(".JK") else f"{tkr}.JK"
        
        if full_tkr in data_high.columns:
            harga_h_nanti = data_high[full_tkr][data_high.index > tgl_sinyal].dropna()
            harga_l_nanti = data_low[full_tkr][data_low.index > tgl_sinyal].dropna()
            
            if not harga_h_nanti.empty and not harga_l_nanti.empty:
                status = "HOLDING/PENDING"
                realized_pct = 0.0 # Default persentase
                
                for tanggal_cek in harga_h_nanti.index:
                    h_price = harga_h_nanti[tanggal_cek]
                    l_price = harga_l_nanti[tanggal_cek]
                    
                    # Logika Persentase
                    if h_price >= target_val:
                        status = "✅ PROFIT (HIT TP)"
                        realized_pct = ((target_val - entry_val) / entry_val) * 100 if entry_val > 0 else 0
                        break
                    elif l_price <= sl_val:
                        status = "❌ LOSS (HIT SL)"
                        realized_pct = ((sl_val - entry_val) / entry_val) * 100 if entry_val > 0 else 0
                        break
                
                hasil_final.append({
                    "Ticker": tkr,
                    "Tanggal": tgl_sinyal.strftime('%Y-%m-%d'),
                    "Prediksi_AI": prob_ai,
                    "Verdict": verdict_ai,
                    "Status_Real": status,
                    "Realized_Pct": realized_pct
                })

    df_hasil = pd.DataFrame(hasil_final)
    
    # 5. TAMPILKAN RAPOR FINAL
    print(f"\n{Fore.WHITE}{Style.BRIGHT}{'='*75}")
    print(f"{Fore.GREEN}             🏆 RAPOR AKURASI AI & SCREENER 🏆           ")
    print(f"{Fore.WHITE}{Style.BRIGHT}{'='*75}")
    
    if not df_hasil.empty:
        daftar_verdict = df_hasil['Verdict'].unique()
        
        for vdc in daftar_verdict:
            sub = df_hasil[df_hasil['Verdict'] == vdc]
            
            # Pisahkan data cuan dan boncos
            win_df = sub[sub['Status_Real'] == "✅ PROFIT (HIT TP)"]
            loss_df = sub[sub['Status_Real'] == "❌ LOSS (HIT SL)"]
            
            win = int(len(win_df))
            loss = int(len(loss_df))
            total_selesai = win + loss
            
            rate = (win / total_selesai * 100) if total_selesai > 0 else 0
            
            # Hitung rata-rata
            avg_profit = win_df['Realized_Pct'].mean() if not win_df.empty else 0
            avg_loss = loss_df['Realized_Pct'].mean() if not loss_df.empty else 0
            
            # Hitung TOTAL AKUMULASI (Net PnL)
            total_profit = win_df['Realized_Pct'].sum() if not win_df.empty else 0
            total_loss = loss_df['Realized_Pct'].sum() if not loss_df.empty else 0
            net_pnl = total_profit + total_loss
            
            if "AMAN" in str(vdc): color = Fore.GREEN
            elif "SPEKULATIF" in str(vdc): color = Fore.YELLOW
            elif "JEBAKAN" in str(vdc): color = Fore.RED
            else: color = Fore.WHITE
            
            print(f"{color}{str(vdc):<25}: {win}/{total_selesai} Sukses ({rate:.1f}% Win Rate)")
            
            # Tampilkan metrik detail jika ada transaksi yang selesai
            if total_selesai > 0:
                print(f"{color}{' ' * 27} 📈 Rata-rata: +{avg_profit:.1f}% (Cuan) | {avg_loss:.1f}% (Loss)")
                
                # Tampilkan Net PnL dengan warna dinamis (Hijau kalau untung, Merah kalau rugi)
                pnl_color = Fore.GREEN if net_pnl > 0 else Fore.RED
                print(f"{color}{' ' * 27} 💰 TOTAL BERSIH: {pnl_color}{net_pnl:+.1f}%{color} dari {total_selesai} transaksi")
            print(f"{Fore.WHITE}{'-'*75}")

    print(f"\n")
    
    # 6. Tampilkan 5 sinyal cuan terbaru dengan persentasenya
    try:
        cuan_only = df_hasil[df_hasil['Status_Real'] == "✅ PROFIT (HIT TP)"].tail(5)
        if not cuan_only.empty:
            print(f"{Fore.CYAN}⭐ SINYAL CUAN TERBARU:")
            for _, r in cuan_only.iterrows():
                print(f"   • {r['Ticker']} ({r['Tanggal']}) - AI: {r['Prediksi_AI']}% | Cuan: +{r['Realized_Pct']:.1f}%")
    except Exception as e:
        pass

if __name__ == "__main__":
    evaluasi_performa()
