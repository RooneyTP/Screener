#!/usr/bin/env python3
"""Cron Wrapper — V7 Dual Mode Scanner (21:00 WIB)
Menjalankan v7_scan.py dan mengirim hasil ke Telegram group via API."""
import sys, os, json, subprocess, urllib.request, urllib.parse

# ── Kredensial Telegram dari .env (root repo) — JANGAN hardcode token di source ──
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

SCREENER_DIR = r"C:\Hermes_Workspace\Screener\idx_alpha_screener"
VENV_PYTHON = r"C:\Users\yanli\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5237365204")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def send_telegram(text):
    """Kirim pesan ke grup Telegram via Bot API."""
    try:
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=data, method="POST"
        )
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read()).get("ok", False)
    except Exception:
        return False


def main():
    """Jalankan scan V7 via subprocess + kirim hasil ke Telegram.

    B1: SEMUA eksekusi ada di sini (bukan module level) — import
    cron_v3_scan TIDAK boleh menjalankan scan atau mengirim Telegram.
    """
    # L8: cek token di dalam main() — import tidak boleh exit walau .env tidak ada.
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN tidak ditemukan di .env — hentikan.", file=sys.stderr)
        sys.exit(1)

    print("=" * 50)
    print("  V7 DUAL MODE SCAN")
    print(f"  [cron_v3_scan.py] ...")
    sys.stdout.flush()

    try:
        # PYTHONUTF8=1: pastikan subprocess membaca file & mendecode stdout/stderr sebagai UTF-8.
        # Tanpa ini, byte non-ASCII (mis. 0x90) di data CSV memicu 'charmap' UnicodeDecodeError
        # pada locale Windows (cp1252) — penyebab cron 21:00 WIB gagal 03-05/08/2026.
        _env = dict(os.environ)
        _env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            [PYTHON, "v7_scan.py"],
            cwd=SCREENER_DIR, capture_output=True, text=True, timeout=600,
            env=_env, encoding="utf-8", errors="replace"
        )

        if proc.stdout:
            output = proc.stdout.strip()
            print(output)

            # Send clean output (first 4000 chars) to Telegram
            if output:
                msg = output[:4000]
                ok = send_telegram(msg)
                if ok:
                    print(f"\n  ✅ Terkirim ke Telegram group ({len(msg)} chars)")
                else:
                    print(f"\n  ⚠️ Gagal kirim ke Telegram")

        if proc.stderr:
            err_lines = [l for l in proc.stderr.split("\n") if "ERROR" in l or "Traceback" in l]
            if err_lines:
                print("\n".join(err_lines[:3]))

        # L8: exit code subprocess DITERUSKAN — scheduler tidak boleh buta
        # (dulu selalu exit 0 walau v7_scan gagal). Pesan error tetap dikirim
        # ke Telegram sebelum exit.
        if proc.returncode != 0:
            print(f"\n{'='*50}\n  ❌ Scan gagal (exit {proc.returncode})\n{'='*50}")
            tail = [l for l in (proc.stderr or "").strip().splitlines() if l.strip()][-3:]
            err_msg = f"❌ Scan gagal exit {proc.returncode}"
            if tail:
                err_msg += "\n```\n" + "\n".join(tail)[:500] + "\n```"
            send_telegram(err_msg[:4000])
            sys.exit(proc.returncode)

        status = "✅ Selesai"
        print(f"\n{'='*50}\n  {status} (exit {proc.returncode})\n{'='*50}")

    except subprocess.TimeoutExpired as e:
        print(f"\n❌ Scan timeout 600s: {e}", file=sys.stderr)
        send_telegram("❌ Scan V7 timeout 600s — cron hentikan")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        send_telegram(f"❌ Scan V7 error: {str(e)[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
