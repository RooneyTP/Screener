"""data_provider.py — Pemilih sumber data screener.

Default: ``risetsaham`` — data lokal server (Yahoo + Stockbit via modul
~/risetsaham), TANPA API key Invezgo.

Set env ``SCREENER_DATA=invezgo`` untuk kembali ke SDK Invezgo (butuh
INVEZGO_API_KEY).

Semua kode screener sebaiknya mengimpor dari sini:

    from data_provider import InvezgoProvider
"""
import os

if os.environ.get("SCREENER_DATA", "risetsaham").strip().lower() == "invezgo":
    from data_invezgo import InvezgoProvider  # noqa: F401
else:
    from data_risetsaham import InvezgoProvider  # noqa: F401
