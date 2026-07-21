"""
Build KSEI PDF cache — standalone with per-file progress
"""
import os, sys, glob, re, time, pandas as pd
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(ROOT, "pemegangSaham")
CACHE = os.path.join(D, "_cache.parquet")

KSEI_MAP = {
    "Individual": "RETAIL", "Financial Institutional": "MM", "Corporate": "MM",
    "Insurance": "MM", "State Owned Enterprises": "MM", "State Owned Company": "MM",
    "Private Equity": "MM", "Mutual Funds": "MM", "Bank": "MM",
    "Securities Company": "MM", "Investment Manager": "MM",
    "Investment Advisors": "MM", "Pension Funds": "MM", "Foundation": "MM",
    "Cooperatives": "MM", "Government": "MM", "Sovereign Wealth Fund": "MM",
    "Capital Market Supporting Institutions And Professions": "MM",
    "Trustee Bank": "MM", "Venture Capital": "MM", "Firm": "MM",
    "Partnership": "MM", "Brokerage Firms": "MM", "Peer To Peer Lending": "MM",
    "Investment Fund Selling Agent": "MM", "Exchange Traded Funds": "MM",
    "Hedge Fund": "MM", "Private Bank": "MM", "Sole Proprietorship": "MM",
    "International Organization": "MM", "Diocese": "MM", "Educational Institution": "MM",
}


def parse_pdf(fpath):
    import pdfplumber
    rows = []
    file_date = None
    try:
        with pdfplumber.open(fpath) as pdf:
            for p in pdf.pages[:3]:
                t = p.extract_text() or ""
                m = re.search(r'per\s+(\d{1,2}-[A-Z]{3}-\d{4})', t)
                if m:
                    file_date = datetime.strptime(m.group(1), "%d-%b-%Y")
                    break
            for p in pdf.pages:
                for tbl in p.extract_tables():
                    if not tbl or len(tbl) < 2:
                        continue
                    h = [str(c).strip().upper() if c else "" for c in tbl[0]]
                    if "SHARE_CODE" not in h:
                        continue
                    ic, inn, icl, ish, ipc = h.index("SHARE_CODE"), None, None, None, None
                    if "INVESTOR_NAME" in h: inn = h.index("INVESTOR_NAME")
                    if "INVESTOR_CLASSIFICATION" in h: icl = h.index("INVESTOR_CLASSIFICATION")
                    if "TOTAL_HOLDING_SHARES" in h: ish = h.index("TOTAL_HOLDING_SHARES")
                    if "PERCENTAGE" in h: ipc = h.index("PERCENTAGE")
                    if inn is None or ish is None:
                        continue
                    for r in tbl[1:]:
                        if not r or len(r) <= max(ic, inn, ish):
                            continue
                        try:
                            ticker = str(r[ic]).strip().upper()
                            name = str(r[inn]).strip()
                            cat = str(r[icl]).strip() if icl and r[icl] else ""
                            s = str(r[ish]).replace(",", "").replace(".", "")
                            if not s.isdigit():
                                continue
                            shares = float(s)
                            if shares <= 0:
                                continue
                            pct = 0.0
                            if ipc and len(r) > ipc and r[ipc]:
                                try:
                                    pct = float(str(r[ipc]).replace(",", "."))
                                except ValueError:
                                    pass
                            rows.append({
                                "ticker": ticker, "name": name, "category": cat,
                                "shares": shares, "pct": pct,
                                "classification": KSEI_MAP.get(cat, "MM"),
                                "date": file_date, "source_file": os.path.basename(fpath),
                            })
                        except Exception:
                            pass
    except Exception as e:
        print(f" ERROR: {e}")
    return rows, file_date


os.makedirs(D, exist_ok=True)
files = sorted(glob.glob(os.path.join(D, "*.pdf")))
if not files:
    print("ERROR: No PDF files!")
    sys.exit(1)

print(f"Parsing {len(files)} PDF files...")
t0 = time.time()
all_rows = []
for i, f in enumerate(files):
    fn = os.path.basename(f)
    print(f"  [{i+1}/{len(files)}] {fn}...", end=" ", flush=True)
    t1 = time.time()
    rows, dt = parse_pdf(f)
    dt_str = dt.strftime("%Y-%m-%d") if dt else "???"
    print(f"{len(rows)} rows (date={dt_str}, {time.time()-t1:.1f}s)")
    all_rows.extend(rows)

if not all_rows:
    print("ERROR: No data extracted!")
    sys.exit(1)

df = pd.DataFrame(all_rows)
print(f"\nTotal: {len(df):,} rows, {df['ticker'].nunique()} tickers")
print(f"MM: {(df['classification']=='MM').sum():,} | RETAIL: {(df['classification']=='RETAIL').sum():,}")

df = df.sort_values("date", ascending=False).drop_duplicates(subset=["ticker", "name"], keep="first")
df.to_parquet(CACHE, index=False)
print(f"Cache: {CACHE} ({time.time()-t0:.0f}s)")
print("Done! Run: python telegram_bot.py")
