@echo off
REM ============================================================
REM  V7 Screener Starter — jalankan semua service screener
REM  - Cron watchdog: scan + kirim Telegram setiap 21:00 WIB
REM  - Positions bot: terima command /posisi di grup Telegram
REM  Cara pakai: double-click file ini, biarkan jendela terbuka.
REM ============================================================
set PY=C:\Users\yanli\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
set SCR=C:\Hermes_Workspace\Screener
set IDX=%SCR%\idx_alpha_screener

echo [%date% %time%] Starting V7 Positions Bot...
start "V7PositionsBot" "%PY%" "%IDX%\telegram_positions_bot.py"

echo [%date% %time%] Starting V7 Cron Watchdog (21:00 WIB daily)...
:loop
"%PY%" "%SCR%\cron_v3_scan.py"
echo [%date% %time%] Scan selesai. Menunggu 24 jam...
timeout /t 86400
goto loop
