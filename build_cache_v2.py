"""Build KSEI PDF cache v2 — dual format, page 2+"""
import os,sys,glob,re,time
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from datetime import datetime

D=os.path.join(os.path.dirname(os.path.abspath(__file__)),"pemegangSaham")
CACHE=os.path.join(D,"_cache.parquet")
os.makedirs(D,exist_ok=True)

files=sorted(glob.glob(os.path.join(D,"*.pdf")))
if not files: print("ERROR: No PDF!"); sys.exit(1)

def parse(fpath):
    import pdfplumber
    rows,dt=[],None
    try:
        with pdfplumber.open(fpath) as pdf:
            for p in pdf.pages[:3]:
                t=p.extract_text() or ""
                m=re.search(r'per\s+(\d{1,2}-[A-Z]{3}-\d{4})',t)
                if m: dt=datetime.strptime(m.group(1),"%d-%b-%Y"); break
            for pi in range(1,len(pdf.pages)):
                for tbl in pdf.pages[pi].extract_tables():
                    if not tbl or len(tbl)<3: continue
                    h=[str(c).strip().upper() if c else "" for c in tbl[0]]
                    if "KODE EFEK" in h and "NAMA PEMEGANG SAHAM" in h:
                        ic,inn=h.index("KODE EFEK"),h.index("NAMA PEMEGANG SAHAM")
                        ish=next((i for i,c in enumerate(h) if c and "KEPEMILIKAN PER" in c),None)
                        if ish is None: continue
                        ilok=next((i for i,c in enumerate(h) if c and "LOKAL/ASING" in c),None)
                        for r in tbl[1:]:
                            if not r or len(r)<=max(ic,inn,ish): continue
                            tkr=str(r[ic]or"").strip().upper()
                            nm=str(r[inn]or"").strip()
                            s=str(r[ish]or"").replace(",","").replace(".","")
                            if not tkr or not nm or not s.isdigit(): continue
                            lok=str(r[ilok]).strip().upper() if ilok and r[ilok] else ""
                            klass="MM" if "ASING" in lok else ("RETAIL" if len(nm.split())<=3 and "PT " not in nm.upper() else "MM")
                            rows.append({"ticker":tkr,"name":nm,"category":lok,"shares":float(s),"pct":0.0,"classification":klass,"date":dt,"source_file":os.path.basename(fpath)})
                    elif "SHARE_CODE" in h and "TOTAL_HOLDING_SHARES" in h:
                        ic,ish=h.index("SHARE_CODE"),h.index("TOTAL_HOLDING_SHARES")
                        inn=h.index("INVESTOR_NAME") if "INVESTOR_NAME" in h else None
                        icl=h.index("INVESTOR_CLASSIFICATION") if "INVESTOR_CLASSIFICATION" in h else None
                        ipc=h.index("PERCENTAGE") if "PERCENTAGE" in h else None
                        if inn is None: continue
                        KM={"Individual":"RETAIL"}
                        for r in tbl[1:]:
                            if not r or len(r)<=max(ic,inn,ish): continue
                            tkr=str(r[ic]or"").strip().upper()
                            nm=str(r[inn]or"").strip()
                            cat=str(r[icl]).strip() if icl and r[icl] else ""
                            s=str(r[ish]or"").replace(",","").replace(".","")
                            if not s.isdigit(): continue
                            pct=0.0
                            if ipc and len(r)>ipc and r[ipc]:
                                try: pct=float(str(r[ipc]).replace(",","."))
                                except: pass
                            rows.append({"ticker":tkr,"name":nm,"category":cat,"shares":float(s),"pct":pct,"classification":KM.get(cat,"MM"),"date":dt,"source_file":os.path.basename(fpath)})
    except Exception as e: print(f" ERR:{e}",end="")
    return rows,dt

print(f"Parsing {len(files)} PDFs (page 2+, >5% + >1%)...")
t0,all_rows=time.time(),[]
for i,f in enumerate(files):
    fn=os.path.basename(f); t1=time.time()
    rows,dt=parse(f)
    ds=dt.strftime("%Y-%m-%d") if dt else "???"
    print(f"  [{i+1:2d}/{len(files)}] {fn}: {len(rows):5d}r ({ds}, {time.time()-t1:.0f}s)")
    all_rows.extend(rows)

if not all_rows:
    print("\nFAIL!")
    sys.exit(1)

df = pd.DataFrame(all_rows)
nmm = int((df["classification"] == "MM").sum())
nr = int((df["classification"] == "RETAIL").sum())
nt = df["ticker"].nunique()
print(f"\nOK: {len(df):,} rows | {nt} tickers | MM={nmm:,} RETAIL={nr:,}")

df = df.sort_values("date", ascending=False).drop_duplicates(subset=["ticker", "name"], keep="first")
df.to_parquet(CACHE, index=False)
print(f"Cache: {CACHE} ({time.time() - t0:.0f}s)")
print("READY! python telegram_bot.py")
