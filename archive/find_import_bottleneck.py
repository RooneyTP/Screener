#!/usr/bin/env python
"""Profile module imports to find bottlenecks."""

import time
import sys

tests = [
    ("pandas", "import pandas"),
    ("numpy", "import numpy"),
    ("yfinance", "import yfinance"),
    ("torch", "import torch"),
    ("ta", "import ta"),
    ("nltk", "import nltk"),
    ("indicators", "from indicators import *"),
    ("data_fetcher", "from data_fetcher import *"),
    ("security", "from security import *"),
    ("performance", "from performance import *"),
    ("nlp_scraper", "from nlp_scraper import get_sentiment"),
    ("ai_model", "from ai_model import get_ai_model"),
]

print("=" * 70)
print("Profiling individual module imports...")
print("=" * 70)

total_time = 0
for name, import_stmt in tests:
    start = time.time()
    try:
        exec(import_stmt)
        elapsed = time.time() - start
        total_time += elapsed
        status = "✅" if elapsed < 1.0 else "⚠️ " if elapsed < 5.0 else "❌"
        print(f"{status} {name:<20} {elapsed:>7.2f}s")
    except Exception as e:
        elapsed = time.time() - start
        print(f"❌ {name:<20} ERROR after {elapsed:.2f}s: {e}")

print("=" * 70)
print(f"Total individual imports: {total_time:.2f}s")
print("=" * 70)

start = time.time()
try:
    import screener
    total = time.time() - start
    print(f"\nFull screener import: {total:.2f}s")
    print(f"Overhead: {total - total_time:.2f}s")
except Exception as e:
    print(f"ERROR: {e}")
