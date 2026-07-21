"""
FILE 1 -- PRODUCER DATA v3.0 (SCALPING UPGRADE)
=================================================
Upgrades:
  - Fetch Open/High/Low/Close/Volume (OHLCV) from Yahoo Finance
  - Better error handling + auto-reconnect
  - Reduced timeout (5s) for faster cycles
  - Cycle interval 30s (was 60s) for more granular data
  - Skip tickers that fail 3x in a row
"""

import asyncio
import aiohttp
import sqlite3
import time
import logging
import random
from datetime import datetime

DB_NAME          = "histori_ihsg.db"
MAX_RETRIES      = 2
RETRY_DELAY      = 1.0
CYCLE_INTERVAL   = 30   # was 60 -- faster cycle
TIMEOUT_SECS     = 5    # was 10 -- faster fail
MAX_CONCURRENT   = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

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
    "WIDI.JK", "AWAN.JK", "HUMI.JK", "GTRA.JK", "MENN.JK"
]))

# Track consecutive failures per ticker
_failures = {}

def init_db(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS histori_ihsg (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker  TEXT    NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            harga   REAL    NOT NULL CHECK(harga > 0),
            volume  REAL,
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
    """Fetch OHLCV data with retry + auto-skip failing tickers."""
    global _failures

    # Skip tickers that fail too often
    if _failures.get(ticker, 0) >= 3:
        return ticker, None, None, None, None

    async with sem:
        await asyncio.sleep(random.uniform(0.1, 0.5))

        ticker_clean = ticker.replace(".JK", "")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_clean}.JK?interval=1m&range=1d"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

        for attempt in range(1, MAX_RETRIES + 2):
            try:
                timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECS)
                async with session.get(url, headers=headers, timeout=timeout) as resp:
                    if resp.status == 429:
                        _failures[ticker] = _failures.get(ticker, 0) + 1
                        return ticker, None, None, None, None

                    if resp.status != 200:
                        raise ValueError(f"HTTP {resp.status}")

                    data = await resp.json()
                    result = data["chart"]["result"]

                    if not result:
                        _failures[ticker] = _failures.get(ticker, 0) + 1
                        return ticker, None, None, None, None

                    quotes = result[0]["indicators"]["quote"][0]
                    close_list = quotes.get("close", [])
                    open_list  = quotes.get("open", [])
                    high_list  = quotes.get("high", [])
                    low_list   = quotes.get("low", [])
                    volume_list = quotes.get("volume", [])

                    # Get last valid data point
                    valid_idx = -1
                    for idx in range(len(close_list)-1, -1, -1):
                        if close_list[idx] is not None:
                            valid_idx = idx
                            break

                    if valid_idx < 0:
                        _failures[ticker] = _failures.get(ticker, 0) + 1
                        return ticker, None, None, None, None

                    harga  = close_list[valid_idx]
                    open_p = open_list[valid_idx] if valid_idx < len(open_list) and open_list[valid_idx] is not None else harga
                    high_p = high_list[valid_idx] if valid_idx < len(high_list) and high_list[valid_idx] is not None else harga
                    low_p  = low_list[valid_idx] if valid_idx < len(low_list) and low_list[valid_idx] is not None else harga
                    vol    = volume_list[valid_idx] if valid_idx < len(volume_list) and volume_list[valid_idx] is not None else 0

                    if harga <= 0:
                        _failures[ticker] = _failures.get(ticker, 0) + 1
                        return ticker, None, None, None, None

                    # Reset failure count on success
                    _failures[ticker] = 0
                    return ticker, open_p, high_p, low_p, harga, vol

            except Exception:
                pass

            if attempt <= MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

        _failures[ticker] = _failures.get(ticker, 0) + 1
        return ticker, None, None, None, None

async def mata_dewa_producer():
    log.info("MATA DEWA v3.0 (OHLCV + Anti-Ban) AKTIF")
    log.info(f"   {len(TICKERS)} tickers, max {MAX_CONCURRENT} concurrent, {CYCLE_INTERVAL}s cycle")

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    with sqlite3.connect(DB_NAME) as conn:
        init_db(conn)
        cur = conn.cursor()
        siklus = 0

        while True:
            siklus += 1
            waktu_mulai = time.time()
            log.info(f"Siklus #{siklus} -- {datetime.now().strftime('%H:%M:%S')}")

            async with aiohttp.ClientSession() as session:
                tasks = [fetch_price_with_retry(session, t, sem) for t in TICKERS]
                results = await asyncio.gather(*tasks)

            berhasil, gagal = 0, 0
            for result_row in results:
                if len(result_row) >= 6:
                    ticker, open_p, high_p, low_p, harga, volume = result_row
                elif len(result_row) == 5:
                    ticker, open_p, high_p, low_p, harga = result_row
                    volume = 0
                else:
                    ticker = result_row[0]
                    open_p = high_p = low_p = harga = volume = None

                if harga is not None and harga > 0:
                    try:
                        cur.execute(
                            "INSERT INTO histori_ihsg (ticker, open, high, low, harga, volume) VALUES (?, ?, ?, ?, ?, ?)",
                            (ticker, open_p, high_p, low_p, float(harga), float(volume or 0))
                        )
                        berhasil += 1
                    except sqlite3.Error as db_err:
                        log.error("DB insert gagal %s: %s", ticker, db_err)
                        gagal += 1
                else:
                    gagal += 1

            conn.commit()

            durasi = time.time() - waktu_mulai
            pct = berhasil / len(TICKERS) * 100
            status = "OK" if pct >= 80 else "WARN"
            log.info("[%s] Berhasil: %d/%d (%.0f%%) | Gagal: %d | Skipped: %d | %.2fs",
                     status, berhasil, len(TICKERS), pct, gagal,
                     sum(1 for v in _failures.values() if v >= 3), durasi)

            if pct < 50:
                log.warning("Success rate < 50%% -- IP dibatasi atau bursa tutup.")

            elapsed = time.time() - waktu_mulai
            sleep_time = max(0, CYCLE_INTERVAL - elapsed)
            await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    asyncio.run(mata_dewa_producer())
