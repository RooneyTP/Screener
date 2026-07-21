"""
shareholder_analyzer.py — Pemegang Saham Analyzer v3.0 (Cached)
==============================================================
Reads KSEI shareholder data from pemegangSaham/ folder (CSV files).
Parses all CSVs ONCE -> caches to parquet -> instant reads.
Auto-detects new files and rebuilds cache.

Classification: KSEI classes -> MM/RETAIL via KSEI_CLASS_MAP
Tracks: MM accumulation/distribution trends across dates.
Free float: shares_outstanding - large holders (>5%)
"""
import os, glob, re
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SHAREHOLDER_DIR = os.path.join(ROOT, "pemegangSaham")
CACHE_PATH = os.path.join(SHAREHOLDER_DIR, "_cache.parquet")

# KSEI classification map
KSEI_CLASS_MAP = {
    "Individual": "RETAIL",
    "Financial Institutional": "MM", "Corporate": "MM",
    "Insurance": "MM", "State Owned Enterprises": "MM",
    "State Owned Company": "MM", "Private Equity": "MM",
    "Mutual Funds": "MM", "Bank": "MM",
    "Securities Company": "MM", "Investment Manager": "MM",
    "Investment Advisors": "MM", "Pension Funds": "MM",
    "Foundation": "MM", "Cooperatives": "MM",
    "Government": "MM", "Sovereign Wealth Fund": "MM",
    "Capital Market Supporting Institutions And Professions": "MM",
    "Trustee Bank": "MM", "Venture Capital": "MM", "Firm": "MM",
    "Partnership": "MM", "Brokerage Firms": "MM",
    "Peer To Peer Lending": "MM", "Investment Fund Selling Agent": "MM",
    "Exchange Traded Funds": "MM", "Hedge Fund": "MM",
    "Private Bank": "MM", "Sole Proprietorship": "MM",
    "International Organization": "MM", "Diocese": "MM",
    "Educational Institution": "MM",
}

MM_KEYWORDS = [
    "institusi", "asing", "bank", "sekuritas", "asuransi", "dana pensiun",
    "reksa dana", "fund", "capital", "asset management", "investment",
    "pt ", "cv ", "yayasan", "koperasi", "bpjs", "taspen", "jiwasraya",
]
RETAIL_KEYWORDS = ["individu", "perorangan", "pribadi", "personal", "masyarakat"]


def _classify_shareholder(name, category="", pct=0.0):
    """Classify shareholder as MM, RETAIL, or INSIDER.
    
    INSIDER = individual name with large ownership (>5%)
    MM = institution, corporation, fund, etc.
    RETAIL = small individual investors
    """
    if category and category in KSEI_CLASS_MAP:
        return KSEI_CLASS_MAP[category]
    text = f"{name} {category}".lower().strip()
    
    # Cek insider: nama individu dengan kepemilikan >5%
    # Biasanya nama orang Indonesia (2-4 kata) tanpa keyword institusi
    if any(k in text for k in MM_KEYWORDS):
        return "MM"
    if any(k in text for k in RETAIL_KEYWORDS):
        return "RETAIL"
    
    # Deteksi insider: individu dengan >5% kepemilikan
    # Insider biasanya: nama orang (2-3 kata), bukan PT/CV/Yayasan
    is_individual = not any(kw in text for kw in ["pt ", "pt.", "cv ", "yayasan", "koperasi",
        "bank", "sekuritas", "asuransi", "fund", "capital", "investment", "limited", "ltd",
        "corp", "inc", "company", "group", "holding", "management"])
    
    if is_individual and pct >= 5.0:
        return "INSIDER"
    
    if len(name.split()) >= 3:
        return "MM"
    return "RETAIL"


def _extract_date_from_pdf(filepath):
    """Extract date from PDF (DEPRECATED — all data now from CSV)."""
    return None


# Cache Manager
def _is_cache_stale():
    if not os.path.exists(CACHE_PATH):
        return True
    try:
        import pandas as pd
        cache = pd.read_parquet(CACHE_PATH)
        cached_files = set(cache["source_file"].dropna().unique())
        current_files = set(os.path.basename(f) for f in
                          glob.glob(os.path.join(SHAREHOLDER_DIR, "*.csv")))
        return not cached_files >= current_files
    except Exception:
        return True


def _parse_indonesian_number(s: str) -> float:
    """Konversi angka format Indonesia (1.500,50) ke float."""
    s = s.strip().replace(" ", "")
    if not s:
        return 0.0
    if "," in s:
        # Punya desimal: "20,27" atau "1.500,50"
        s = s.replace(".", "")   # hapus separator ribuan
        s = s.replace(",", ".")  # ganti koma desimal jadi titik
    else:
        # Bilangan bulat: "404.380.443" → hapus semua titik
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _find_latest_column(columns: list, prefix: str = "Kepemilikan Per") -> str | None:
    """Cari kolom dengan tanggal terbaru dari prefix tertentu."""
    from datetime import datetime as _dt
    candidates = [c for c in columns if c and prefix in str(c)]
    if not candidates:
        return None
    best_col, best_dt = candidates[0], _dt.min
    for c in candidates:
        # Extract date from column name: "Kepemilikan Per 21-MAY-2026"
        parts = str(c).rsplit(" ", 1)
        if len(parts) == 2:
            try:
                dt = _dt.strptime(parts[1], "%d-%b-%Y")
                if dt > best_dt:
                    best_dt, best_col = dt, c
            except (ValueError, IndexError):
                continue
    return best_col


def _parse_all_pdfs():
    import pandas as pd
    rows = []

    if not os.path.isdir(SHAREHOLDER_DIR):
        return pd.DataFrame()

    # Baca semua file CSV di folder pemegangSaham/
    csv_files = sorted(glob.glob(os.path.join(SHAREHOLDER_DIR, "*.csv")))
    for cf in csv_files:
        fname = os.path.basename(cf)
        if fname == "_cache.parquet":
            continue
        try:
            # Auto-detect delimiter: KSEI = semicolon, data_tambahan = comma
            with open(cf, "r", encoding="utf-8-sig", errors="replace") as fh:
                header_line = fh.readline()
                sep = ";" if ";" in header_line else ","
            csv_df = pd.read_csv(cf, sep=sep, low_memory=False)
            if csv_df.empty:
                continue

            # Deteksi format: KSEI export atau data_tambahan
            kode_cols = [c for c in csv_df.columns if "Kode Efek" in str(c)]
            nama_cols = [c for c in csv_df.columns if "Nama Pemegang Saham" in str(c)]

            if kode_cols and nama_cols:
                # ── Format KSEI export (semicolon-delimited) ──
                kode_col = kode_cols[0]
                nama_col = nama_cols[0]

                # Cari kolom Kepemilikan dengan tanggal TERAKHIR
                shares_col = _find_latest_column(list(csv_df.columns), "Kepemilikan Per")
                if not shares_col:
                    continue

                # Cari kolom Status (Lokal/Asing)
                status_cols = [c for c in csv_df.columns
                               if str(c).strip() in ("Status (Lokal/Asing)", "Status(Lokal/Asing)", "LOKAL/ASING")]
                status_col = status_cols[0] if status_cols else None

                for _, row in csv_df.iterrows():
                    try:
                        ticker = str(row.get(kode_col, "")).strip().upper()
                        name = str(row.get(nama_col, "")).strip()
                        if not ticker or not name or ticker in ("NAN", "N/A", "") or name.lower() in ("nan", "null", "-", ""):
                            continue

                        shares = _parse_indonesian_number(str(row.get(shares_col, "0")))
                        if shares <= 0:
                            continue

                        # Tentukan klasifikasi dari Status (Lokal/Asing)
                        lok_str = ""
                        classification = "RETAIL"
                        if status_col:
                            lok_str = str(row.get(status_col, "")).strip().upper()
                            if lok_str in ("A", "ASING") or "ASING" in lok_str:
                                classification = "MM"
                            elif any(kw in name.upper() for kw in ["PT ", "PT.", "CV ", "YAYASAN", "KOPERASI"]):
                                classification = "MM"
                        else:
                            # Fallback: deteksi dari nama
                            if any(kw in name.upper() for kw in ["PT ", "PT.", "CV ", "YAYASAN", "KOPERASI",
                                   "BANK", "SEKURITAS", "ASURANSI", "FUND", "LIMITED", "LTD", "CORP", "INC"]):
                                classification = "MM"

                        rows.append({
                            "ticker": ticker, "name": name,
                            "category": lok_str, "shares": shares,
                            "pct": 0.0, "classification": classification,
                            "date": None, "source_file": fname,
                        })
                    except Exception:
                        pass
            else:
                # ── Format data_tambahan (comma-delimited) ──
                csv_df["source_file"] = fname
                if "date" in csv_df.columns:
                    csv_df["date"] = pd.to_datetime(csv_df["date"])
                rows.extend(csv_df.to_dict("records"))
        except Exception:
            pass

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("date", ascending=False).drop_duplicates(
        subset=["ticker", "name"], keep="first")
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    df.to_parquet(CACHE_PATH, index=False)
    return df


def _get_cached_df():
    import pandas as pd
    if not os.path.exists(CACHE_PATH):
        return pd.DataFrame()
    return pd.read_parquet(CACHE_PATH)


def _parse_pdf_list(file_basenames):
    """Parse a specific list of PDF files (DEPRECATED — all data now from CSV)."""
    raise NotImplementedError("PDF parsing is deprecated — all KSEI data now comes from CSV files")


def _parse_single_pdf(fpath, fname, file_date, _unused):
    """Extract shareholder rows from a single PDF (DEPRECATED — all data now from CSV)."""
    raise NotImplementedError("PDF parsing is deprecated — all KSEI data now comes from CSV files")


def analyze_shareholder_structure(ticker, total_shares_outstanding=0, track_trend=False, float_shares=0):
    """Analyze shareholder structure from KSEI data.
    
    Args:
        ticker: Stock ticker
        total_shares_outstanding: Total shares outstanding from CSV screener
        track_trend: Whether to track MM accumulation/distribution trend
        float_shares: Free float shares from CSV screener (Float_Shares column)
    """
    df = _get_cached_df()
    ticker_upper = ticker.strip().upper().replace(".JK", "")

    if df.empty:
        return {
            "ticker": ticker_upper, "status": "no_data",
            "mm_pct": 0, "retail_pct": 0, "free_float_pct": 0,
            "dominance": "UNKNOWN", "n_holders": 0,
            "mm_trend": "N/A", "mm_trend_pct": 0,
        }

    data = df[df["ticker"] == ticker_upper]
    if data.empty:
        return {
            "ticker": ticker_upper, "status": "no_data",
            "mm_pct": 0, "retail_pct": 0, "free_float_pct": 0,
            "dominance": "UNKNOWN", "n_holders": 0,
            "mm_trend": "N/A", "mm_trend_pct": 0,
        }

    total_reported = int(data["shares"].sum())

    # Gunakan total_shares_outstanding dari CSV jika ada, fallback ke total_reported
    if total_shares_outstanding > 0:
        use_total = total_shares_outstanding
    elif total_reported > 0:
        use_total = total_reported
    else:
        use_total = 0

    if use_total > 0:
        # Hitung persentase setiap holder terhadap use_total (Shares_Outstanding)
        data = data.copy()
        data["pct"] = (data["shares"] / use_total) * 100
    else:
        data["pct"] = 0.0

    # Aggregate by classification
    grouped = data.groupby("classification").agg(
        total_shares=("shares", "sum"),
        n_holders=("name", "nunique"),
    ).reset_index()

    mm_data = grouped[grouped["classification"] == "MM"]
    retail_data = grouped[grouped["classification"] == "RETAIL"]
    insider_data = grouped[grouped["classification"] == "INSIDER"]

    mm_shares = int(mm_data["total_shares"].sum()) if not mm_data.empty else 0
    retail_shares = int(retail_data["total_shares"].sum()) if not retail_data.empty else 0
    insider_shares = int(insider_data["total_shares"].sum()) if not insider_data.empty else 0
    n_mm = int(mm_data["n_holders"].sum()) if not mm_data.empty else 0
    n_retail = int(retail_data["n_holders"].sum()) if not retail_data.empty else 0

    if use_total > 0:
        mm_pct = round((mm_shares / use_total) * 100, 1)
        retail_pct = round((retail_shares / use_total) * 100, 1)
        insider_pct = round((insider_shares / use_total) * 100, 1)
    else:
        mm_pct = retail_pct = insider_pct = 0.0

    # Dominance logic
    if mm_pct > retail_pct and mm_pct >= 30:
        dominance = "MM_DOMINANT"
    elif retail_pct > mm_pct and retail_pct >= 50:
        dominance = "RETAIL_DOMINANT"
    elif mm_pct >= 15:
        dominance = "MM_SIGNIFICANT"
    else:
        dominance = "BALANCED"

    # MM accumulation/distribution trend analysis
    mm_trend = "N/A"
    mm_trend_pct = 0
    if track_trend:
        try:
            # Group by date to see MM ownership over time
            mm_over_time = data[data["classification"] == "MM"].copy()
            if not mm_over_time.empty and "date" in mm_over_time.columns:
                mm_over_time = mm_over_time.dropna(subset=["date"])
            if not mm_over_time.empty:
                by_date = mm_over_time.groupby("date").agg(
                    total_shares=("shares", "sum"),
                )
                if len(by_date) >= 2:
                    by_date = by_date.sort_index()
                    latest = by_date.iloc[-1]["total_shares"]
                    previous = by_date.iloc[-2]["total_shares"]
                    if previous > 0:
                        diff = latest - previous
                        pct_change = round((diff / previous) * 100, 1)
                        if pct_change > 2:
                            mm_trend = "ACCUMULATING"
                        elif pct_change < -2:
                            mm_trend = "DISTRIBUTING"
                        else:
                            mm_trend = "STABLE"
                        mm_trend_pct = pct_change
                    else:
                        mm_trend = "STABLE"
        except Exception:
            mm_trend = "N/A"

    # Hitung free float: shares_outstanding - (insider shares + large holder shares >5%)
    free_float_pct = 0
    if use_total > 0:
        if float_shares > 0:
            free_float_pct = round((float_shares / use_total) * 100, 1)
        else:
            # Estimasi: hitung large holders (>5% of outstanding)
            large_holders = data[data["pct"] >= 5.0]
            large_shares = int(large_holders["shares"].sum()) if not large_holders.empty else 0
            free_shares = max(0, use_total - large_shares)
            free_float_pct = round((free_shares / use_total) * 100, 1)

    return {
        "ticker": ticker_upper,
        "status": "ok",
        "mm_pct": mm_pct,
        "retail_pct": retail_pct,
        "insider_pct": insider_pct,
        "free_float_pct": free_float_pct,
        "dominance": dominance,
        "n_holders": int(data["name"].nunique()),
        "n_mm": n_mm,
        "n_retail": n_retail,
        "mm_trend": mm_trend,
        "mm_trend_pct": mm_trend_pct,
        "total_shares_outstanding": use_total,
        "total_reported": total_reported,
        "mm_shares": mm_shares,
        "retail_shares": retail_shares,
        "insider_shares": insider_shares,
        "source": "pemegangSaham (KSEI)",
        "timestamp": datetime.now().isoformat(),
    }


def get_scoring_bonus(ticker, total_shares=0):
    analysis = analyze_shareholder_structure(ticker, total_shares, track_trend=True)
    if analysis["status"] != "ok":
        return 0, ""

    bonus = 0
    reasons = []

    if analysis["mm_pct"] >= 30:
        bonus += 5
        reasons.append(f"MM {analysis['mm_pct']:.0f}%")
    elif analysis["mm_pct"] >= 15:
        bonus += 2

    if analysis["free_float_pct"] >= 60:
        bonus += 3
        reasons.append(f"FF {analysis['free_float_pct']:.0f}%")
    elif analysis["free_float_pct"] >= 40:
        bonus += 1

    if analysis["dominance"] == "MM_DOMINANT":
        bonus += 3
        reasons.append("MM Dominant")
    elif analysis["dominance"] == "RETAIL_DOMINANT":
        bonus -= 3
        reasons.append("Retail Dominant")

    if analysis["mm_trend"] == "ACCUMULATING":
        bonus += 4
        reasons.append(f"MM Accum {analysis['mm_trend_pct']:+.1f}%")
    elif analysis["mm_trend"] == "DISTRIBUTING":
        bonus -= 4
        reasons.append(f"MM Dist {analysis['mm_trend_pct']:+.1f}%")

    reason_str = " | ".join(reasons) if reasons else ""
    return bonus, reason_str


def rebuild_cache():
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
    return _parse_all_pdfs()


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "BBCA"
    result = analyze_shareholder_structure(ticker, track_trend=True)
    import json
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
