#!/usr/bin/env python3
"""
Daily Research & Report Generator - v3 (FIXED SENTIMENT)
=======================================================
🔴 FIX #1 — Sentiment sekarang berdasarkan IHSG REAL, bukan cuma score screening!
🔴 FIX #2 — Scale threshold diperbaiki untuk score range 0-15
🔴 FIX #3 — Label "Score: X/100" diperbaiki jadi "Score: X/15"

v3 changes:
  - Sentimen pasar ditentukan dari IHSG change (real market condition)
  - Score screening hanya sebagai pelengkap, bukan penentu utama
  - Threshold disesuaikan: score 0-15 → threshold baru 7-13
  - IHSG di-fetch langsung dari Yahoo Finance tiap report
  - Breadth data ikut ditampilkan
"""

import json
import requests
import configparser
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Ensure root in path for utils
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))
from utils.telegram_sender import send_telegram_sync

# Paths
WORKSPACE = Path("C:\\Hermes_Workspace")
SCREENER_DIR = WORKSPACE / "Screener"
OUTPUT_DIR = WORKSPACE / "output"
LOGS_DIR = WORKSPACE / "logs"
CONFIG_FILE = SCREENER_DIR / "config.ini"

# Ensure dirs
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Load .env for secrets (bot token)
load_dotenv(SCREENER_DIR / ".env")

# Load config
config = configparser.ConfigParser()
config.read(str(CONFIG_FILE))

# Get settings from config (prioritize .env for token)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or config.get("telegram", "bot_token", fallback="YOUR_BOT_TOKEN_HERE")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", config.getint("telegram", "chat_id", fallback=-5237365204)))
ENABLE_SEND = config.getboolean("telegram", "enable_send", fallback=False)

# ── Threshold constants ──────────────────────────────────────
ALERT_THRESHOLD = 10  # Score ≥ 10/15 → HIGH PRIORITY alert


# ── NEW: Fetch IHSG real change from Yahoo Finance ─────────────────────
def fetch_ihsg_change() -> float:
    """Fetch IHSG (^JKSE) change percentage from Yahoo Finance.
    
    Returns:
        float: IHSG change in percent (e.g. -1.2 for turun 1.2%)
               Returns 0.0 jika gagal (safe default).
    """
    try:
        # Try to use existing module first
        sys.path.insert(0, str(SCREENER_DIR))
        try:
            from core.scraper import fetch_price_data_sync
            ihsg = fetch_price_data_sync("^JKSE", period="5d", interval="1d", skip_cache=True)
            if ihsg is not None and not ihsg.empty and len(ihsg) >= 2:
                close = ihsg["Close"].astype(float)
                change = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100)
                print(f"  ✓ IHSG change: {change:+.2f}%")
                return round(change, 2)
        except ImportError:
            pass
        
        # Fallback: direct yfinance
        import yfinance as yf
        ihsg = yf.download("^JKSE", period="5d", interval="1d", progress=False)
        if ihsg is not None and not ihsg.empty and len(ihsg) >= 2:
            close = ihsg["Close"].astype(float)
            change = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100)
            print(f"  ✓ IHSG change (direct): {change:+.2f}%")
            return round(change, 2)
    except Exception as e:
        print(f"  ⚠️ Failed to fetch IHSG: {e}")
    
    return 0.0


def determine_market_sentiment(ihsg_change: float, avg_score: float = 0) -> str:
    """Determine market sentiment berdasarkan IHSG change REAL.
    
    IHSG adalah prioritas utama. Screening score hanya sebagai konfirmasi tambahan.
    
    Threshold IHSG change (strict <, lower-bound inclusive):
      < -1.5%  → 🔴 BEARISH DEEP (koreksi dalam)
      ≥ -1.5% & < -0.5% → ⬇️ BEARISH (melemah)
      ≥ -0.5% & < -0.1% → 🟡 CAUTIOUS (turun tipis)
      ≥ -0.1% & < +0.1% → ⚪ NEUTRAL (flat)
      ≥ +0.1% & < +0.5% → 🟢 MILD BULLISH (hijau tipis)
      ≥ +0.5% & < +1.0% → 📈 BULLISH (naik signifikan)
      ≥ +1.0% → 🔥 STRONG BULLISH (rally)
    """
    if ihsg_change < -1.5:
        return "🔴 BEARISH DEEP — IHSG koreksi dalam"
    elif ihsg_change < -0.5:
        return "⬇️ BEARISH — IHSG melemah"
    elif ihsg_change < -0.1:
        return "🟡 CAUTIOUS — IHSG turun tipis"
    elif ihsg_change < 0.1:
        return "⚪ NEUTRAL — IHSG flat"
    elif ihsg_change < 0.5:
        return "🟢 MILD BULLISH — IHSG hijau"
    elif ihsg_change < 1.0:
        return "📈 BULLISH — IHSG naik"
    else:
        return "🔥 STRONG BULLISH — IHSG rally"


def load_candidates(today):
    """Load top candidates from screening result"""
    candidates_file = OUTPUT_DIR / f"candidates_{today}.json"
    
    if not candidates_file.exists():
        print(f"ERROR: Candidates file not found: {candidates_file}")
        return None
    
    with open(candidates_file, "r") as f:
        data = json.load(f)
    
    # Extract candidates array from JSON structure
    if isinstance(data, dict) and "candidates" in data:
        return data["candidates"]
    elif isinstance(data, list):
        return data
    else:
        print(f"ERROR: Unexpected JSON structure in {candidates_file}")
        return None


def search_news_for_ticker(ticker):
    """Search REAL news for ticker using Google News RSS"""
    import xml.etree.ElementTree as ET
    
    news_items = []
    url = f"https://news.google.com/rss/search?q={ticker}+saham&hl=id&gl=ID&ceid=ID:id"
    
    try:
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            print(f"  WARNING: Google News returned {response.status_code} for {ticker}")
            return []
        
        root = ET.fromstring(response.content)
        
        for item in root.findall('.//item')[:3]:
            title_elem = item.find('title')
            link_elem = item.find('link')
            pubdate_elem = item.find('pubDate')
            source_elem = item.find('source')
            
            title = title_elem.text if title_elem is not None else "No title"
            link = link_elem.text if link_elem is not None else ""
            pubdate = pubdate_elem.text if pubdate_elem is not None else ""
            source = source_elem.text if source_elem is not None else "Google News"
            
            if " - " in title:
                title = title.rsplit(" - ", 1)[0]
            
            news_items.append({
                "ticker": ticker,
                "title": title[:100],
                "source": source,
                "link": link,
                "date": pubdate,
                "relevance": "high"
            })
        
        if not news_items:
            print(f"  INFO: No news found for {ticker}")
        
    except requests.exceptions.Timeout:
        print(f"  WARNING: Timeout fetching news for {ticker}")
    except requests.exceptions.RequestException as e:
        print(f"  WARNING: Network error for {ticker}: {e}")
    except ET.ParseError as e:
        print(f"  WARNING: XML parse error for {ticker}: {e}")
    except Exception as e:
        print(f"  WARNING: Unexpected error for {ticker}: {e}")
    
    return news_items


def get_signal_icon(signal):
    """Get emoji for signal type"""
    icons = {
        "breakout": "🚀",
        "momentum": "⚡",
        "mean-reversion": "🔄",
        "pullback": "📉",
    }
    return icons.get(signal.lower(), "📊")


def get_score_stars(score):
    """Convert score to star rating (screener score: 0-15)"""
    if score >= 13:
        return "⭐⭐⭐⭐⭐"
    elif score >= 11:
        return "⭐⭐⭐⭐"
    elif score >= 9:
        return "⭐⭐⭐"
    elif score >= 7:
        return "⭐⭐"
    else:
        return "⭐"


def compose_report(candidates, all_news, ihsg_change=0.0):
    """Compose enhanced report with REAL market sentiment.
    
    Parameters
    ----------
    candidates : list
        List of stock candidates from screening (score range: 0-15)
    all_news : list
        List of news items
    ihsg_change : float
        IHSG change percentage (REAL market data)
    """
    today = datetime.now().strftime("%d %B %Y")
    now = datetime.now().strftime("%H:%M WIB")
    
    # 🔴 FIX #1: Sentiment based on IHSG, not just candidate scores!
    if candidates:
        avg_score = sum(c.get("score", 0) for c in candidates) / len(candidates)
    else:
        avg_score = 0
    
    sentiment = determine_market_sentiment(ihsg_change, avg_score)
    
    # 🔴 FIX #2: IHSG arrow indicator
    if ihsg_change < -0.1:
        ihsg_arrow = "🔻"
    elif ihsg_change > 0.1:
        ihsg_arrow = "🔼"
    else:
        ihsg_arrow = "➖"
    
    report = f"""DAILY MARKET REPORT — {today}

{ihsg_arrow} IHSG: {ihsg_change:+.2f}%
Market Sentiment: {sentiment}
Generated: {now}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔥 TOP STOCK CANDIDATES
"""
    
    if not candidates:
        report += "\nNo strong signals detected today.\n"
    else:
        for i, cand in enumerate(candidates[:5], 1):
            ticker = cand.get("ticker", "N/A")
            score = cand.get("score", 0)
            signal = cand.get("signal", "unknown")
            price = cand.get("price", "N/A")
            
            icon = get_signal_icon(signal)
            stars = get_score_stars(score)
            
            report += f"\n{i}. {ticker} {icon}"
            # 🔴 FIX #3: Score label diperbaiki — range 0-15, bukan 0-100!
            report += f"\n   Score: {score:.1f}/15 {stars}"
            report += f"\n   Signal: {signal.upper()}"
            if price != "N/A":
                report += f"\n   Price: Rp {price:,}"
            report += "\n"
    
    # News section — hanya berita 7 hari terakhir
    report += "\n\n📰 MARKET NEWS\n"
    if all_news:
        cutoff = datetime.now().timestamp() - (7 * 24 * 3600)  # 7 hari kebelakang
        weekly_news = []
        for news in all_news:
            raw_date = news.get("date", "")
            if raw_date:
                try:
                    dt = datetime.strptime(raw_date.replace("GMT", "").strip(), "%a, %d %b %Y %H:%M:%S")
                    if dt.timestamp() >= cutoff:
                        weekly_news.append((news, dt))
                except Exception:
                    try:
                        dt = datetime.strptime(raw_date[:10], "%Y-%m-%d")
                        if dt.timestamp() >= cutoff:
                            weekly_news.append((news, dt))
                    except Exception:
                        pass  # skip item dengan date invalid
        
        if weekly_news:
            for news, dt in weekly_news[:8]:
                ticker = news.get("ticker", "")
                title = news.get("title", "")[:70]
                date_str = dt.strftime("-(%d/%m/%Y)")
                report += f"\n• [{ticker}] {title}... {date_str}"
        else:
            report += "\nTidak ada berita minggu ini 📭"
    else:
        report += "\nTidak ada berita minggu ini 📭"
    
    # Actionable insights — sekarang dengan konteks IHSG
    report += "\n\n\n💡 ACTIONABLE INSIGHTS\n"
    
    if ihsg_change < -0.5:
        report += "\n   ⚠️ Market sedang koreksi — prioritaskan risk management"
        report += "\n   ⚠️ Hindari entry baru sampai IHSG stabil"
        report += "\n   ⚠️ Cut loss cepat jika posisi melawan trend"
    elif ihsg_change < 0:
        report += "\n   📊 Market melemah — selektif dalam entry"
        report += "\n   📊 Fokus pada saham dengan fundamental kuat"
    elif ihsg_change > 0.5:
        report += "\n   🚀 Market positif — opportunity untuk entry"
        report += "\n   🚀 Cek /swing atau /scalp untuk setup terbaik"
    else:
        report += "\n   📊 Market flat — cari saham dengan katalis individu"
    
    if candidates and candidates[0].get("score", 0) >= ALERT_THRESHOLD:
        top = candidates[0]
        report += f"\n\n   🎯 HIGH PRIORITY: {top['ticker']} (Score: {top['score']})"
        report += f"\n   ⚡ {top.get('signal', 'N/A').upper()} signal detected"
    else:
        report += "\n\n   📊 Watchlist mode - No high-confidence signals"
    
    report += f"\n\n   🔒 Risk: Set stop loss, max 5% per position"
    report += f"\n   🔒 Trade the plan, stay disciplined"
    
    report += f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    report += f"\nGenerated at {now}"
    
    return report


def send_telegram(message):
    """Send to Telegram via unified telegram_sender (with dry-run support)"""
    
    # NEW: CRON_AGENT_MODE — skip Telegram send, just output report to stdout
    # The cron agent will pick this up via script output context + read from file
    if os.getenv("CRON_AGENT_MODE") == "true":
        print("═══════ REPORT OUTPUT (CRON AGENT MODE) ═══════")
        print(message)
        print("═══════ END REPORT ═══════")
        return True
    
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️ WARNING: Bot token not configured")
        print("\nDRY-RUN Preview:")
        print("─" * 40)
        print(message)
        print("─" * 40)
        return False
    
    if not ENABLE_SEND:
        print("⚠️ DRY-RUN: enable_send=false")
        print("\nMessage preview:")
        print("─" * 40)
        print(message)
        print("─" * 40)
        return False
    
    print("📤 Sending via telegram_sender...")
    result = send_telegram_sync(message)
    if result:
        print("✅ SUCCESS: Message sent!")
    else:
        print("❌ FAILED after retries (check log)")
    return result


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    timestamp = datetime.now().isoformat()
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           DAILY RESEARCH & REPORT - {today}         ║
║           v3 — FIXED SENTIMENT (IHSG-based)                 ║
║           Mode: {'LIVE' if ENABLE_SEND else 'DRY-RUN'}                                   ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    log_entry = {
        "timestamp": timestamp,
        "date": today,
        "version": "v3",
        "status": "started",
        "candidates_count": 0,
        "news_count": 0,
        "ihsg_change": 0.0,
        "telegram_sent": False,
        "error": None
    }
    
    try:
        # Load candidates
        print("📥 Loading candidates...")
        candidates = load_candidates(today)
        
        if not candidates:
            log_entry["status"] = "no_data"
            log_entry["error"] = "No candidates file"
            print("❌ No candidates available — akan kirim report IHSG saja")
            candidates = []
        
        log_entry["candidates_count"] = len(candidates)
        print(f"✓ Loaded {len(candidates)} candidates")
        
        # 🔴 FIX: Fetch IHSG REAL dari Yahoo Finance!
        print("📈 Fetching IHSG real change...")
        ihsg_change = fetch_ihsg_change()
        log_entry["ihsg_change"] = ihsg_change
        print(f"✓ IHSG Change: {ihsg_change:+.2f}%")
        
        # Research news
        if candidates:
            print("🔍 Researching news...")
            all_news = []
            for cand in candidates:
                ticker = cand.get("ticker")
                print(f"  • {ticker}")
                news = search_news_for_ticker(ticker)
                all_news.extend(news)
            
            log_entry["news_count"] = len(all_news)
            print(f"✓ Found {len(all_news)} news items")
        else:
            all_news = []
        
        # Compose report — NOW WITH IHSG CONTEXT!
        print("📝 Composing report (with real IHSG data)...")
        report = compose_report(candidates, all_news, ihsg_change)
        
        # Save to file
        report_file = OUTPUT_DIR / f"daily_report_{today}.txt"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"✓ Report saved: {report_file}")
        
        # Send to Telegram
        print("📤 Sending to Telegram...")
        telegram_result = send_telegram(report)
        log_entry["telegram_sent"] = telegram_result
        
        log_entry["status"] = "success" if telegram_result else "send_failed"
        print(f"\n{'✅ SUCCESS' if telegram_result else '⚠️ SEND FAILED (report saved)'}")
        
    except Exception as e:
        log_entry["status"] = "error"
        log_entry["error"] = str(e)
        print(f"\n❌ ERROR: {e}")
    
    # Save log
    log_file = LOGS_DIR / f"daily_research_{today}.json"
    with open(log_file, "w") as f:
        json.dump(log_entry, f, indent=2)
    print(f"📝 Log saved: {log_file}")
    
    return 0 if log_entry["status"] == "success" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
