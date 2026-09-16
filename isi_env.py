#!/usr/bin/env python3
"""isi_env.py — Isi .env Screener dengan aman (input tersembunyi, tidak lewat chat).

Cara pakai (di terminal laptop):
    cd ~/screener && .venv/bin/python isi_env.py

- Input TIDAK tampil di layar dan TIDAK pernah lewat chat.
- Nilai yang sudah ada ditawarkan untuk dipertahankan (Enter = pakai yang lama).
- File ditulis mode 600 (hanya pemilik yang bisa baca).
"""
import getpass
import os
import stat

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def baca_lama():
    data = {}
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as f:
            for baris in f:
                baris = baris.strip()
                if baris and "=" in baris and not baris.startswith("#"):
                    k, v = baris.split("=", 1)
                    data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def tanya(nama, lama, wajib):
    label = "(wajib)" if wajib else "(opsional — Enter utk lewati)"
    if lama:
        label += f" [sudah ada: {len(lama)} karakter, Enter = pakai yang lama]"
    nilai = getpass.getpass(f"{nama} {label}: ").strip()
    return nilai or lama


def main():
    lama = baca_lama()
    print("=== Isi .env Screener (ketikan tidak akan tampil di layar) ===")
    print("Sumber data default = RisetSaham lokal (tanpa API key). Isi INVEZGO")
    print("hanya kalau memang mau pakai Invezgo (SCREENER_DATA=invezgo).")
    inv = tanya("INVEZGO_API_KEY", lama.get("INVEZGO_API_KEY", ""), False)
    tg = tanya("TELEGRAM_BOT_TOKEN", lama.get("TELEGRAM_BOT_TOKEN", ""), False)
    cid = tanya("TELEGRAM_CHAT_ID", lama.get("TELEGRAM_CHAT_ID", ""), False)
    ds = tanya("DEEPSEEK_API_KEY", lama.get("DEEPSEEK_API_KEY", ""), False)

    if not inv and not tg and not ds:
        print("PERINGATAN: semua kosong. Tanpa Telegram hasil tetap masuk")
        print("RisetSaham (kanal utama), jadi scan TETAP bisa jalan. Lanjut simpan?")

    baris = []
    if inv:
        baris.append(f"INVEZGO_API_KEY={inv}")
    if tg:
        baris.append(f"TELEGRAM_BOT_TOKEN={tg}")
    if cid:
        baris.append(f"TELEGRAM_CHAT_ID={cid}")
    if ds:
        baris.append(f"DEEPSEEK_API_KEY={ds}")
    if not baris:
        print("Tidak ada yang diisi — .env tidak ditulis (scan pakai data lokal saja).")
        return 0

    with open(PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(baris) + "\n")
    os.chmod(PATH, stat.S_IRUSR | stat.S_IWUSR)  # 600

    print(f"OK — tersimpan ke {PATH} (mode 600).")
    print(f"   INVEZGO_API_KEY     : {str(len(inv)) + ' karakter (awalan ' + inv[:4] + '...)' if inv else '(kosong — pakai data lokal RisetSaham)'}")
    print(f"   TELEGRAM_BOT_TOKEN  : {'ada, ' + str(len(tg)) + ' karakter' if tg else 'kosong'}")
    print(f"   TELEGRAM_CHAT_ID    : {'ada' if cid else 'kosong'}")
    print(f"   DEEPSEEK_API_KEY    : {'ada' if ds else 'kosong'}")
    print("Mau ubah nanti? Jalankan lagi skrip ini — nilai lama tetap ditawarkan.")


if __name__ == "__main__":
    main()
