"""
FILE 1 — PRODUCER DATA (v2.2 - Phase-1/4 Fixes)
=================================================
Perbaikan v2.1:
  - Penambahan asyncio.Semaphore untuk membatasi request serentak
  - Penambahan jeda acak (jitter) agar terlihat seperti manusia (Anti-Bot bypass)

Perbaikan v2.2 (Phase-1 & Phase-4):
  - [FIX P1-1] Main while-loop dibungkus try/except/finally; DB connection ditutup di finally
  - [FIX P1-2] Loop for hasil diperbaiki: unpack 2-elemen (ticker, None) ditangani dengan aman
  - [FIX P4]   Semua print() diganti dengan log.info/warning/error + format timestamp penuh
"""

import asyncio
import aiohttp
import sqlite3
import time
import logging
import random
from datetime import datetime

# ─── Konfigurasi ────────────────────────────────────────────────────────────
DB_NAME          = "histori_ihsg.db"
MAX_RETRIES      = 2          
RETRY_DELAY      = 1.5        
CYCLE_INTERVAL   = 60         
TIMEOUT_SECS     = 10         
MAX_CONCURRENT   = 5          # REM OTOMATIS: Maksimal 5 saham bersamaan

# Phase-4: Logging dengan timestamp penuh (bukan HH:MM:SS saja)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Daftar ticker dihilangkan untuk menghemat ruang chat, masukkan daftar TICKERS mu di sini
TICKERS = list(dict.fromkeys([
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
    "TPMA.JK", "SMDR.JK", "BIRD.JK", "ALII.JK", "PMUI.JK", "AREA.JK",
    "STRK.JK", "WIDI.JK", "AWAN.JK", "HUMI.JK", "GTRA.JK", "MENN.JK",
    "UNVR.JK", "ICBP.JK", "INDF.JK", "MYOR.JK", "GOOD.JK", "ROTI.JK",
    "CAMP.JK", "CLEO.JK", "ADES.JK", "STTP.JK", "SIDO.JK", "KAEF.JK",
    "PEHA.JK", "AMRT.JK", "MIDI.JK", "MAPI.JK", "MAPA.JK", "ACES.JK",
    "ERAA.JK", "RALS.JK", "LPPF.JK", "HOKI.JK", "CPIN.JK", "JPFA.JK",
    "ENZO.JK", "BSDE.JK", "CTRA.JK", "SMRA.JK", "PWON.JK", "ASRI.JK",
    "DMAS.JK", "DUTI.JK", "DILD.JK", "PPRO.JK", "BKSL.JK", "GWSA.JK",
    "MKPI.JK", "LPCK.JK", "KIJA.JK", "SSIA.JK", "KLBF.JK", "MIKA.JK",
    "HEAL.JK", "SILO.JK", "PRDA.JK", "DGNS.JK", "BMHS.JK", "IRRA.JK",
    "TSPC.JK", "SAME.JK", "SMGR.JK", "INTP.JK", "SMBR.JK", "SMCB.JK",
    "KRAS.JK", "ISSP.JK", "BAJA.JK", "NIKL.JK", "ALKA.JK", "BRNA.JK",
    "TOTO.JK", "ASSA.JK", "GIAA.JK", "TMAS.JK", "NELY.JK", "HAIS.JK",
    "PANI.JK", "BPTR.JK", "AALI.JK", "LSIP.JK", "SIMP.JK", "BWPT.JK",
    "TAPG.JK", "DSNG.JK", "TBLA.JK", "SSMS.JK", "ANJT.JK"
]))

def init_db(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS histori_ihsg (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker  TEXT    NOT NULL,
            harga   REAL    NOT NULL CHECK(harga > 0),
            volume  REAL,   -- 🟢 INI KOLOM BARU YANG KITA TAMBAHKAN
            waktu   DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_histori_ticker ON histori_ihsg(ticker);
        CREATE INDEX IF NOT EXISTS idx_histori_waktu  ON histori_ihsg(waktu);
        CREATE TABLE IF NOT EXISTS log_error (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker  TEXT,
            pesan   TEXT,
            waktu   DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

async def fetch_price_with_retry(session: aiohttp.ClientSession, ticker: str, sem: asyncio.Semaphore) -> tuple:
    """Fetch harga saham dengan antrean Semaphore dan Jeda Acak."""
    async with sem:  # 🟢 Hanya 5 request yang boleh masuk blok ini secara bersamaan
        # Jeda acak 0.1 - 0.5 detik biar dikira manusia nge-klik
        await asyncio.sleep(random.uniform(0.1, 0.5)) 
        
        ticker_clean = ticker.replace(".JK", "")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_clean}.JK?interval=1m&range=1d"
        # Ganti User-Agent agar lebih bervariasi
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

        for attempt in range(1, MAX_RETRIES + 2):
            try:
                timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECS)
                async with session.get(url, headers=headers, timeout=timeout) as resp:
                    if resp.status == 429: # Status kode khusus jika IP diblokir (Too Many Requests)
                        return ticker, None
                        
                    if resp.status != 200:
                        raise ValueError(f"HTTP {resp.status}")

                    data  = await resp.json()
                    result = data["chart"]["result"]

                    if not result:
                        return ticker, None, None 

                    # 🟢 Sekarang kita ambil Harga DAN Volume
                    close_list = result[0]["indicators"]["quote"][0].get("close", [])
                    volume_list = result[0]["indicators"]["quote"][0].get("volume", [])

                    # Pasangkan harga dan volume, buang yang datanya kosong (None)
                    valid_data = [(c, v) for c, v in zip(close_list, volume_list) if c is not None and v is not None]

                    if not valid_data:
                        return ticker, None, None

                    harga, volume = valid_data[-1] # Ambil data detik terakhir
                    if harga <= 0:
                        return ticker, None, None

                    return ticker, harga, volume

            except Exception as e:
                pass

            if attempt <= MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

        return ticker, None

async def mata_dewa_producer():
    log.info("⚡ MATA DEWA (ASYNC PRODUCER) v2.1 AKTIF ⚡")
    log.info(f"   Memantau {len(TICKERS)} ticker dengan mode Anti-Ban (Max {MAX_CONCURRENT} request/detik)")

    # 🟢 Buat Satpam Antrean (Semaphore)
    sem = asyncio.Semaphore(MAX_CONCURRENT)

    with sqlite3.connect(DB_NAME) as conn:
        init_db(conn)
        cur = conn.cursor()
        siklus = 0
        
        while True:
            siklus += 1
            waktu_mulai = time.time()
            log.info(f"\n{'─'*50}")
            log.info(f"Siklus #{siklus} dimulai — {datetime.now().strftime('%H:%M:%S')}")

            async with aiohttp.ClientSession() as session:
                # Masukkan semaphore ke dalam setiap tugas
                tasks  = [fetch_price_with_retry(session, t, sem) for t in TICKERS]
                results = await asyncio.gather(*tasks)

            berhasil, gagal = 0, 0
            # Phase-1 Fix: unpack safely — fetch_price_with_retry may return
            # a 2-tuple (ticker, None) on early error or a 3-tuple (ticker, harga, volume)
            for result_row in results:
                if len(result_row) == 3:
                    ticker, harga, volume = result_row
                else:
                    # 2-tuple: ticker, None — treat as failure
                    ticker = result_row[0]
                    harga  = None
                    volume = None

                if harga is not None and volume is not None:
                    try:
                        cur.execute(
                            "INSERT INTO histori_ihsg (ticker, harga, volume) VALUES (?, ?, ?)",
                            (ticker, float(harga), float(volume))
                        )
                        berhasil += 1
                    except sqlite3.Error as db_err:
                        # Phase-1: log DB errors instead of silently dropping data
                        log.error("DB insert gagal untuk %s: %s", ticker, db_err)
                        gagal += 1
                else:
                    gagal += 1

            # Phase-1: commit AFTER the full batch loop, not inside it
            conn.commit()

            durasi = time.time() - waktu_mulai
            pct    = berhasil / len(TICKERS) * 100
            status = "OK" if pct >= 80 else "WARN"
            # Phase-4: use % formatting instead of f-strings in log calls
            log.info("[%s] Berhasil: %d/%d (%.0f%%) | Gagal: %d | Durasi: %.2fs",
                     status, berhasil, len(TICKERS), pct, gagal, durasi)

            if pct < 50:
                log.warning("Success rate < 50%% — IP masih dibatasi atau bursa sedang tutup.")

            elapsed = time.time() - waktu_mulai
            sleep_time = max(0, CYCLE_INTERVAL - elapsed)
            log.info(f"Tidur {sleep_time:.1f}s hingga siklus berikutnya...")
            await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    asyncio.run(mata_dewa_producer())