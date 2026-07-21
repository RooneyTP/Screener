"""
backfill_data.py — Data Backfill 2+ Tahun
Download historical OHLCV dari Yahoo Finance untuk semua ticker,
simpan ke Parquet untuk training AI yang lebih akurat.
GRATIS — Yahoo Finance API free.
"""

import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import time
import os
import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Daftar ticker dari WATCHLIST_SEKTOR di screener.py
ALL_TICKERS = [
    "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BRIS.JK", "BBTN.JK",
    "BNGA.JK", "BDMN.JK", "NISP.JK", "BTPS.JK", "ARTO.JK", "CFIN.JK",
    "BBYB.JK", "BVIC.JK", "BJTM.JK", "BJBR.JK", "PNBN.JK", "BSIM.JK",
    "ASII.JK", "SRTG.JK", "BMTR.JK", "BHIT.JK", "MLPL.JK", "SMMA.JK",
    "ABMM.JK", "UNTR.JK", "TPIA.JK", "LPKR.JK", "MPPA.JK", "BNLI.JK",
    "SCMA.JK", "VIVA.JK", "ADMG.JK", "TLKM.JK", "ISAT.JK", "EXCL.JK",
    "GOTO.JK", "BUKA.JK", "BELI.JK", "WIFI.JK", "EMTK.JK", "MLPT.JK",
    "MTDL.JK", "DMMX.JK", "KREN.JK", "AXIO.JK", "GLVA.JK", "ADRO.JK",
    "ITMG.JK", "PTBA.JK", "INDY.JK", "HRUM.JK", "BUMI.JK", "BRMS.JK",
    "DEWA.JK", "ENRG.JK", "MEDC.JK", "PGAS.JK", "AKRA.JK", "ANTM.JK",
    "INCO.JK", "TINS.JK", "CUAN.JK", "MBMA.JK", "NCKL.JK", "KKGI.JK",
    "DOID.JK", "ADMR.JK", "RMKE.JK", "TOBA.JK", "JSMR.JK", "PTPP.JK",
    "ADHI.JK", "WIKA.JK", "WSKT.JK", "WEGE.JK", "PPRE.JK", "TOTL.JK",
    "ACST.JK", "JKON.JK", "META.JK", "CMNP.JK", "LEAD.JK", "RIGS.JK",
    "TPMA.JK", "SMDR.JK", "BIRD.JK", "UNVR.JK", "ICBP.JK", "INDF.JK",
    "MYOR.JK", "GOOD.JK", "ROTI.JK", "CAMP.JK", "CLEO.JK", "ADES.JK",
    "STTP.JK", "SIDO.JK", "KAEF.JK", "PEHA.JK", "AMRT.JK", "MIDI.JK",
    "MAPI.JK", "MAPA.JK", "ACES.JK", "ERAA.JK", "RALS.JK", "LPPF.JK",
    "HOKI.JK", "CPIN.JK", "JPFA.JK", "ENZO.JK", "BSDE.JK", "CTRA.JK",
    "SMRA.JK", "PWON.JK", "ASRI.JK", "DMAS.JK", "DUTI.JK", "DILD.JK",
    "PPRO.JK", "BKSL.JK", "GWSA.JK", "MKPI.JK", "LPCK.JK", "KIJA.JK",
    "SSIA.JK", "KLBF.JK", "MIKA.JK", "HEAL.JK", "SILO.JK", "PRDA.JK",
    "DGNS.JK", "BMHS.JK", "IRRA.JK", "TSPC.JK", "SAME.JK", "SMGR.JK",
    "INTP.JK", "SMBR.JK", "SMCB.JK", "KRAS.JK", "ISSP.JK", "BAJA.JK",
    "NIKL.JK", "ALKA.JK", "BRNA.JK", "TOTO.JK", "ASSA.JK", "GIAA.JK",
    "TMAS.JK", "NELY.JK", "HAIS.JK", "PANI.JK", "BPTR.JK", "AALI.JK",
    "LSIP.JK", "SIMP.JK", "BWPT.JK", "TAPG.JK", "DSNG.JK", "TBLA.JK",
    "SSMS.JK", "ANJT.JK", "ALII.JK", "PMUI.JK", "AREA.JK", "STRK.JK",
    "WIDI.JK", "AWAN.JK", "HUMI.JK", "GTRA.JK", "MENN.JK",
    # IHSG index itself
    "^JKSE"
]

OUTPUT_PARQUET = "data_lake/ohlcv_historical.parquet"
BATCH_SIZE = 10  # Download 10 ticker sekaligus


def backfill(start_date: str = "2023-01-01", end_date: str | None = None):
    """
    Download historical OHLCV data untuk semua ticker.
    
    Parameters:
        start_date: "YYYY-MM-DD" — default 2+ tahun ke belakang
        end_date: "YYYY-MM-DD" — default hari ini
    """
    if end_date is None:
        end_date = datetime.date.today().isoformat()
    
    print(f"[BACKFILL] Download historical data {start_date} -> {end_date}")
    print(f"           {len(ALL_TICKERS)} tickers, batch size {BATCH_SIZE}")
    print()
    
    # Cek existing data
    existing_tickers = set()
    if os.path.exists(OUTPUT_PARQUET):
        try:
            existing = pd.read_parquet(OUTPUT_PARQUET)
            existing_tickers = set(existing['Ticker'].unique())
            print(f"[EXISTING] {len(existing_tickers)} tickers already in Parquet")
            print(f"           {len(existing)} total rows")
        except:
            pass
    
    new_data = []
    failed = []
    
    for i in range(0, len(ALL_TICKERS), BATCH_SIZE):
        batch = ALL_TICKERS[i:i+BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(ALL_TICKERS) + BATCH_SIZE - 1) // BATCH_SIZE
        
        # Skip tickers yang sudah ada kalau tidak perlu update
        batch_to_fetch = [t for t in batch if t.replace(".JK","") not in existing_tickers]
        if not batch_to_fetch:
            print(f"  [{batch_num:2d}/{total_batches}] All {len(batch)} tickers already exist — skip")
            continue
        
        print(f"  [{batch_num:2d}/{total_batches}] Downloading {len(batch_to_fetch)} tickers...", end=" ")
        
        try:
            raw = yf.download(batch_to_fetch, start=start_date, end=end_date,
                            progress=False, threads=False, auto_adjust=True)
            
            if raw.empty:
                print("EMPTY")
                continue
            
            # Extract Close prices
            if isinstance(raw.columns, pd.MultiIndex):
                close_df = raw["Close"] if "Close" in raw.columns.levels[0] else pd.DataFrame()
            else:
                # Single ticker
                close_df = pd.DataFrame({"Close": raw["Close"]}) if "Close" in raw.columns else pd.DataFrame()
                close_df.columns = [batch_to_fetch[0]]
            
            # Convert to long format
            for tkr in batch_to_fetch:
                if tkr in close_df.columns:
                    series = close_df[tkr].dropna()
                    if len(series) > 0:
                        df_tkr = pd.DataFrame({
                            "Ticker": tkr.replace(".JK", ""),
                            "Tanggal": series.index.strftime("%Y-%m-%d"),
                            "Harga": series.values
                        })
                        new_data.append(df_tkr)
            
            print(f"OK ({len(new_data)} tickers collected so far)")
            
        except Exception as e:
            print(f"FAILED: {e}")
            failed.extend(batch_to_fetch)
        
        time.sleep(0.5)  # Rate limiting
    
    # Gabungkan semua data
    if not new_data:
        print("\n[DONE] No new data to save.")
        return
    
    df_new = pd.concat(new_data, ignore_index=True)
    df_new['Tanggal'] = pd.to_datetime(df_new['Tanggal'])
    
    # Merge dengan existing
    if os.path.exists(OUTPUT_PARQUET):
        try:
            df_existing = pd.read_parquet(OUTPUT_PARQUET)
            df_existing['Tanggal'] = pd.to_datetime(df_existing['Tanggal'])
            df_all = pd.concat([df_existing, df_new], ignore_index=True)
            df_all = df_all.drop_duplicates(subset=['Ticker', 'Tanggal'], keep='last')
        except:
            df_all = df_new
    else:
        df_all = df_new
    
    os.makedirs("data_lake", exist_ok=True)
    df_all.to_parquet(OUTPUT_PARQUET, engine='pyarrow', compression='snappy')
    
    print(f"\n[DONE] Saved {len(df_all):,} rows to {OUTPUT_PARQUET}")
    print(f"       Tickers: {df_all['Ticker'].nunique()}")
    print(f"       Date range: {df_all['Tanggal'].min().date()} -> {df_all['Tanggal'].max().date()}")
    print(f"       Days of data: {(df_all['Tanggal'].max() - df_all['Tanggal'].min()).days}")
    
    if failed:
        print(f"\n[WARN] {len(failed)} tickers failed: {failed[:5]}...")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-01-01", help="Start date YYYY-MM-DD")
    p.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    args = p.parse_args()
    backfill(args.start, args.end)
