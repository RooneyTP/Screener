#!/usr/bin/env python3
"""Cron wrapper — V7 Dual Mode Scanner (21:00 WIB) dengan JAMINAN KIRIM TELEGRAM.

Mekanisme jaminan:
1. Jalankan v7_scan.py (capture stdout+stderr, timeout 600s).
2. Kirim output ke Telegram dengan RETRY 3x + verifikasi respons {"ok":true}.
   Sukses = Telegram membalas ok:true (message_id tercatat). Bukan cuma "request selesai".
3. Kalau v7_scan exit != 0 -> kirim pesan error ke grup, lalu exit(returncode).
4. Kalau output mengandung sinyal (Swing N>0 / Intra N>0) dan kirim gagal 3x
   -> tulis data/send_failed.log (bukti) + exit 1 (Hermes cron menandai error).
5. Setiap kirim sukses -> print [SEND] ok message_id=... ke stdout (bukti di cron output).
"""
import sys, os, json, time, subprocess, urllib.request, urllib.parse

ENV_PATH = r"C:\Hermes_Workspace\Screener\.env"
SCAN_DIR = r"C:\Hermes_Workspace\Screener\idx_alpha_screener"
FAIL_LOG = r"C:\Hermes_Workspace\Screener\idx_alpha_screener\data\send_failed.log"


def load_env():
    env = {}
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def send_tg(text, token, chat_id, retries=3):
    """Kirim pesan, retry, VERIFIKASI ok:true. Return (ok, message_id, last_error)."""
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    last_err = "no attempt"
    for attempt in range(1, retries + 1):
        try:
            resp = urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30)
            body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            if parsed.get("ok"):
                mid = parsed.get("result", {}).get("message_id", "?")
                return True, mid, ""
            last_err = f"Telegram ok:false: {body[:120]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(2 * attempt)
    return False, None, last_err


def count_signals(output):
    """Deteksi jumlah sinyal dari ringkasan 'Swing N · Intra M'."""
    import re
    m = re.search(r"Swing (\d+) · Intra (\d+)", output)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def main():
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID", "-5237365204")

    print("V7 DUAL MODE SCAN")
    sys.stdout.flush()

    if not token or not chat:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tidak ditemukan di .env")
        sys.exit(1)

    try:
        result = subprocess.run(
            [sys.executable, "v7_scan.py"],
            cwd=SCAN_DIR, capture_output=True, text=True, timeout=600,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    except Exception as e:
        msg = f"SCAN GAGAL: subprocess error: {e}"
        ok, mid, err = send_tg(msg, token, chat)
        print(msg)
        print(f"[SEND] {'ok' if ok else 'FAIL'} message_id={mid} err={err}")
        sys.exit(1)

    output = result.stdout
    if result.stderr:
        tail = result.stderr.strip()[-800:]
        if tail:
            output += f"\n[stderr] {tail}"

    swing_n, intra_n = count_signals(output)
    has_signals = (swing_n or 0) + (intra_n or 0) > 0

    if result.returncode != 0:
        # Scan gagal: kirim pesan error JELAS + exit non-zero (scheduler tidak buta)
        msg = f"SCAN GAGAL exit={result.returncode}\n{output[-1500:]}"
        ok, mid, err = send_tg(msg, token, chat)
        print(msg)
        print(f"[SEND] {'ok' if ok else 'FAIL'} message_id={mid} err={err}")
        print(f"Exit code scan: {result.returncode}")
        sys.exit(result.returncode)

    # Scan sukses: kirim pesan. Kalau ADA sinyal -> retry lebih agresif (5x).
    retries = 5 if has_signals else 3
    ok, mid, err = send_tg(output, token, chat, retries=retries)

    if ok:
        print(output)
        print(f"[SEND] ok message_id={mid} chars={len(output)} signals=({swing_n},{intra_n})")
        sys.exit(0)
    else:
        # Kirim gagal: catat bukti + exit 1 (tidak pernah senyap)
        fail_note = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | signals=({swing_n},{intra_n}) | err={err}\n"
        try:
            with open(FAIL_LOG, "a", encoding="utf-8") as f:
                f.write(fail_note)
        except Exception:
            pass
        print(output)
        print(f"[SEND] FAIL setelah {retries} percobaan: {err}")
        print(f"FAIL LOG: {FAIL_LOG}")
        sys.exit(1)


if __name__ == "__main__":
    main()
