# ANALISIS ARSITEKTUR & IDENTIFIKASI BUG
## Proyek: Telegram Screener IHSG
### Path: `C:\Hermes_Workspace\Screener`
### Tanggal Analisis: 27 Juni 2026

---

## 🔴 RINGKASAN EKSEKUTIF

Proyek ini memiliki **masalah fundamental pada arsitektur instance management** yang menyebabkan bot Telegram mengalami **409 Conflict error sebanyak 293 kali** sejak 15 Mei 2026 — terus berlanjut hingga hari ini. Bot tidak pernah berfungsi dengan benar karena selalu ada **dua atau lebih instance** yang saling berebut koneksi ke Telegram API.

---

## 🏗️ ARSITEKTUR KODE — 50+ File

```
C:\Hermes_Workspace\Screener\
├── telegram_bot.py (1576 baris)      ← BOT UTAMA v6.1 (Swing + Scalp)
├── ai_agent.py (1074 baris)           ← LLM Agent multi-backend
├── daily_research_reporter_v2.py (468) ← Reporter harian + IHSG sentiment
├── daily_screening_runner.py          ← Bridge screener → JSON candidates
├── run_daily_analysis.py              ← Master runner chaining semua step
├── cron_market_summary.py             ← Cron wrapper (no_agent=True)
├── send_telegram_alert.py             ← CLI alert standalone
├── screener.py                        ← Screener utama
├── chat_memory.py                     ← Memory chat untuk AI
├── nlp_scraper.py                     ← NLP scraper
├── user_prefs.py                      ← User preferences
├── shareholder_analyzer.py            ← Analisis pemegang saham
│
├── dashboard/
│   ├── alerts.py (257 baris)          ← AlertManager (Discord + Telegram + log)
│   └── app.py                         ← Dashboard Streamlit
│
├── strategies/
│   ├── swing.py
│   └── scalp.py
│
├── src/
│   ├── signals/ (scoring, swing_strategy, ai_coordinator)
│   ├── data/ (fetcher, schema)
│   └── execution/ (sizer, slippage)
│
├── scalp/ (run, signals, ai, backtest, config, executor, producer)
├── risk/ (correlation, kill_switch)
├── core/ (scoring, scraper, file_handler)
├── utils/ (helpers, notifications)
└── tests/ (test_cache_fixes, test_risk, test_signals)
```

---

## 🔥 BUG #1 (KRITIS — URGENT): DUAL INSTANCE CONFLICT

### Gejala
- **293x** `telegram.error.Conflict: Conflict: terminated by other getUpdates request` dalam log
- **320x** `No error handlers are registered, logging exception.` — setiap 409 tidak tertangani
- Bot **TIDAK PERNAH berfungsi** sejak 15 Mei — hanya menghasilkan error berantai

### Akar Masalah

**TIGA Python Environment berbeda menjalankan bot secara bersamaan:**

| Environment | Path Python | Path Telegram Library |
|---|---|---|
| MS Store Python 3.11 | `...\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\...` | `...\local-packages\Python311\site-packages\...` |
| Virtualenv envSCreener | `C:\Screener\envSCreener\Scripts\python.exe` | `C:\Screener\envSCreener\Lib\site-packages\...` |
| Hermes Agent venv | `...\hermes\hermes-agent\venv\...` | `...\hermes-agent\venv\Lib\site-packages\...` |

**Mekanisme Lock File GAGAL total karena:**

1. **Race condition:** Check-then-create tidak atomic — dua instance bisa lolos sebelum lock ditulis
2. **PID reuse:** Lock hanya cek `OpenProcess(PROCESS_QUERY_INFORMATION)`, jika process mati dan PID dipakai ulang, deteksi false positive
3. **Stale lock:** Lock file PID 18424 masih ada di `C:\Users\yanli\AppData\Local\Temp\screener_bot.lock` tapi process sudah MATI — tidak ada cleanup (crash tidak tertangani `atexit`)
4. **Tidak ada mutual exclusion lintas environment:** Lock file di temp directory yang SAMA — but jika TEMP env var berbeda tiap Python env, lock tidak efektif

### Flow Error (dari log):
```
Instance A (MS Store) start → create lock → polling OK
Instance B (envSCreener) start → cek lock → ??? (gagal deteksi) → polling → 409 Conflict
Instance A dapet 409 juga → keduanya flood error loop
```

---

## 🔥 BUG #2 (KRITIS — URGENT): TIGA CARA KIRIM TELEGRAM

Ada **tiga mekanisme berbeda** untuk berkomunikasi dengan Telegram API. Mereka **saling mengganggu** karena semuanya menggunakan token yang sama:

| # | File | Method | Instance |
|---|---|---|---|
| 1 | `telegram_bot.py:1475` | `Application.builder().token(TOKEN).build()` + `app.run_polling()` | **Full bot** — long polling, menerima command |
| 2 | `dashboard/alerts.py:68` | `telegram.Bot(token=TOKEN)` | Hanya **send message** — digunakan via AlertManager |
| 3 | `daily_research_reporter_v2.py:53` | `requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage")` | Hanya **send message** — HTTP langsung |

**Masalah:** Method #1 membuat koneksi long-polling yang eksklusif. Method #2 dan #3 hanya send-only, jadi tidak cause 409 secara langsung. Tapi method #1 yang multiple-instance-lah penyebab utama 409.

---

## 🔥 BUG #3 (HIGH): TIDAK ADA ERROR HANDLER UNTUK CONFLICT

Di `telegram_bot.py:1475`, setelah `Application.builder()` tidak ada:
```python
app.add_error_handler(...)  # ← TIDAK ADA!
```

Akibatnya setiap `telegram.error.Conflict` menghasilkan traceback ~30 baris ke log.

---

## 🔥 BUG #4 (HIGH): LOG FILE MEMBENGKAK 7.4MB

- 51.742 baris, 7.1MB — hanya untuk log bot yang error terus
- Masing-masing 409 error menghasilkan ~30 baris traceback = **8.790 baris traceback sampah**
- Log harian screener juga menumpuk (12 file, total ~700KB)

---

## 🔥 BUG #5 (MEDIUM): LOCK FILE MECHANISM RENTAN

Di `telegram_bot.py:1437-1465`:
```python
LOCK_FILE = os.path.join(_tmp.gettempdir(), "screener_bot.lock")
if os.path.exists(LOCK_FILE):
    ...
    handle = kernel32.OpenProcess(0x0400, False, int(old_pid))
    if handle:
        ...  # CUKUP cek handle truthy — rentan PID reuse!
```

**Masalah:**
1. `0x0400` (PROCESS_QUERY_INFORMATION) tidak cukup reliable di semua Windows
2. Tidak ada file locking (`msvcrt.locking` atau `fcntl.flock` di Linux)
3. Tidak ada pembedaan berdasarkan Python environment
4. PID reuse bisa menyebabkan false positive

---

## 🔥 BUG #6 (MEDIUM): RISIKO KEAMANAN — CREDENTIAL EXPOSURE

### Findings:

| Credential | Status | Risk |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Di .env (file terbaca), di log termasking sebagian (`8101714771:***`) | **MEDIUM** — prefix token visible |
| `DISCORD_WEBHOOK` | Di .env **FULL URL visible** | **HIGH** — siapa pun bisa post ke Discord |
| `OPENCODE_ZEN_API_KEY` | Di .env (file terbaca via terminal) | **HIGH** — API key visible |
| `.env` | Di .gitignore (PROTECTED) ✅ | Tidak tercommit |

### Rekomendasi:
- Rotate Discord webhook URL SEGERA
- Bot token prefix `8101714771` sudah exposed di log — rotate juga
- API key opencode zen sudah visible — rotate

---

## 🔧 ANALISIS WATCHDOG

Di `telegram_bot.py:1419-1426`:
```python
def _watchdog():
    while True:
        time.sleep(300)  # 5 menit
        try:
            _sync_csv_to_db()
        except Exception:
            pass  # ← SILENT EXCEPTION!
```
- Watchdog hanya sync CSV → SQLite, tidak memonitor health bot
- `except: pass` menelan semua error tanpa logging

---

## 📊 DIAGRAM ALIR DATA

```
                    ┌─────────────────────────────────────┐
                    │         run_daily_analysis.py        │
                    │         cron_market_summary.py       │
                    └──────────┬──────────────────────────┘
                               │ subprocess
                               ▼
                    ┌─────────────────────┐
                    │ daily_screening_runner│──→ candidates_{date}.json
                    └─────────────────────┘
                               │
                               ▼
                    ┌──────────────────────────────┐
                    │ daily_research_reporter_v2.py │
                    │   → requests.post (Telegram)  │ ← Method #3
                    │   → AlertManager (Discord)    │
                    └──────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                   telegram_bot.py v6.1                   │
│   → python-telegram-bot Application.run_polling()        │ ← Method #1
│   → menerima command (/cek, /swing, /scalp, dll)         │
│   → ai_agent.py untuk chat natural language              │
│   → dashboard/alerts.py untuk alert (Method #2)          │
└─────────────────────────────────────────────────────────┘
```

---

## 🎯 REKOMENDASI PRIORITAS

### 🔴 URGENT — Fix Sekarang (Hari Ini)

| # | Tindakan | Alasan |
|---|---|---|
| **U1** | **Matikan SEMUA instance bot** — kill semua process python yang jalan | Hentikan flood 409 |
| **U2** | **Hapus lock file stale**: `C:\Users\yanli\AppData\Local\Temp\screener_bot.lock` | Biarkan bot bisa start fresh |
| **U3** | **Rotate Discord webhook URL** | Safety kredensial |
| **U4** | **Rotate Telegram bot token** via @BotFather | Prefix token 8101714771 sudah exposed |

### 🟡 MINGGU INI — Perbaikan Kode

| # | Tindakan | Detail |
|---|---|---|
| **W1** | **Atomic lock mechanism** | Gunakan `msvcrt.locking()` untuk Windows file lock, tambahkan `hostname` di lock file name untuk bedakan environment |
| **W2** | **Add `app.add_error_handler()`** | Tangkap `telegram.error.Conflict` dengan graceful retry + backoff |
| **W3** | **Unified Telegram sender** | Semua kirim pesan via satu class, hapus `requests.post` langsung di `daily_research_reporter_v2.py` |
| **W4** | **Log rotation** | Implement `RotatingFileHandler` (max 5MB, backup 3), batch cleanup log lama |

### 🔵 BULAN INI — Refactor Arsitektur

| # | Tindakan | Detail |
|---|---|---|
| **M1** | **Single entry point** | Satu service/daemon untuk bot, cron cukup trigger API endpoint |
| **M2** | **Flask/FastAPI health endpoint** | Ganti watchdog dengan HTTP health check |
| **M3** | **Environment isolation** | Docker atau virtualenv dengan lock path unik per env |
| **M4** | **Centralized config** | Pindah dari campuran config.ini + .env ke satu file YAML |
| **M5** | **Async refactor** | Banyak blocking IO di thread pool — bisa pakai asyncio murni |

---

## 📋 CEKLIST INVESTIGASI

- [x] Cek process running → ✅ Tidak ada python.exe dengan command screener/bot running saat ini
- [x] Cek lock file → ✅ ADA: `C:\Users\yanli\AppData\Local\Temp\screener_bot.lock` (PID 18424, process sudah mati)
- [x] Cek dua environment Python → ✅ TERBUKTI: MS Store + envSCreener (+ hermes-agent venv)
- [x] Cek scheduled task / startup → ✅ Tidak ada (Windows Task Scheduler & Startup kosong)
- [x] Cek credential leak di log → ✅ Discord webhook di .env full visible, bot token prefix visible
- [x] Cek .gitignore → ✅ .env sudah dilindungi
- [x] Analisis fragmentasi kode → ✅ 50+ file, 3 cara kirim Telegram, watchdog tidak berguna
- [x] Analisis lock file → ✅ Race condition, PID reuse risk, stale lock
- [x] Rekomendasi prioritas → ✅ Lihat tabel di atas

---

## 📈 STATISTIK LOG

| Metric | Value |
|---|---|
| Total lines | 51,742 |
| File size | 7.1 MB |
| 409 Conflict count | 293 |
| "No error handlers" count | 320 |
| Date range | 15 Mei — 27 Juni 2026 |
| Distinct Python paths | 3 (MS Store, envSCreener, hermes-agent) |
| Daily log files (screener) | 12 files, ~700KB total |

---

## KESIMPULAN

**Bot Telegram Screener IHSG TIDAK PERNAH berfungsi dengan benar sejak 15 Mei 2026.** Penyebab utamanya adalah dual-instance conflict yang disebabkan oleh:

1. Dua (atau lebih) Python environment menjalankan `telegram_bot.py` secara bersamaan
2. Lock file mechanism tidak efektif karena race condition dan tidak atomic
3. Tidak ada error handler untuk `telegram.error.Conflict`
4. Tidak ada logging rotasi, menyebabkan log membengkak sampai 7.4MB

**Prioritas pertama:** Hentikan semua instance bot, hapus lock file stale, rotate credential yang exposed. **Prioritas kedua:** Implementasi atomic lock + error handler + unified Telegram sender. **Prioritas ketiga:** Refactor arsitektur ke single entry point yang lebih maintainable.
