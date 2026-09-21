# Teknis — cara kerja di repo ini

## Lingkungan
- **Python 3.11 + venv di `.venv/`** — venv JANGAN di-commit / di-zip; buat ulang:
  `python3 -m venv .venv && .venv/bin/pip install -r idx_alpha_screener/requirements.txt`
- Selalu set `PYTHONUTF8=1` untuk perintah Python (di Windows wajib, di Linux aman).

## Perintah penting
```bash
cd ~/screener
# Tes (WAJIB hijau sebelum menganggap pekerjaan selesai):
PYTHONUTF8=1 .venv/bin/python -m unittest discover -s idx_alpha_screener -p "test_v7.py" -v
# Uji scan cepat (aman, panggilan sedikit):
.venv/bin/python scan_ihsg.py --limit 50 --top 10
# Scan mandiri beberapa ticker:
.venv/bin/python scan_mandiri.py --tickers TLKM,BRPT --out /tmp/x.csv
# Kirim ke app tanpa benar-benar kirim:
.venv/bin/python kirim_ke_risetsaham.py --dry
# Backtest kebijakan (1 tahun data cache) — alat validasi perubahan:
.venv/bin/python backtest_v7_teknikal.py
```

## Sumber data (default: `risetsaham` — data lokal server)
- `SCREENER_DATA=risetsaham` (default): harga/IHSG = Yahoo (cache 20 jam);
  broker/asing = Stockbit.
- **Sopan**: fase-2 maks 3 worker; jangan naikkan tanpa alasan. Non-komersial.
- **Kredensial TIDAK ada di repo ini**:
  - sesi Stockbit: `/home/yuan/risetsaham/data/stockbit_session.json` (di server)
  - token kirim batch: `SCREENER_TOKEN` dibaca dari `/home/yuan/risetsaham/.env`
  - Di mesin lain: pakai mock / mode uji; JANGAN hardcode kredensial.
- Invezgo = legacy opsional (`SCREENER_DATA=invezgo` + `INVEZGO_API_KEY`).

## Konvensi
- Label & tampilan untuk user = **Bahasa Indonesia**. Teks CSV tetap ASCII
  (koma di dalam sel diganti `;`).
- Jangan commit: `.env`, kredensial, atau folder `data/` besar (sudah untracked).
- Commit kecil dengan pesan jelas. Branch aktif: `feat/integrasi-risetsaham`.
- Git remote: `git@github.com:RooneyTP/Screener.git`.
