"""
v6 — V4 Engine optimized for KONGLOMERAT universe
==================================================
V6 bukan engine baru — tapi V4 dengan:
  1. Universe terbatas saham konglomerat (25-30 ticker)
  2. Threshold dikalibrasi khusus untuk large caps
  3. Slippage tier Large/Mid (bukan Small/Micro)
  4. Gak ada BEARISH weekly filter (terbukti gak ngaruh)

Alasan: WR konglomerat 47.8% vs campuran 44.0% (+3.8% lebih tinggi).
Large caps predictable, fundamental lengkap, fee lebih murah.

Grup konglomerat:
  ASTRONEWS Sapta: ASII, UNTR, AKRA, AALI, CPIN, ISAT
  SALIM: INDF, ICBP, KLBF, HMSP
  DJARUM: BBCA, BBRI, BMRI, BBNI
  SINARMAS: SMRA, BSDE, ASRI
  ADARO: ADRO
  BARITO: BRPT
  BAKRIE: ENRG
  LIPPO: LPKR
  CHAROEN: CPIN, JPFA
"""

# Ticker per grup
KONGLOMERAT_TICKERS = [
    # ASTRA (Boy Thohir)
    "ASII.JK", "UNTR.JK", "AKRA.JK", "CPIN.JK", "ISAT.JK",
    # SALIM
    "INDF.JK", "ICBP.JK", "KLBF.JK", "HMSP.JK",
    # DJARUM (Hartono)
    "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK",
    # SINARMAS
    "SMRA.JK", "BSDE.JK", "ASRI.JK",
    # ADARO (Boy Thohir)
    "ADRO.JK",
    # BARITO
    "BRPT.JK",
    # BAKRIE
    "ENRG.JK",
    # LIPPO
    "LPKR.JK",
    # Tambahan blue chip pendukung
    "TLKM.JK", "UNVR.JK", "GGRM.JK", "MYOR.JK", "SIDO.JK",
    "BJBR.JK", "BJTM.JK", "BRIS.JK", "BBTN.JK",
    "ADMR.JK", "PTBA.JK", "PGAS.JK", "EXCL.JK",
    "TOWR.JK", "TBIG.JK", "MTEL.JK",
]

# Threshold khusus large caps (lebih rendah karena WR lebih bagus)
# Berdasarkan backtest konglomerat 2.500+ sinyal
THRESHOLDS = {
    "BULL":            [65, 55, 48, 40, 32],
    "BEAR":            [60, 52, 45, 38, 30],
    "RANGING":         [62, 52, 45, 38, 30],
    "HIGH_VOLATILITY": [62, 52, 45, 38, 30],
}

import logging
logger = logging.getLogger("v6")
enabled: bool = False

def configure(cfg: dict):
    global THRESHOLDS
    if cfg:
        if "thresholds" in cfg:
            THRESHOLDS.update(cfg["thresholds"])
        # Sync ke scoring module (biar V4 pake threshold V6)
        try:
            import scoring as sc
            sc.THRESHOLDS.update(THRESHOLDS)
        except:
            pass

def is_enabled(): return enabled
