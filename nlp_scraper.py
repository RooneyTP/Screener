import yfinance as yf
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
import warnings
import requests
from xml.etree import ElementTree as ET
import time
import logging

# Suppress yfinance logging
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

warnings.filterwarnings("ignore")

# Mengunduh kamus emosi (lexicon) secara otomatis & diam-diam jika belum ada
try:
    nltk.data.find('sentiment/vader_lexicon.zip')
except LookupError:
    nltk.download('vader_lexicon', quiet=True)

# Mengaktifkan Mesin NLP VADER
sia = SentimentIntensityAnalyzer()

# Menambahkan kosakata khusus pasar modal saham Indonesia/Global agar NLP lebih cerdas
kosakata_saham = {
    "cuan": 2.0, "profit": 1.5, "akumulasi": 1.5, "bullish": 2.0, "terbang": 1.5, "meroket": 2.0,
    "rugi": -2.0, "loss": -1.5, "distribusi": -1.5, "bearish": -2.0, "nyangkut": -2.0, "anjlok": -2.0,
    "suspend": -3.0, "arb": -2.0, "arahkan": 1.0, "dividen": 1.5,
    "laba": 1.5, "meningkat": 1.0, "tumbuh": 1.0, "lonjak": 1.5, "akuisisi": 1.5, "rekor": 2.0,
    "turun": -1.0, "merosot": -1.5, "gagal": -2.0, "hutang": -1.5, "denda": -2.0, "korupsi": -3.0
}
sia.lexicon.update(kosakata_saham)

# Cache for news to avoid repeated failed requests
_news_cache = {}
_cache_timestamp = {}

def fetch_alternative_news_source(ticker):
    """
    Try to fetch news from alternative sources when yfinance fails
    """
    try:
        # Try using NewsAPI or alternative financial data sources
        # For now, we'll try fetching from investing.com or other sources
        import requests
        from xml.etree import ElementTree as ET
        
        # Try fetching from Google News RSS feed
        search_query = f"{ticker} stock news"
        url = f"https://news.google.com/rss/search?q={search_query}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=3)
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            items = root.findall(".//item")
            news_list = []
            
            for item in items[:5]:  # Get first 5 news items
                title_elem = item.find('title')
                if title_elem is not None and title_elem.text:
                    news_list.append({'content': {'title': title_elem.text}})
            
            return news_list if news_list else []
    except:
        pass
    
    return []

def fetch_yahoo_finance_news(ticker, use_cache=True):
    """
    Fetch news from Yahoo Finance with retry logic and caching
    Falls back to alternative sources if yfinance fails
    """
    try:
        full_tkr = f"{ticker}.JK" if not ticker.endswith(".JK") else ticker
        
        # Check cache (valid for 5 minutes)
        if use_cache and full_tkr in _news_cache:
            cache_age = time.time() - _cache_timestamp.get(full_tkr, 0)
            if cache_age < 300:  # 5 minutes
                return _news_cache[full_tkr]
        
        # Try to fetch news from yfinance
        try:
            stock = yf.Ticker(full_tkr)
            news = stock.news
            
            # Check if news is valid (not None and is a list)
            if news and isinstance(news, list) and len(news) > 0:
                _news_cache[full_tkr] = news
                _cache_timestamp[full_tkr] = time.time()
                return news
        except:
            pass
        
        # Fallback to alternative news source
        alt_news = fetch_alternative_news_source(ticker)
        if alt_news:
            _news_cache[full_tkr] = alt_news
            _cache_timestamp[full_tkr] = time.time()
            return alt_news
        
        # Cache empty result
        _news_cache[full_tkr] = []
        _cache_timestamp[full_tkr] = time.time()
        return []
        
    except Exception as e:
        # Return cached result if available, even if expired
        full_tkr = f"{ticker}.JK" if not ticker.endswith(".JK") else ticker
        if full_tkr in _news_cache:
            return _news_cache[full_tkr]
        return []

def get_sentiment(ticker):
    """
    Scraper untuk menarik berita terbaru dan menganalisis sentimennya menggunakan NLP.
    """
    try:
        full_tkr = f"{ticker}.JK" if not ticker.endswith(".JK") else ticker
        
        # Fetch news using alternative methods
        berita = fetch_yahoo_finance_news(ticker)
        
        if not berita:
            return 0.0, "NO_NEWS"

        total_skor = 0
        news_count = 0
        
        # Mengambil maksimal 5 berita paling segar
        for artikel in berita[:5]:
            # Handle different news formats
            if isinstance(artikel, dict):
                # Check if it's nested format (yfinance)
                if 'content' in artikel and isinstance(artikel['content'], dict):
                    judul = artikel['content'].get('title', '')
                else:
                    # Check if title is at top level
                    judul = artikel.get('title', '')
            else:
                judul = str(artikel)
            
            if judul and len(judul) > 5:
                # Mesin NLP membaca judul berita dan memberikan skor komponen
                skor = sia.polarity_scores(judul)['compound']
                total_skor += skor
                news_count += 1
        
        if news_count == 0:
            return 0.0, "NO_NEWS"
        
        rata_rata_skor = total_skor / news_count
        
        # Mengkategorikan hasil NLP
        if rata_rata_skor >= 0.15:
            return rata_rata_skor, "BULLISH"
        elif rata_rata_skor <= -0.15:
            return rata_rata_skor, "BEARISH"
        else:
            return rata_rata_skor, "NEUTRAL"
            
    except Exception as e:
        return 0.0, "NEUTRAL"

if __name__ == "__main__":
    # Test Scraper
    print("Mencoba membaca sentimen berita GOTO...")
    skor, label = get_sentiment("GOTO")
    print(f"Hasil NLP -> Skor: {skor:.2f} | Label: {label}")