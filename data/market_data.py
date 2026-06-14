# ════════════════════════════════════════════════════════════════
# OMNINEXUS — data/market_data.py
# Live Market Data Engine
# Uses Twelve Data WebSocket for real-time prices (0 REST credits)
# Uses Twelve Data REST for OHLCV candles + indicators
# Uses yfinance for 10yr historical data
# ════════════════════════════════════════════════════════════════

import asyncio
import json
import logging
import requests
import threading
import time
import websocket
from datetime import datetime, timedelta
from typing import Optional
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.data.market_data')

TD_WS_URL  = 'wss://ws.twelvedata.com/v1/quotes/price'
TD_REST    = 'https://api.twelvedata.com'


# ── PRICE CACHE ────────────────────────────────────────────────
# Stores latest prices from WebSocket in memory
# All other modules read from this cache — no extra API calls
_price_cache: dict = {}
_api_calls_today: int = 0
_api_calls_reset: datetime = datetime.utcnow().replace(
    hour=0, minute=0, second=0
)


def _track_api_call():
    """Track daily REST API usage."""
    global _api_calls_today, _api_calls_reset
    now = datetime.utcnow()
    if now.date() > _api_calls_reset.date():
        _api_calls_today = 0
        _api_calls_reset = now
    _api_calls_today += 1
    if _api_calls_today > config.TD_DAILY_BUDGET * 0.9:
        logger.warning(
            f'API budget at {_api_calls_today}/'
            f'{config.TD_DAILY_BUDGET} — slowing down'
        )


def get_api_usage() -> dict:
    """Returns current API usage stats."""
    return {
        'calls_today':  _api_calls_today,
        'daily_budget': config.TD_DAILY_BUDGET,
        'remaining':    config.TD_DAILY_BUDGET - _api_calls_today,
        'pct_used':     round(
            _api_calls_today / config.TD_DAILY_BUDGET * 100, 1
        ),
    }


# ── WEBSOCKET PRICE STREAMER ───────────────────────────────────

class PriceStreamer:
    """
    Streams live prices from Twelve Data WebSocket.
    Costs zero REST API credits.
    Runs in a background thread so the bot stays responsive.
    """

    def __init__(self):
        self.ws          = None
        self.connected   = False
        self.running     = False
        self._thread     = None

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            event = data.get('event')

            if event == 'price':
                symbol_td = data.get('symbol')
                price     = float(data.get('price', 0))
                ts        = data.get('timestamp', int(time.time()))

                # Map Twelve Data symbol back to our name
                for name, td_sym in config.TD_SYMBOLS.items():
                    if td_sym == symbol_td:
                        _price_cache[name] = {
                            'price':     price,
                            'timestamp': ts,
                            'updated':   datetime.utcnow().isoformat(),
                            'source':    'websocket',
                        }
                        logger.debug(
                            f'WS price: {name} = {price}'
                        )

            elif event == 'subscribe-status':
                logger.info(
                    f'WebSocket subscribed: '
                    f'{data.get("status")} — '
                    f'{data.get("message", "")}'
                )

            elif event == 'heartbeat':
                logger.debug('WebSocket heartbeat received')

        except Exception as e:
            logger.error(f'WebSocket message error: {e}')

    def on_open(self, ws):
        self.connected = True
        logger.info('Twelve Data WebSocket connected')
        # Subscribe to all instruments
        symbols = list(config.TD_SYMBOLS.values())
        subscribe_msg = {
            'action': 'subscribe',
            'params': {
                'symbols': ','.join(symbols),
            }
        }
        ws.send(json.dumps(subscribe_msg))
        logger.info(f'Subscribed to: {symbols}')

    def on_error(self, ws, error):
        logger.error(f'WebSocket error: {error}')
        self.connected = False

    def on_close(self, ws, close_status_code, close_msg):
        self.connected = False
        logger.warning(
            f'WebSocket closed: {close_status_code} — {close_msg}'
        )
        # Auto-reconnect after 10 seconds
        if self.running:
            logger.info('Reconnecting in 10s...')
            time.sleep(10)
            self.start()

    def start(self):
        """Starts WebSocket in background thread."""
        self.running = True
        ws_url = (
            f'{TD_WS_URL}'
            f'?apikey={config.TWELVE_DATA_API_KEY}'
        )
        self.ws = websocket.WebSocketApp(
            ws_url,
            on_message = self.on_message,
            on_open    = self.on_open,
            on_error   = self.on_error,
            on_close   = self.on_close,
        )
        self._thread = threading.Thread(
            target=self.ws.run_forever,
            kwargs={'ping_interval': 30, 'ping_timeout': 10},
            daemon=True
        )
        self._thread.start()
        logger.info('Price streamer started in background')

    def stop(self):
        """Stops WebSocket stream."""
        self.running = False
        if self.ws:
            self.ws.close()
        logger.info('Price streamer stopped')


# ── SINGLETON STREAMER ─────────────────────────────────────────
_streamer = PriceStreamer()


def start_price_stream():
    """Call once at system startup to begin live price streaming."""
    _streamer.start()
    # Wait up to 10s for first prices
    for _ in range(20):
        if len(_price_cache) >= len(config.INSTRUMENTS):
            logger.info('All instrument prices received')
            break
        time.sleep(0.5)


def get_live_price(instrument: str) -> Optional[dict]:
    """
    Returns latest cached price for an instrument.
    Falls back to REST API if WebSocket hasn't received yet.
    """
    if instrument in _price_cache:
        cached = _price_cache[instrument]
        # Check if price is fresh (less than 2 minutes old)
        age = time.time() - cached['timestamp']
        if age < 120:
            return cached

    # Fallback: fetch via REST
    return _fetch_price_rest(instrument)


def _fetch_price_rest(instrument: str) -> Optional[dict]:
    """Fetches current price via REST as fallback."""
    try:
        _track_api_call()
        symbol = config.TD_SYMBOLS.get(instrument)
        if not symbol:
            return None

        r = requests.get(
            f'{TD_REST}/price',
            params={
                'symbol': symbol,
                'apikey': config.TWELVE_DATA_API_KEY,
            },
            timeout=10
        )
        data = r.json()
        price = float(data.get('price', 0))
        if price > 0:
            result = {
                'price':     price,
                'timestamp': int(time.time()),
                'updated':   datetime.utcnow().isoformat(),
                'source':    'rest',
            }
            _price_cache[instrument] = result
            logger.info(f'REST price: {instrument} = {price}')
            return result

        return None

    except Exception as e:
        logger.error(f'REST price fetch error for {instrument}: {e}')
        return None


def get_all_prices() -> dict:
    """Returns current prices for all instruments."""
    prices = {}
    for instrument in config.INSTRUMENTS:
        data = get_live_price(instrument)
        prices[instrument] = data['price'] if data else None
    return prices


# ── OHLCV CANDLES ──────────────────────────────────────────────

def get_candles(
    instrument: str,
    interval:   str  = '1h',
    outputsize: int  = 100,
) -> Optional[list]:
    """
    Fetches OHLCV candles from Twelve Data REST.
    interval: 1min, 5min, 15min, 30min, 1h, 4h, 1day
    outputsize: number of candles (max 5000)
    Returns list of dicts with open, high, low, close, volume.
    """
    try:
        _track_api_call()
        symbol = config.TD_SYMBOLS.get(instrument)
        if not symbol:
            return None

        r = requests.get(
            f'{TD_REST}/time_series',
            params={
                'symbol':     symbol,
                'interval':   interval,
                'outputsize': outputsize,
                'apikey':     config.TWELVE_DATA_API_KEY,
            },
            timeout=15
        )
        data = r.json()

        if data.get('status') == 'error':
            logger.error(
                f'Candles error for {instrument}: '
                f'{data.get("message")}'
            )
            return None

        values = data.get('values', [])
        candles = []
        for v in values:
            candles.append({
                'datetime': v.get('datetime'),
                'open':     float(v.get('open',  0)),
                'high':     float(v.get('high',  0)),
                'low':      float(v.get('low',   0)),
                'close':    float(v.get('close', 0)),
                'volume':   float(v.get('volume', 0)),
            })

        logger.info(
            f'Candles: {instrument} {interval} — '
            f'{len(candles)} bars'
        )
        return candles

    except Exception as e:
        logger.error(f'Candles fetch error for {instrument}: {e}')
        return None


# ── 10-YEAR HISTORICAL DATA ────────────────────────────────────

def get_historical_data(
    instrument: str,
    period:     str = '10y',
    interval:   str = '1d',
) -> Optional[object]:
    """
    Fetches 10 years of historical data using yfinance.
    Completely free, no API key needed.
    Returns a pandas DataFrame with OHLCV data.
    period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
    interval: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo
    """
    try:
        import yfinance as yf
        yf_symbol = config.YF_SYMBOLS.get(instrument)
        if not yf_symbol:
            logger.error(f'No yfinance symbol for {instrument}')
            return None

        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            logger.warning(
                f'No historical data for {instrument}'
            )
            return None

        logger.info(
            f'Historical: {instrument} — '
            f'{len(df)} bars | '
            f'{df.index[0].date()} to {df.index[-1].date()}'
        )
        return df

    except Exception as e:
        logger.error(
            f'Historical data error for {instrument}: {e}'
        )
        return None


# ── FOREX NEWS (FINNHUB) ───────────────────────────────────────

def get_forex_news(category: str = 'forex') -> list:
    """
    Fetches latest forex news from Finnhub.
    Free — uses Finnhub 60 req/min quota.
    """
    try:
        r = requests.get(
            'https://finnhub.io/api/v1/news',
            params={
                'category': category,
                'token':    config.FINNHUB_API_KEY,
            },
            timeout=10
        )
        news = r.json()
        if isinstance(news, list):
            logger.info(f'News: {len(news)} articles fetched')
            return news[:20]
        return []

    except Exception as e:
        logger.error(f'News fetch error: {e}')
        return []


# ── MARKET STATUS ──────────────────────────────────────────────

def get_market_status() -> dict:
    """Returns current trading session and market open/close status."""
    now_utc = datetime.utcnow()
    hour    = now_utc.hour
    weekday = now_utc.weekday()  # 0=Monday, 6=Sunday

    # Market is closed on weekends
    if weekday >= 5:
        session = 'WEEKEND — MARKET CLOSED'
        is_open = False
    elif 22 <= hour or hour < 8:
        session = 'SYDNEY/TOKYO SESSION'
        is_open = True
    elif 8 <= hour < 12:
        session = 'LONDON SESSION'
        is_open = True
    elif 12 <= hour < 17:
        session = 'LONDON/NEW YORK OVERLAP'
        is_open = True
    elif 17 <= hour < 22:
        session = 'NEW YORK SESSION'
        is_open = True
    else:
        session = 'TRANSITIONING'
        is_open = True

    return {
        'session':    session,
        'is_open':    is_open,
        'utc_time':   now_utc.strftime('%H:%M UTC'),
        'weekday':    now_utc.strftime('%A'),
    }


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Market Data Engine Test')
    print('='*55 + '\n')

    print('Starting price stream...')
    start_price_stream()
    time.sleep(5)

    print('\nLive Prices:')
    for inst in config.INSTRUMENTS:
        data = get_live_price(inst)
        if data:
            print(f'  {inst}: {data["price"]} ({data["source"]})')
        else:
            print(f'  {inst}: unavailable')

    print('\nMarket Status:')
    status = get_market_status()
    for k, v in status.items():
        print(f'  {k}: {v}')

    print('\nAPI Usage:')
    usage = get_api_usage()
    for k, v in usage.items():
        print(f'  {k}: {v}')

    print('\nFetching 1h candles for XAUUSD...')
    candles = get_candles('XAUUSD', '1h', 10)
    if candles:
        print(f'  Latest candle: {candles[0]}')

    print('\nFetching 10yr history for XAUUSD...')
    df = get_historical_data('XAUUSD', '10y', '1d')
    if df is not None:
        print(f'  Rows: {len(df)}')
        print(f'  From: {df.index[0].date()}')
        print(f'  To:   {df.index[-1].date()}')
        print(f'  Latest close: {df["Close"].iloc[-1]:.2f}')


# ── AUTO-REFRESH ROTATOR ───────────────────────────────────────
# Refreshes indicators for ONE pair at a time every 30 minutes
# Rotates: XAUUSD → GBPUSD → GBPJPY → XAUUSD → ...
# Uses only 4 API credits per cycle instead of 12

_refresh_index = 0

async def auto_refresh_loop():
    """
    Background async loop — refreshes one pair's indicators
    every 30 minutes in rotation.
    Attach this to the bot's event loop on startup.
    """
    global _refresh_index

    while True:
        try:
            await asyncio.sleep(config.TD_INDICATOR_INTERVAL_SEC)

            pairs = config.TD_AUTO_REFRESH_PAIRS
            instrument = pairs[_refresh_index % len(pairs)]
            _refresh_index += 1

            usage = get_api_usage()
            if usage['remaining'] < config.TD_LOW_BUDGET_THRESHOLD:
                logger.warning(
                    f'API budget low ({usage["remaining"]} left) — '
                    f'skipping auto-refresh for {instrument}'
                )
                continue

            logger.info(
                f'Auto-refresh: {instrument} '
                f'(budget: {usage["remaining"]} remaining)'
            )

            from data.indicators import (
                get_rsi, get_macd, get_bbands, get_atr
            )
            get_rsi(instrument)
            get_macd(instrument)
            get_bbands(instrument)
            get_atr(instrument)

            logger.info(
                f'Auto-refresh complete: {instrument} | '
                f'Budget used: {get_api_usage()["calls_today"]}'
            )

        except Exception as e:
            logger.error(f'Auto-refresh error: {e}')