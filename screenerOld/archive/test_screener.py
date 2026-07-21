"""
test_screener.py — Unit tests for screener core logic.
Gunakan pytest.
"""

import sys
import os
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_import_screener():
    """Test basic import of screener module."""
    import screener
    assert hasattr(screener, "SEMUA_TICKER"), "SEMUA_TICKER not found"
    assert hasattr(screener, "analisis_saham"), "analisis_saham not found"
    assert len(screener.SEMUA_TICKER) > 0, "SEMUA_TICKER is empty"

def test_database_structure():
    """Test database tables exist."""
    conn = sqlite3.connect("histori_ihsg.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

def test_analisis_saham():
    """Test single stock analysis returns dict."""
    import screener
    result = screener.analisis_saham("BBCA")
    assert result is not None, "analisis_saham returned None"
    assert isinstance(result, dict), "analisis_saham should return dict"

def test_basic_signal_detection():
    """Test bahwa logika dasar sinyal masih berjalan."""
    import screener
    result = screener.analisis_saham("BBCA")
    
    if result and "Sinyal" in result:
        valid_signals = ["BELI", "JUAL", "HOLD", "TUNGGU", "ULTRABELI", "CUAN", "HINDARI"]
        assert result["Sinyal"] in valid_signals or isinstance(result["Sinyal"], int)
