# 🚀 SARAN PENGEMBANGAN LANJUTAN - SCREENER SYSTEM

**Roadmap:** Q2 2026 - Q4 2026 | **Priority:** Strategic Enhancement

---

## 1️⃣ ARCHITECTURE MODERNIZATION

### A. Migrate dari Polling ke Event-Driven Architecture

**Current (Polling):**
```python
# File 2 polling setiap 0.2-3 detik
while True:
    cursor.execute("SELECT ... WHERE id > ?")
    time.sleep(POLL_FAST)  # Inefficient!
```

**Target (Event-Driven):**
```python
# Menggunakan message queue (RabbitMQ/Redis/Kafka)
import pika

def producer_callback():
    while True:
        # Fetch data
        for ticker, harga in results:
            channel.basic_publish(
                exchange='price_updates',
                routing_key=f'ticker.{ticker}',
                body=json.dumps({'ticker': ticker, 'harga': harga})
            )

def consumer_callback(ch, method, properties, body):
    data = json.loads(body)
    hasil = analisis_saham(data['ticker'])
    # Process...
```

**Benefit:**
- ✅ CPU usage turun 80% (no continuous polling)
- ✅ Real-time processing (latency < 100ms)
- ✅ Scalable (add consumers tanpa bottleneck)
- ✅ Resilient (message queue = durability)

**Estimate:** 3-4 hari development

---

### B. Containerization dengan Docker

**Buat Dockerfile untuk setiap service:**

```dockerfile
# Dockerfile.producer
FROM python:3.11-slim
WORKDIR /app
COPY 1_producer_data.py .
COPY requirements.txt .
RUN pip install -r requirements.txt
CMD ["python", "1_producer_data.py"]

# Dockerfile.consumer_ai
FROM python:3.11-slim
WORKDIR /app
COPY 2_consumer_ai.py screener.py .
COPY requirements.txt .
RUN pip install -r requirements.txt
CMD ["python", "2_consumer_ai.py"]

# Dockerfile.consumer_rl
FROM python:3.11-slim
WORKDIR /app
COPY 3_consumer_rl.py .
COPY requirements.txt .
RUN pip install -r requirements.txt
CMD ["python", "3_consumer_rl.py"]
```

**docker-compose.yml:**
```yaml
version: '3.9'

services:
  producer:
    build:
      context: .
      dockerfile: Dockerfile.producer
    container_name: producer_data
    environment:
      - DB_NAME=histori_ihsg.db
    volumes:
      - ./data:/app/data
    networks:
      - screener_network
    restart: unless-stopped

  rabbitmq:
    image: rabbitmq:3.12-management
    container_name: rabbitmq
    ports:
      - "5672:5672"
      - "15672:15672"
    environment:
      - RABBITMQ_DEFAULT_USER=screener
      - RABBITMQ_DEFAULT_PASS=password123
    networks:
      - screener_network

  consumer_ai:
    build:
      context: .
      dockerfile: Dockerfile.consumer_ai
    container_name: consumer_ai
    depends_on:
      - producer
      - rabbitmq
    networks:
      - screener_network
    restart: unless-stopped

  consumer_rl:
    build:
      context: .
      dockerfile: Dockerfile.consumer_rl
    container_name: consumer_rl
    depends_on:
      - consumer_ai
    volumes:
      - ./data:/app/data
    networks:
      - screener_network
    restart: unless-stopped

networks:
  screener_network:
    driver: bridge
```

**Run:**
```bash
docker-compose up -d
docker-compose logs -f  # Monitor all services
```

**Benefit:**
- ✅ Environment consistency (dev = prod)
- ✅ Easy scaling (scale consumer_rl=3)
- ✅ Simplified deployment
- ✅ Resource isolation

---

## 2️⃣ FEATURE ENHANCEMENTS

### A. Advanced Risk Management

**Current:** Max 20% per saham, hard stop at SL

**Proposed:**

```python
class AdvancedRiskManager:
    """
    1. Dynamic position sizing berdasarkan volatility
    2. Trailing stop loss
    3. Partial profit taking (3-level exit)
    4. Correlation-based portfolio rebalancing
    """
    
    def __init__(self, initial_capital=100_000_000):
        self.capital = initial_capital
        self.positions = []
        self.volatility_cache = {}
    
    def hitung_optimal_size(self, ticker, rr_ratio, volatility):
        """
        Sizing formula: f = (2p - 1) / b
        Dimana:
        - p = win probability (dari historical data)
        - b = risk-reward ratio
        - f = fraction of capital to risk
        """
        win_prob = self.get_win_probability(ticker)  # Query dari trade_history
        kelly_fraction = (2 * win_prob - 1) / rr_ratio
        
        # Limit ke 25% untuk safety
        kelly_fraction = min(max(kelly_fraction, 0.02), 0.25)
        
        position_size = self.capital * kelly_fraction
        shares = int(position_size / self.get_current_price(ticker))
        
        log.info(f"Kelly Criterion: {kelly_fraction:.1%} | Shares: {shares}")
        return shares
    
    def trailing_stop_loss(self, position, current_price, trail_pct=2.0):
        """
        Jika profit > trail_pct, geser SL ke entry + 0.5%
        Ini lock-in profit sambil tetap let winners run.
        """
        profit_pct = (current_price - position['buy_price']) / position['buy_price']
        
        if profit_pct > trail_pct / 100:
            new_sl = position['buy_price'] * 1.005  # 0.5% above entry
            if new_sl > position['sl']:
                log.info(f"🔄 Trailing SL: {position['sl']:.0f} → {new_sl:.0f}")
                position['sl'] = new_sl
    
    def partial_exit(self, position, current_price):
        """
        3-level exit strategy:
        - Level 1 (TP/3): Sell 50% shares, move SL to entry
        - Level 2 (TP*2/3): Sell 30% shares
        - Level 3 (TP): Sell remaining 20% shares
        """
        tp = position['tp']
        
        if current_price >= tp * 2/3 and position['exit_level'] < 1:
            self.close_partial(position, 0.5, 1)
        elif current_price >= tp * 4/3 and position['exit_level'] < 2:
            self.close_partial(position, 0.3, 2)
        elif current_price >= tp and position['exit_level'] < 3:
            self.close_partial(position, 0.2, 3)
```

**Benefit:** Maximize profits dengan dynamic sizing + lock-in gains

---

### B. Multi-Timeframe Analysis

**Current:** Single timeframe analysis

**Proposed:**

```python
class MultiTimeframeAnalyzer:
    """
    Analisis di multiple timeframe untuk confirmation:
    - Daily trend (primary)
    - 4-hour (secondary)
    - Hourly (entry confirmation)
    """
    
    def __init__(self):
        self.timeframes = ['1d', '4h', '1h']
        self.data_fetcher = PriceDataFetcher()  # NEW
    
    def analisis_multitf(self, ticker):
        """
        1. Daily: Trend identification (uptrend/downtrend/range)
        2. 4-hour: Support/resistance levels
        3. Hourly: Entry confirmation signal
        """
        results = {}
        
        for tf in self.timeframes:
            data = self.data_fetcher.get_historical_data(ticker, tf, periods=100)
            
            # Technical indicators
            rsi = self.hitung_rsi(data, period=14)
            macd = self.hitung_macd(data)
            bb = self.hitung_bollinger_bands(data)
            
            results[tf] = {
                'trend': self.identify_trend(data),
                'rsi': rsi[-1],
                'macd': macd[-1],
                'bollinger': bb[-1],
                'support': self.find_support(data),
                'resistance': self.find_resistance(data)
            }
        
        # Confirmation: semua timeframe harus agree
        is_confirmed = self.check_confirmation(results)
        
        return {
            'analysis': results,
            'signal': self.generate_signal(results),
            'confidence': self.hitung_confidence_score(results),
            'is_confirmed': is_confirmed
        }
    
    def generate_signal(self, results):
        """
        Signal hanya dikirim jika:
        - Daily trend agree dengan 4h trend
        - Hourly RSI overextended (confirmation)
        - Price near support/resistance dari multiple timeframes
        """
        daily = results['1d']
        h4 = results['4h']
        hourly = results['1h']
        
        # Check alignment
        if daily['trend'] != h4['trend']:
            return 'NEUTRAL'  # Conflicting trends
        
        # Check strength
        if daily['trend'] == 'UPTREND' and hourly['rsi'] < 30:
            return 'STRONG_BUY'  # Oversold on hourly, uptrend on daily
        elif daily['trend'] == 'DOWNTREND' and hourly['rsi'] > 70:
            return 'STRONG_SELL'
        
        return 'BUY' if daily['trend'] == 'UPTREND' else 'SELL'
```

**Benefit:** Better entry confirmation, reduce false signals

---

### C. Market Regime Detection

**Current:** Same signal logic untuk semua kondisi pasar

**Proposed:**

```python
class MarketRegimeDetector:
    """
    Deteksi market condition & adjust strategy accordingly:
    1. Trending market → use momentum strategy
    2. Range-bound market → use mean-reversion strategy
    3. High volatility → reduce position size
    4. Low liquidity → skip (avoid slippage)
    """
    
    def detect_regime(self):
        """Return: 'TRENDING', 'RANGE_BOUND', 'HIGHLY_VOLATILE'"""
        # Get IHSG index price
        ihsg_data = self.fetch_ihsg_data(periods=100)
        
        # Calculate ADX (trend strength)
        adx = self.hitung_adx(ihsg_data)
        
        # Calculate ATR (volatility)
        atr = self.hitung_atr(ihsg_data)
        atr_ratio = atr[-1] / ihsg_data['close'][-1]  # ATR as % of price
        
        # Calculate Bollinger Band width (range-boundness)
        bb_width = self.hitung_bb_width(ihsg_data)
        
        if adx[-1] > 40:
            return 'STRONG_TREND'
        elif adx[-1] > 25:
            return 'TRENDING'
        elif bb_width < 0.02:  # Bands narrow
            return 'RANGE_BOUND'
        elif atr_ratio > 0.05:  # High volatility
            return 'HIGHLY_VOLATILE'
        else:
            return 'NORMAL'
    
    def adjust_strategy(self, regime, signal):
        """Modify signal berdasarkan market regime"""
        
        if regime == 'STRONG_TREND':
            # Increase position size, follow trend aggressively
            position_multiplier = 1.5
            return signal
        
        elif regime == 'RANGE_BOUND':
            # Use mean-reversion, trade the extremes
            if signal == 'BUY':
                signal = 'BUY_REVERSION'  # Different SL/TP
        
        elif regime == 'HIGHLY_VOLATILE':
            # Reduce position size, wider SL
            position_multiplier = 0.5
            return signal
        
        return signal
```

---

## 3️⃣ MONITORING & OBSERVABILITY

### A. Real-time Dashboard

```python
# dashboard.py - Using Streamlit/Dash
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta

st.set_page_config(page_title="Screener Dashboard", layout="wide")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("Total Capital", "Rp 100.0M", "+5.2%")
with col2:
    st.metric("Open Positions", 7, "+2")
with col3:
    st.metric("Win Rate", "62.5%", "+5%")
with col4:
    st.metric("Drawdown", "-12.3%", "-1.2%")

# Profit chart
tab1, tab2, tab3 = st.tabs(["Equity Curve", "Trades", "Performance"])

with tab1:
    equity_data = query_equity_curve(days=30)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity_data['date'],
        y=equity_data['equity'],
        mode='lines+markers',
        name='Equity'
    ))
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    trades_df = query_recent_trades(limit=50)
    st.dataframe(
        trades_df[['ticker', 'entry', 'exit', 'profit', 'win_rate']],
        use_container_width=True
    )

with tab3:
    # Performance metrics
    metrics = {
        'Total Trades': 150,
        'Winning Trades': 94,
        'Losing Trades': 56,
        'Win Rate': '62.7%',
        'Avg Win': 'Rp 125K',
        'Avg Loss': 'Rp -85K',
        'Profit Factor': 2.14,
        'Sharpe Ratio': 1.8,
        'Max Drawdown': '-12.3%'
    }
    for k, v in metrics.items():
        st.metric(k, v)
```

**Deploy dengan:**
```bash
streamlit run dashboard.py --server.port 8501
# Akses: http://localhost:8501
```

---

### B. Centralized Logging & Alerting

```python
# logging_manager.py
import logging
from logging.handlers import RotatingFileHandler
import json
from datetime import datetime

class StructuredLogger:
    """JSON-based logging untuk easy parsing & analysis"""
    
    def __init__(self):
        self.logger = logging.getLogger("screener")
        
        # File handler dengan rotation
        fh = RotatingFileHandler(
            'logs/screener.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        fh.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(fh)
    
    def log_trade(self, ticker, action, price, shares, sl, tp):
        """Log every trade dengan struktur JSON"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'event': 'TRADE',
            'action': action,  # BUY/SELL
            'ticker': ticker,
            'price': price,
            'shares': shares,
            'sl': sl,
            'tp': tp,
            'risk_reward': (tp - price) / (price - sl) if (price - sl) > 0 else 0
        }
        self.logger.info(json.dumps(log_entry))
    
    def log_signal(self, ticker, signal, confidence, reason):
        """Log signal generation"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'event': 'SIGNAL',
            'ticker': ticker,
            'signal': signal,
            'confidence': confidence,
            'reason': reason
        }
        self.logger.info(json.dumps(log_entry))


# alerting.py - Send alerts untuk critical events
class AlertManager:
    """Send alerts via Email/Telegram/Slack"""
    
    def __init__(self):
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
    
    def alert_high_drawdown(self, current_dd, threshold=-15):
        """Alert jika drawdown > threshold"""
        if current_dd < threshold:
            self.send_telegram(
                f"⚠️ HIGH DRAWDOWN ALERT\n"
                f"Current: {current_dd:.1f}%\n"
                f"Action: Review positions"
            )
    
    def alert_rate_limit(self):
        """Alert jika hit rate limit"""
        self.send_telegram(
            "🚫 RATE LIMIT DETECTED\n"
            f"Producer backoff activated\n"
            f"Resuming in 5 minutes..."
        )
    
    def send_telegram(self, message):
        """Send via Telegram bot"""
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        requests.post(url, json={
            'chat_id': self.telegram_chat_id,
            'text': message
        })
```

---

## 4️⃣ MACHINE LEARNING ENHANCEMENTS

### A. Signal Confidence Scoring

```python
class MLSignalValidator:
    """Use ML untuk validate signals sebelum execution"""
    
    def __init__(self):
        self.model = self.load_trained_model('models/signal_validator.pkl')
        self.scaler = self.load_scaler('models/feature_scaler.pkl')
    
    def validate_signal(self, ticker, signal_data):
        """
        Input features:
        - Technical indicators (RSI, MACD, BB)
        - Market regime
        - Historical win rate (untuk ticker ini)
        - Signal strength
        - Recent volatility
        """
        features = self.extract_features(signal_data)
        features_scaled = self.scaler.transform([features])
        
        confidence = self.model.predict_proba(features_scaled)[0][1]  # Class 1 = good signal
        
        log.info(f"Signal confidence: {confidence:.1%}")
        
        # Only execute jika confidence > 65%
        return confidence > 0.65, confidence
    
    def extract_features(self, signal_data):
        """Extract machine learning features"""
        return [
            signal_data['rsi'],
            signal_data['macd'],
            signal_data['volatility'],
            signal_data['historical_win_rate'],
            signal_data['market_regime_score'],
            signal_data['time_of_day'],  # Avoid low-liquidity hours
            signal_data['days_since_last_trade'],
            signal_data['cumulative_pl_today']
        ]
```

**Training script:**
```python
# train_signal_validator.py
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import pandas as pd

# Load historical trade data
trades = pd.read_sql("SELECT * FROM trade_history", conn)

# Feature engineering
X = trades[['rsi', 'macd', 'volatility', 'win_rate', 'market_regime']]
y = trades['was_profitable'].astype(int)  # Target: profitable or not

# Train
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_scaled, y)

# Save
joblib.dump(model, 'models/signal_validator.pkl')
joblib.dump(scaler, 'models/feature_scaler.pkl')
```

---

### B. Dynamic Strategy Selection

```python
class StrategySelector:
    """
    Automatically select best strategy berdasarkan market conditions
    dan historical performance
    """
    
    STRATEGIES = {
        'momentum': MomentumStrategy(),
        'mean_reversion': MeanReversionStrategy(),
        'volatility_breakout': VolatilityBreakoutStrategy()
    }
    
    def select_best_strategy(self, ticker, market_regime):
        """
        Query historical performance untuk setiap strategy
        di market regime ini, pilih yang paling profitable
        """
        best_strategy = None
        best_roi = -100
        
        for strategy_name, strategy in self.STRATEGIES.items():
            # Query win rate untuk strategy ini
            win_rate = self.query_strategy_performance(
                strategy_name, 
                ticker, 
                market_regime
            )
            
            if win_rate > best_roi:
                best_roi = win_rate
                best_strategy = strategy_name
        
        log.info(f"Using {best_strategy} strategy (ROI: {best_roi:.1%})")
        return best_strategy
```

---

## 5️⃣ OPERATIONAL EXCELLENCE

### A. Backtesting & Paper Trading Framework

```python
# backtest.py
class BacktestEngine:
    """Simulate trading dengan historical data"""
    
    def run_backtest(self, strategy, data, initial_capital=100_000_000):
        """
        1. Load historical price data
        2. For each price bar:
           - Generate signal
           - Execute trade (simulated)
           - Track P&L
        3. Calculate performance metrics
        """
        self.capital = initial_capital
        self.equity = initial_capital
        self.positions = []
        self.trades = []
        
        for i, row in data.iterrows():
            # Check exits
            for position in self.positions[:]:
                if row['price'] <= position['sl']:
                    self.close_position(position, row['price'], 'SL')
                elif row['price'] >= position['tp']:
                    self.close_position(position, row['price'], 'TP')
            
            # Generate signal
            signal = strategy.generate_signal(data.iloc[:i+1])
            
            if signal == 'BUY':
                position = {
                    'entry_price': row['price'],
                    'shares': self.capital * 0.2 / row['price'],
                    'sl': row['price'] * 0.98,
                    'tp': row['price'] * 1.05
                }
                self.positions.append(position)
            
            self.equity = self.capital + sum(
                p['shares'] * (row['price'] - p['entry_price']) 
                for p in self.positions
            )
        
        # Calculate metrics
        return self.calculate_metrics()
    
    def calculate_metrics(self):
        """Calculate Sharpe ratio, max drawdown, win rate, etc."""
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t['pl'] > 0)
        
        return {
            'total_trades': total_trades,
            'win_rate': winning_trades / total_trades if total_trades > 0 else 0,
            'avg_profit': sum(t['pl'] for t in self.trades) / total_trades,
            'max_drawdown': self.calculate_max_drawdown(),
            'sharpe_ratio': self.calculate_sharpe_ratio(),
            'roi': (self.equity - self.capital) / self.capital * 100
        }
```

**Usage:**
```python
# Test strategy dengan historical data
backtest = BacktestEngine()
results = backtest.run_backtest(
    strategy=screener_strategy,
    data=load_historical_data('2024-01-01', '2026-04-01')
)

print(f"Win Rate: {results['win_rate']:.1%}")
print(f"ROI: {results['roi']:.1%}")
print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
```

---

### B. Automated Performance Reporting

```python
# reporting.py
class PerformanceReporter:
    """Generate daily/weekly/monthly performance reports"""
    
    def generate_daily_report(self):
        """Email laporan setiap pukul 17:00"""
        today = datetime.now().date()
        
        trades_today = self.query_trades_by_date(today)
        total_pl = sum(t['pl'] for t in trades_today)
        win_count = sum(1 for t in trades_today if t['pl'] > 0)
        
        report = f"""
        📊 SCREENER DAILY REPORT - {today}
        
        TRADING SUMMARY
        ├─ Total Trades: {len(trades_today)}
        ├─ Winning Trades: {win_count}
        ├─ Daily P/L: Rp {total_pl:,.0f}
        └─ Win Rate: {win_count/len(trades_today)*100:.1f}%
        
        TOP TRADES
        {self.format_trades_table(trades_today[:5])}
        
        CAPITAL STATUS
        ├─ Current Equity: Rp {self.get_current_equity():,.0f}
        ├─ ROI YTD: {self.calculate_ytd_roi():.1%}
        └─ Current Drawdown: {self.get_current_drawdown():.1%}
        
        NEXT ACTIONS
        ├─ Positions to monitor: {len(self.get_open_positions())}
        └─ Recent alerts: {len(self.get_alerts())}
        """
        
        self.send_email(report)
        self.send_telegram(report)
        
        return report
    
    def generate_weekly_report(self):
        """Detailed analysis + recommendations"""
        # Similar structure tapi lebih detailed
        pass
```

---

## 6️⃣ SCALABILITY ROADMAP

### Phase 1: Current (Single Machine)
- ✅ 200 ticker
- ✅ File-based SQLite
- ✅ Synchronous producer

### Phase 2: Q2 2026 (Multi-service)
- ✅ Message queue (RabbitMQ)
- ✅ PostgreSQL (shared database)
- ✅ Docker containers
- ✅ Multiple consumer instances

### Phase 3: Q3 2026 (High Volume)
- ✅ 2000+ ticker (add global stocks)
- ✅ Redis cache layer
- ✅ Kubernetes orchestration
- ✅ ML model serving (TensorFlow Serving)

### Phase 4: Q4 2026 (Enterprise)
- ✅ Multi-exchange integration (NYSE, NASDAQ, etc.)
- ✅ Real portfolio API integration (broker connect)
- ✅ Advanced risk management (VaR, stress testing)
- ✅ Regulatory compliance (audit logging)

---

## 7️⃣ INTEGRATION OPPORTUNITIES

### A. Broker API Integration

```python
class BrokerIntegrator:
    """
    Execute trades di broker real, bukan virtual portfolio
    Supported brokers: Binance, Indodax, UpBit, dll
    """
    
    def __init__(self, broker_name, api_key, api_secret):
        if broker_name == 'binance':
            self.client = ccxt.binance({'apiKey': api_key, 'secret': api_secret})
        elif broker_name == 'indodax':
            self.client = ccxt.indodax({'apiKey': api_key, 'secret': api_secret})
    
    def execute_buy(self, ticker, harga, shares):
        """Execute BUY order di broker"""
        try:
            order = self.client.create_limit_buy_order(
                symbol=ticker,
                amount=shares,
                price=harga
            )
            log.info(f"Order placed: {order['id']}")
            return order
        except Exception as e:
            log.error(f"Failed to place order: {e}")
```

---

### B. External API Integration

```python
class ExternalDataIntegrator:
    """
    Integrate external data sources untuk better signals:
    - News sentiment analysis
    - Earnings calendar
    - Economic calendar
    - Insider trading data
    """
    
    def get_news_sentiment(self, ticker):
        """Fetch news sentiment dari NewsAPI atau similar"""
        response = requests.get(
            f"https://newsapi.org/v2/everything?q={ticker}&sortBy=publishedAt"
        )
        news = response.json()['articles']
        
        sentiment_scores = [self.analyze_sentiment(article['title']) for article in news]
        avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
        
        return avg_sentiment  # -1 to +1
    
    def get_earnings_dates(self, ticker):
        """Check if stock akan earnings dalam 5 hari (avoid earnings)"""
        # Query dari earnings calendar API
        pass
```

---

## 8️⃣ COMPLIANCE & SECURITY

### A. Audit Logging

```python
class AuditLogger:
    """Track semua actions untuk compliance"""
    
    def log_event(self, event_type, details, user=None):
        """
        event_type: TRADE, CONFIGURATION_CHANGE, ACCESS, ERROR
        """
        audit_entry = {
            'timestamp': datetime.now().isoformat(),
            'event_type': event_type,
            'user': user or 'SYSTEM',
            'details': details,
            'ip_address': self.get_ip(),
            'environment': os.getenv('ENV', 'production')
        }
        
        # Store in immutable log
        self.db.execute(
            "INSERT INTO audit_log (entry) VALUES (?)",
            (json.dumps(audit_entry),)
        )
```

### B. Data Encryption & Secrets Management

```python
# config.py - Using python-dotenv
from dotenv import load_dotenv
import os

load_dotenv('.env')  # Never commit .env file!

DB_PASSWORD = os.getenv('DB_PASSWORD')
API_KEY = os.getenv('API_KEY')
BROKER_SECRET = os.getenv('BROKER_SECRET')

# Use AWS Secrets Manager atau HashiCorp Vault untuk production
```

---

## 📋 IMPLEMENTATION PRIORITY

```
Q2 2026 (Next 6 weeks):
├─ Week 1-2: Error handling fixes (BUG_SUMMARY)
├─ Week 2-3: Event-driven migration (RabbitMQ)
├─ Week 3-4: Advanced risk management
└─ Week 5-6: Dashboard + monitoring

Q3 2026 (Next 3 months):
├─ Multi-timeframe analysis
├─ Market regime detection
├─ Backtesting framework
├─ Docker/Kubernetes
└─ PostgreSQL migration

Q4 2026 (Next 6 months):
├─ ML signal validation
├─ Broker API integration
├─ 2000+ ticker support
└─ Regulatory compliance
```

---

## 💡 QUICK WINS (Start Now!)

1. **Add Health Check Endpoint** (30 min)
   ```python
   @app.get("/health")
   def health_check():
       return {
           "status": "healthy",
           "producer_alive": check_producer(),
           "consumer_ai_alive": check_consumer_ai(),
           "db_accessible": check_database()
       }
   ```

2. **Add Metrics Export** (1 hour)
   ```python
   # Export metrics untuk Prometheus
   trades_total.inc()
   signal_confidence.observe(confidence)
   ```

3. **Simple Slack Alerts** (1 hour)
   ```python
   def send_slack_alert(message):
       requests.post(SLACK_WEBHOOK, json={'text': message})
   ```

4. **CSV Export for Analysis** (30 min)
   ```python
   trades_df.to_csv(f'exports/trades_{date.today()}.csv')
   ```

---

**Status:** Ready for planning & prioritization
**Next Step:** Pick 3-5 items untuk Q2 sprint planning
