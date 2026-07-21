# RESOLUSI SEGERA — Telegram Screener IHSG Bot
## Langkah-langkah untuk menghentikan 409 Conflict SEKARANG

### Step 1: Kill Semua Instance Bot
Jika ada python.exe yang menjalankan telegram_bot.py, kill dengan:
```
taskkill /F /IM python.exe
```
**CATATAN:** Ini akan kill SEMUA python — pastikan tidak ada proses python lain yang penting.

### Step 2: Hapus Lock File Stale
```
rm -f /c/Users/yanli/AppData/Local/Temp/screener_bot.lock
```

### Step 3: Verifikasi Tidak Ada Lagi Process
```
tasklist /FI "IMAGENAME eq python.exe" 
```
Harus return: "No tasks are running"

### Step 4: Rotate Credential (OPSIONAL TAPI DIREKOMENDASIKAN)
- **Discord webhook:** Generate ulang di Discord Server Settings → Integrations → Webhooks
- **Telegram bot token:** `/revoke` di @BotFather, lalu `/token` untuk generate baru
- Update `.env` dengan token baru

### Step 5: Start Ulang Bot dengan Single Environment
Hanya jalankan SATU environment, misalnya dari hermes workspace:
```bash
cd C:/Hermes_Workspace/Screener
python telegram_bot.py
```

### Step 6: Verifikasi Tidak Ada 409 Lagi
Cek 5 menit pertama di log:
```bash
tail -f logs/telegram_bot.log | grep -i conflict
```
Seharusnya tidak ada lagi `409 Conflict`.
