# ════════════════════════════════════════════════════════════════
# OMNINEXUS — ingestion/fred_yield.py
# FRED Real Yield Microservice
# Pulls US 10Y Treasury Yield + CPI from FRED API
# Calculates Real Yield = Nominal Yield - Inflation Rate
# Gold signal #1 — Real Yield is the primary driver of XAUUSD
# ════════════════════════════════════════════════════════════════

import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.ingestion.fred_yield')

# ── FRED SERIES IDs ────────────────────────────────────────────
# These are the exact FRED database identifiers
SERIES_10Y_NOMINAL    = 'GS10'        # 10-Year Treasury Yield
SERIES_CPI            = 'CPIAUCSL'    # Consumer Price Index
SERIES_TIPS_10Y       = 'DFII10'      # 10Y TIPS (market real yield)
SERIES_BREAKEVEN_10Y  = 'T10YIE'      # 10Y Breakeven Inflation Rate

FRED_BASE_URL = 'https://api.stlouisfed.org/fred/series/observations'


# ── CORE FETCHER ───────────────────────────────────────────────
def fetch_fred_series(
    series_id: str,
    limit: int = 12
) -> Optional[list]:
    """
    Fetches the most recent observations for a FRED series.
    Returns a list of dicts with 'date' and 'value' keys.
    Returns None if the request fails.
    """
    params = {
        'series_id':        series_id,
        'api_key':          config.FRED_API_KEY,
        'file_type':        'json',
        'sort_order':       'desc',
        'limit':            limit,
        'observation_start': (
            datetime.now() - timedelta(days=365)
        ).strftime('%Y-%m-%d')
    }

    try:
        response = requests.get(
            FRED_BASE_URL,
            params=params,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        observations = []
        for obs in data.get('observations', []):
            # FRED returns '.' for missing values — skip those
            if obs['value'] != '.':
                observations.append({
                    'date':  obs['date'],
                    'value': float(obs['value'])
                })

        logger.info(
            f'FRED {series_id}: fetched '
            f'{len(observations)} observations'
        )
        return observations

    except requests.exceptions.RequestException as e:
        logger.error(f'FRED API request failed for {series_id}: {e}')
        return None
    except Exception as e:
        logger.error(f'FRED data processing error for {series_id}: {e}')
        return None


# ── LATEST VALUE EXTRACTOR ─────────────────────────────────────
def get_latest_value(series_id: str) -> Optional[float]:
    """
    Returns the single most recent non-null value
    for a given FRED series.
    """
    observations = fetch_fred_series(series_id, limit=5)
    if observations:
        return observations[0]['value']
    return None


# ── REAL YIELD CALCULATOR ──────────────────────────────────────
def calculate_real_yield() -> dict:
    """
    Calculates the US 10-Year Real Yield using two methods:

    Method 1 — TIPS Direct:
        Uses the DFII10 series (market-implied real yield)
        This is the most accurate and market-relevant measure.

    Method 2 — CPI Derived:
        Real Yield = Nominal 10Y - Breakeven Inflation Rate
        Used as backup if TIPS data is unavailable.

    Returns a dict with all yield components and
    the Gold bias signal derived from real yield level.
    """
    logger.info('Calculating Real Yield for XAUUSD signal...')

    # ── Fetch all components ───────────────────────────────────
    nominal_yield   = get_latest_value(SERIES_10Y_NOMINAL)
    tips_real_yield = get_latest_value(SERIES_TIPS_10Y)
    breakeven_rate  = get_latest_value(SERIES_BREAKEVEN_10Y)

    # ── Calculate Real Yield ───────────────────────────────────
    if tips_real_yield is not None:
        # Method 1: Direct TIPS real yield (preferred)
        real_yield = tips_real_yield
        method_used = 'TIPS_DIRECT'
    elif nominal_yield and breakeven_rate:
        # Method 2: Derived from nominal - breakeven
        real_yield = nominal_yield - breakeven_rate
        method_used = 'NOMINAL_MINUS_BREAKEVEN'
    else:
        logger.error('Insufficient data to calculate Real Yield')
        return {'error': 'Insufficient FRED data'}

    # ── Gold Bias Signal ───────────────────────────────────────
    # Real Yield below -0.5%  → STRONG BULLISH (gold accelerates)
    # Real Yield -0.5% to 0%  → BULLISH
    # Real Yield 0% to 0.5%   → NEUTRAL
    # Real Yield 0.5% to 1.5% → BEARISH
    # Real Yield above 1.5%   → STRONG BEARISH (gold suppressed)

    if real_yield < -0.5:
        gold_bias       = 'STRONG BULLISH'
        bias_score      = 90
        bias_color      = '🟢🟢'
    elif real_yield < 0.0:
        gold_bias       = 'BULLISH'
        bias_score      = 70
        bias_color      = '🟢'
    elif real_yield < 0.5:
        gold_bias       = 'NEUTRAL'
        bias_score      = 50
        bias_color      = '⚪'
    elif real_yield < 1.5:
        gold_bias       = 'BEARISH'
        bias_score      = 30
        bias_color      = '🔴'
    else:
        gold_bias       = 'STRONG BEARISH'
        bias_score      = 10
        bias_color      = '🔴🔴'

    # ── Breakout Detection ─────────────────────────────────────
    # Check if real yield is falling while gold consolidates
    # This is the primary breakout setup signal
    recent_tips = fetch_fred_series(SERIES_TIPS_10Y, limit=5)
    breakout_signal = False
    yield_delta = 0.0

    if recent_tips and len(recent_tips) >= 2:
        latest  = recent_tips[0]['value']
        prev    = recent_tips[1]['value']
        yield_delta = latest - prev

        # Yield falling = bullish for gold
        if yield_delta < -0.05:
            breakout_signal = True
            logger.info(
                f'BREAKOUT SIGNAL: Real yield falling '
                f'{yield_delta:.3f}% — Gold breakout possible'
            )

    # ── Build Result ───────────────────────────────────────────
    result = {
        'timestamp':        datetime.utcnow().isoformat(),
        'nominal_yield':    nominal_yield,
        'tips_real_yield':  tips_real_yield,
        'breakeven_rate':   breakeven_rate,
        'real_yield':       real_yield,
        'yield_delta':      yield_delta,
        'method':           method_used,
        'gold_bias':        gold_bias,
        'bias_score':       bias_score,
        'bias_color':       bias_color,
        'breakout_signal':  breakout_signal,
    }

    logger.info(
        f'Real Yield: {real_yield:.3f}% | '
        f'Gold Bias: {gold_bias} | '
        f'Breakout: {breakout_signal}'
    )

    return result


# ── TELEGRAM FORMATTER ─────────────────────────────────────────
def format_yield_alert(data: dict) -> str:
    """
    Formats Real Yield data into a Telegram message.
    Called by the signal monitor and /signals command.
    """
    if 'error' in data:
        return f'❌ Real Yield Error: {data["error"]}'

    breakout_line = ''
    if data.get('breakout_signal'):
        breakout_line = (
            f'\n⚡ <b>BREAKOUT SIGNAL ACTIVE</b>\n'
            f'Yield falling {data["yield_delta"]:.3f}% — '
            f'Gold breakout imminent'
        )

    return (
        f'📊 <b>REAL YIELD REPORT</b>\n'
        f'<code>{data["timestamp"][:19]} UTC</code>\n\n'
        f'Nominal 10Y Yield:  '
        f'<b>{data["nominal_yield"]:.3f}%</b>\n'
        f'Breakeven Rate:     '
        f'<b>{data["breakeven_rate"]:.3f}%</b>\n'
        f'Real Yield (TIPS):  '
        f'<b>{data["real_yield"]:.3f}%</b>\n'
        f'Yield Delta:        '
        f'<b>{data["yield_delta"]:+.3f}%</b>\n\n'
        f'Gold Bias:  {data["bias_color"]} '
        f'<b>{data["gold_bias"]}</b>\n'
        f'Bias Score: <b>{data["bias_score"]}/100</b>\n'
        f'Method:     <code>{data["method"]}</code>'
        f'{breakout_line}'
    )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — FRED Real Yield Engine Test')
    print('='*55 + '\n')

    data = calculate_real_yield()

    if 'error' not in data:
        print(f'Nominal Yield:    {data["nominal_yield"]:.3f}%')
        print(f'Breakeven Rate:   {data["breakeven_rate"]:.3f}%')
        print(f'Real Yield:       {data["real_yield"]:.3f}%')
        print(f'Yield Delta:      {data["yield_delta"]:+.3f}%')
        print(f'Gold Bias:        {data["gold_bias"]}')
        print(f'Bias Score:       {data["bias_score"]}/100')
        print(f'Breakout Signal:  {data["breakout_signal"]}')
        print(f'Method:           {data["method"]}')
        print('\nTelegram Format:')
        print('-'*40)
        # Strip HTML tags for clean terminal display
        import re
        clean = re.sub(r'<[^>]+>', '', format_yield_alert(data))
        print(clean)
    else:
        print(f'ERROR: {data["error"]}')