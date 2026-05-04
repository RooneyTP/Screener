# test_screener.py
import pytest
# Import fungsi matematika krusial dari file screener utamamu
from screener import hitung_kelly_sizing, position_size_calc

def test_hitung_kelly_sizing():
    # Skenario 1: Win Rate tinggi (70%), harga saham Rp 1.000, Modal 10 Juta
    # p=0.7, q=0.3, b=2.0 -> Kelly=0.55 -> Safe=0.275. Batas max = 25% modal (Rp 2.5jt)
    # Rp 2.500.000 / (1000 * 100) = 25 Lot
    hasil_bagus = hitung_kelly_sizing(ai_win_prob_percent=70.0, harga_saham=1000.0)
    assert "25.0% Modal" in hasil_bagus
    assert "25 Lot" in hasil_bagus

    # Skenario 2: Win Rate buruk/Ragu-ragu (30%)
    # p=0.3, q=0.7, b=2.0 -> Kelly= -0.05 (Negatif = Terlalu bahaya, jangan beli)
    hasil_buruk = hitung_kelly_sizing(ai_win_prob_percent=30.0, harga_saham=1000.0)
    assert "0 Lot" in hasil_buruk

def test_position_size_calc():
    # Skenario 1: Modal 10 Juta, Risiko 1%, Beli di 1000, SL di 900 (Risiko 100/lembar)
    # Total risiko = Rp 100.000. Berarti boleh beli 1000 lembar (10 Lot).
    hasil = position_size_calc(account_equity=10_000_000, risk_pct=1.0, entry=1000, stop_loss=900)
    assert hasil["shares"] == 1000
    assert hasil["risk_amount"] == 100_000
    
    # Skenario 2: Cacat data (SL lebih besar dari Entry)
    hasil_error = position_size_calc(10_000_000, 1.0, entry=1000, stop_loss=1100)
    assert hasil_error["shares"] == 0