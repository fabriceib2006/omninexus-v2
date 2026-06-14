# ════════════════════════════════════════════════════════════════
# OMNINEXUS — data/indicators.py
# Technical Indicators Engine
# Fetches RSI, MACD, Bollinger Bands, ATR from Twelve Data REST
# Also calculates indicators locally from candle data
# Cached every 30 minutes to preserve API budget
# ════════════════════════════════════════════════════════════════

import logging
import requests
import time
from datetime import datetime
from typing import Optional
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.data.indicators')

TD_REST = 'https://api.twelvedata.com'

# ── INDICATOR CACHE ────────────────────────────────────────────
# Stores last fetched indicator values
# Refreshed every 30 minutes to save API budget
_indicator_cache: dict = {}
_cache_timestamps: dict = {}
CACHE_TTL = 1800  # 30 minutes


def _is_cache_fresh(key: str) -> bool:
    """Returns True if cached value is less than 30min old."""
    if key not in _cache_timestamps:
        return False
    return (time.time() - _cache_timestamps[key]) < CACHE_TTL


def _fetch_td_indicator(
    symbol:   str,
    function: str,
    params:   dict = None
) -> Optional[dict]:
    """
    Generic Twelve Data indicator fetcher.
    Returns the latest value dict or None on error.
    """
    try:
        request_params = {
            'symbol':   symbol,
            'interval': '1h',
            'apikey':   config.TWELVE_DATA_API_KEY,
        }
        if params:
            request_params.update(params)

        r = requests.get(
            f'{TD_REST}/{function}',
            params=request_params,
            timeout=15
        )
        data = r.json()

        if data.get('status') == 'error':
            logger.error(
                f'{function} error for {symbol}: '
                f'{data.get("message")}'
            )
            return None

        values = data.get('values', [])
        if values:
            return values[0]  # Most recent value
        return None

    except Exception as e:
        logger.error(f'{function} fetch error for {symbol}: {e}')
        return None


# ── RSI ────────────────────────────────────────────────────────

def get_rsi(
    instrument: str,
    period:     int = 14
) -> Optional[dict]:
    """
    Fetches RSI for an instrument.
    Returns dict with value and signal interpretation.
    Uses cache — only fetches fresh data every 30 minutes.
    """
    cache_key = f'rsi_{instrument}_{period}'
    if _is_cache_fresh(cache_key):
        return _indicator_cache.get(cache_key)

    symbol = config.TD_SYMBOLS.get(instrument)
    if not symbol:
        return None

    raw = _fetch_td_indicator(symbol, 'rsi', {'time_period': period})
    if not raw:
        return None

    rsi_val = float(raw.get('rsi', 50))

    if rsi_val >= config.RSI_OVERBOUGHT:
        signal    = 'SELL'
        strength  = min(100, int((rsi_val - 70) * 3.33))
        emoji     = '🔴'
    elif rsi_val <= config.RSI_OVERSOLD:
        signal    = 'BUY'
        strength  = min(100, int((30 - rsi_val) * 3.33))
        emoji     = '🟢'
    elif rsi_val > 55:
        signal    = 'BEARISH'
        strength  = int((rsi_val - 55) * 3)
        emoji     = '🟠'
    elif rsi_val < 45:
        signal    = 'BULLISH'
        strength  = int((45 - rsi_val) * 3)
        emoji     = '🟡'
    else:
        signal    = 'NEUTRAL'
        strength  = 0
        emoji     = '⚪'

    result = {
        'instrument': instrument,
        'value':      round(rsi_val, 2),
        'signal':     signal,
        'strength':   strength,
        'emoji':      emoji,
        'period':     period,
        'datetime':   raw.get('datetime'),
        'updated':    datetime.utcnow().isoformat(),
    }

    _indicator_cache[cache_key]    = result
    _cache_timestamps[cache_key]   = time.time()
    logger.info(
        f'RSI {instrument}: {rsi_val:.1f} — {signal}'
    )
    return result


# ── MACD ───────────────────────────────────────────────────────

def get_macd(instrument: str) -> Optional[dict]:
    """
    Fetches MACD for an instrument.
    Returns dict with macd, signal, histogram and direction.
    """
    cache_key = f'macd_{instrument}'
    if _is_cache_fresh(cache_key):
        return _indicator_cache.get(cache_key)

    symbol = config.TD_SYMBOLS.get(instrument)
    if not symbol:
        return None

    raw = _fetch_td_indicator(symbol, 'macd', {
        'fast_period':   12,
        'slow_period':   26,
        'signal_period': 9,
    })
    if not raw:
        return None

    macd_val  = float(raw.get('macd',       0))
    sig_val   = float(raw.get('macd_signal', 0))
    hist_val  = float(raw.get('macd_hist',   0))

    if hist_val > 0 and macd_val > sig_val:
        direction = 'BULLISH'
        emoji     = '🟢'
    elif hist_val < 0 and macd_val < sig_val:
        direction = 'BEARISH'
        emoji     = '🔴'
    elif hist_val > 0:
        direction = 'WEAKLY BULLISH'
        emoji     = '🟡'
    else:
        direction = 'WEAKLY BEARISH'
        emoji     = '🟠'

    # Detect crossover
    crossover = None
    if macd_val > sig_val and hist_val > 0 and hist_val < 0.0001:
        crossover = 'BULLISH CROSSOVER'
    elif macd_val < sig_val and hist_val < 0 and hist_val > -0.0001:
        crossover = 'BEARISH CROSSOVER'

    result = {
        'instrument': instrument,
        'macd':       round(macd_val,  5),
        'signal':     round(sig_val,   5),
        'histogram':  round(hist_val,  5),
        'direction':  direction,
        'crossover':  crossover,
        'emoji':      emoji,
        'datetime':   raw.get('datetime'),
        'updated':    datetime.utcnow().isoformat(),
    }

    _indicator_cache[cache_key]  = result
    _cache_timestamps[cache_key] = time.time()
    logger.info(
        f'MACD {instrument}: {direction} | '
        f'hist={hist_val:.5f}'
    )
    return result


# ── BOLLINGER BANDS ────────────────────────────────────────────

def get_bbands(
    instrument: str,
    period:     int = 20
) -> Optional[dict]:
    """
    Fetches Bollinger Bands for an instrument.
    Returns upper, middle, lower bands + position signal.
    """
    cache_key = f'bbands_{instrument}'
    if _is_cache_fresh(cache_key):
        return _indicator_cache.get(cache_key)

    symbol = config.TD_SYMBOLS.get(instrument)
    if not symbol:
        return None

    raw = _fetch_td_indicator(symbol, 'bbands', {
        'time_period': period,
        'sd':          2,
    })
    if not raw:
        return None

    upper  = float(raw.get('upper_band',  0))
    middle = float(raw.get('middle_band', 0))
    lower  = float(raw.get('lower_band',  0))

    # Get current price to determine position
    from data.market_data import get_live_price
    price_data = get_live_price(instrument)
    current    = price_data['price'] if price_data else middle

    band_width = upper - lower
    pct_b      = (
        (current - lower) / band_width
        if band_width > 0 else 0.5
    )

    if current >= upper:
        position = 'ABOVE UPPER — OVERBOUGHT'
        signal   = 'SELL'
        emoji    = '🔴'
    elif current <= lower:
        position = 'BELOW LOWER — OVERSOLD'
        signal   = 'BUY'
        emoji    = '🟢'
    elif pct_b > 0.8:
        position = 'NEAR UPPER BAND'
        signal   = 'BEARISH'
        emoji    = '🟠'
    elif pct_b < 0.2:
        position = 'NEAR LOWER BAND'
        signal   = 'BULLISH'
        emoji    = '🟡'
    else:
        position = 'INSIDE BANDS'
        signal   = 'NEUTRAL'
        emoji    = '⚪'

    result = {
        'instrument': instrument,
        'upper':      round(upper,   5),
        'middle':     round(middle,  5),
        'lower':      round(lower,   5),
        'current':    round(current, 5),
        'pct_b':      round(pct_b,   3),
        'band_width': round(band_width, 5),
        'position':   position,
        'signal':     signal,
        'emoji':      emoji,
        'datetime':   raw.get('datetime'),
        'updated':    datetime.utcnow().isoformat(),
    }

    _indicator_cache[cache_key]  = result
    _cache_timestamps[cache_key] = time.time()
    logger.info(
        f'BBands {instrument}: {position}'
    )
    return result


# ── ATR ────────────────────────────────────────────────────────

def get_atr(
    instrument: str,
    period:     int = 14
) -> Optional[dict]:
    """
    Fetches Average True Range.
    Used to set dynamic SL/TP based on actual volatility.
    """
    cache_key = f'atr_{instrument}'
    if _is_cache_fresh(cache_key):
        return _indicator_cache.get(cache_key)

    symbol = config.TD_SYMBOLS.get(instrument)
    if not symbol:
        return None

    raw = _fetch_td_indicator(symbol, 'atr', {
        'time_period': period
    })
    if not raw:
        return None

    atr_val = float(raw.get('atr', 0))

    # Suggest SL/TP distances based on ATR
    sl_distance = round(atr_val * 1.5, 5)
    tp_distance = round(atr_val * 3.0, 5)

    result = {
        'instrument':  instrument,
        'atr':         round(atr_val,  5),
        'sl_distance': sl_distance,
        'tp_distance': tp_distance,
        'period':      period,
        'datetime':    raw.get('datetime'),
        'updated':     datetime.utcnow().isoformat(),
    }

    _indicator_cache[cache_key]  = result
    _cache_timestamps[cache_key] = time.time()
    logger.info(
        f'ATR {instrument}: {atr_val:.5f} | '
        f'SL dist: {sl_distance}'
    )
    return result


# ── EMA ────────────────────────────────────────────────────────

def get_ema(
    instrument: str,
    period:     int = 200
) -> Optional[dict]:
    """
    Fetches Exponential Moving Average.
    EMA200 = major trend direction filter.
    Price above EMA200 = uptrend = prefer buys only.
    """
    cache_key = f'ema_{instrument}_{period}'
    if _is_cache_fresh(cache_key):
        return _indicator_cache.get(cache_key)

    symbol = config.TD_SYMBOLS.get(instrument)
    if not symbol:
        return None

    raw = _fetch_td_indicator(symbol, 'ema', {
        'time_period': period
    })
    if not raw:
        return None

    ema_val = float(raw.get('ema', 0))

    from data.market_data import get_live_price
    price_data = get_live_price(instrument)
    current    = price_data['price'] if price_data else ema_val

    if current > ema_val:
        trend = 'UPTREND'
        signal = 'BUY BIAS'
        emoji  = '🟢'
    else:
        trend  = 'DOWNTREND'
        signal = 'SELL BIAS'
        emoji  = '🔴'

    result = {
        'instrument': instrument,
        'ema':        round(ema_val, 5),
        'current':    round(current, 5),
        'trend':      trend,
        'signal':     signal,
        'emoji':      emoji,
        'period':     period,
        'datetime':   raw.get('datetime'),
        'updated':    datetime.utcnow().isoformat(),
    }

    _indicator_cache[cache_key]  = result
    _cache_timestamps[cache_key] = time.time()
    logger.info(
        f'EMA{period} {instrument}: {ema_val:.5f} — {trend}'
    )
    return result


# ── ALL INDICATORS FOR ONE INSTRUMENT ─────────────────────────

def get_all_indicators(instrument: str) -> dict:
    """
    Fetches all indicators for one instrument.
    Uses 4 REST calls (RSI + MACD + BBands + ATR).
    Returns unified dict with all values.
    """
    logger.info(f'Fetching all indicators for {instrument}...')
    return {
        'rsi':    get_rsi(instrument),
        'macd':   get_macd(instrument),
        'bbands': get_bbands(instrument),
        'atr':    get_atr(instrument),
        'ema200': get_ema(instrument, 200),
        'ema50':  get_ema(instrument, 50),
    }


def clear_cache(instrument: str = None):
    """Clears indicator cache for one or all instruments."""
    global _indicator_cache, _cache_timestamps
    if instrument:
        keys_to_del = [
            k for k in _indicator_cache
            if instrument in k
        ]
        for k in keys_to_del:
            del _indicator_cache[k]
            del _cache_timestamps[k]
    else:
        _indicator_cache  = {}
        _cache_timestamps = {}
    logger.info(
        f'Cache cleared: {instrument or "all instruments"}'
    )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Indicators Engine Test')
    print('='*55 + '\n')

    for inst in ['XAUUSD', 'GBPUSD', 'GBPJPY']:
        print(f'\n{inst}:')
        rsi = get_rsi(inst)
        if rsi:
            print(
                f'  RSI:    {rsi["value"]} — '
                f'{rsi["signal"]} {rsi["emoji"]}'
            )
        macd = get_macd(inst)
        if macd:
            print(
                f'  MACD:   {macd["direction"]} '
                f'{macd["emoji"]}'
            )
        bb = get_bbands(inst)
        if bb:
            print(
                f'  BBands: {bb["position"]} '
                f'{bb["emoji"]}'
            )
        atr = get_atr(inst)
        if atr:
            print(
                f'  ATR:    {atr["atr"]:.5f} | '
                f'SL: {atr["sl_distance"]}'
            )