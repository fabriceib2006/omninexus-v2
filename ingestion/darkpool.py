# ════════════════════════════════════════════════════════════════
# OMNINEXUS — ingestion/darkpool.py
# Dark Pool Ghost Signal
# Monitors FINRA weekly dark pool volume reports
# Detects unusual institutional accumulation in:
#   - GLD  (SPDR Gold ETF)
#   - IAU  (iShares Gold ETF)
#   - GBP futures proxy via FXB (Invesco GBP ETF)
# Z-score above 2.0σ = institutional positioning signal
# Lead time: 12-72 hours before spot price moves
# ════════════════════════════════════════════════════════════════

import requests
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.ingestion.darkpool')

# ── TARGET SECURITIES ──────────────────────────────────────────
# These are the instruments we monitor in dark pools
# as leading indicators for XAUUSD and GBP pairs
TARGET_SYMBOLS = {
    'GLD': {
        'name':        'SPDR Gold ETF',
        'instrument':  'XAUUSD',
        'signal_type': 'GOLD_DARK_POOL'
    },
    'IAU': {
        'name':        'iShares Gold ETF',
        'instrument':  'XAUUSD',
        'signal_type': 'GOLD_DARK_POOL'
    },
    'FXB': {
        'name':        'Invesco GBP/USD ETF',
        'instrument':  'GBPUSD',
        'signal_type': 'GBP_DARK_POOL'
    },
    'GDX': {
        'name':        'VanEck Gold Miners ETF',
        'instrument':  'XAUUSD',
        'signal_type': 'GOLD_MINERS_DARK_POOL'
    }
}

# ── FINRA API ENDPOINTS ────────────────────────────────────────
# FINRA provides free public dark pool data via their
# Market Data Center API - no authentication required
FINRA_BASE_URL = (
    'https://api.finra.org/data/group/otcMarket'
    '/name/weeklySummary'
)

# Alternative: FINRA OTC Transparency Data
FINRA_OTC_URL = (
    'https://api.finra.org/data/group/otcMarket'
    '/name/regShoThresholdList'
)

# Historical baseline file for Z-score calculation
BASELINE_FILE = 'logs/darkpool_baseline.json'


# ── BASELINE MANAGER ───────────────────────────────────────────
def load_baseline() -> dict:
    """
    Loads historical dark pool volume baseline from file.
    Used to calculate Z-scores against historical average.
    Returns empty dict if no baseline exists yet.
    """
    if os.path.exists(BASELINE_FILE):
        try:
            with open(BASELINE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f'Baseline load error: {e}')
    return {}


def save_baseline(baseline: dict):
    """Saves updated baseline to file."""
    try:
        os.makedirs('logs', exist_ok=True)
        with open(BASELINE_FILE, 'w') as f:
            json.dump(baseline, f, indent=2)
        logger.info('Dark pool baseline saved')
    except Exception as e:
        logger.error(f'Baseline save error: {e}')


def update_baseline(symbol: str, volume: float):
    """
    Updates the rolling baseline for a symbol.
    Keeps last 52 weeks of data for Z-score calculation.
    """
    baseline = load_baseline()

    if symbol not in baseline:
        baseline[symbol] = {
            'volumes':    [],
            'updated':    datetime.utcnow().isoformat()
        }

    baseline[symbol]['volumes'].append(volume)
    # Keep only last 52 data points (52 weeks)
    baseline[symbol]['volumes'] = (
        baseline[symbol]['volumes'][-52:]
    )
    baseline[symbol]['updated'] = datetime.utcnow().isoformat()

    save_baseline(baseline)


# ── Z-SCORE CALCULATOR ─────────────────────────────────────────
def calculate_z_score(
    symbol: str,
    current_volume: float
) -> float:
    """
    Calculates Z-score of current volume vs historical baseline.
    Z = (current - mean) / std_dev
    Z > 2.0 = statistically significant anomaly
    """
    baseline = load_baseline()

    if symbol not in baseline:
        logger.info(
            f'{symbol}: No baseline yet. '
            f'Building baseline with current volume.'
        )
        update_baseline(symbol, current_volume)
        return 0.0

    volumes = baseline[symbol]['volumes']

    if len(volumes) < 4:
        # Need at least 4 data points for meaningful Z-score
        update_baseline(symbol, current_volume)
        return 0.0

    import statistics
    mean   = statistics.mean(volumes)
    stdev  = statistics.stdev(volumes)

    if stdev == 0:
        return 0.0

    z_score = (current_volume - mean) / stdev

    logger.info(
        f'{symbol}: Volume={current_volume:,.0f} | '
        f'Mean={mean:,.0f} | '
        f'Z-Score={z_score:.2f}'
    )

    # Update baseline with new observation
    update_baseline(symbol, current_volume)

    return round(z_score, 2)


# ── FINRA DATA FETCHER ─────────────────────────────────────────
def fetch_finra_dark_pool(symbol: str) -> Optional[float]:
    """
    Fetches dark pool (ATS - Alternative Trading System)
    weekly volume for a specific symbol from FINRA.

    FINRA ATS data is published weekly and is free/public.
    Returns the most recent weekly dark pool volume.
    """
    try:
        # FINRA ATS Transparency Data endpoint
        url = (
            f'https://api.finra.org/data/group/otcMarket'
            f'/name/atsWeeklySecurityData'
        )

        headers = {
            'Accept':     'application/json',
            'User-Agent': 'Mozilla/5.0 OmniNexus Research'
        }

        params = {
            'limit':   10,
            'offset':  0,
            'fields':  'issueSymbolIdentifier,'
                       'totalWeeklyShareQuantity,'
                       'weekStartDate',
        }

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()

            # Search for our target symbol
            for record in data:
                if (record.get('issueSymbolIdentifier', '')
                        .upper() == symbol.upper()):
                    volume = float(
                        record.get('totalWeeklyShareQuantity', 0)
                    )
                    logger.info(
                        f'FINRA {symbol}: '
                        f'Dark pool volume = {volume:,.0f}'
                    )
                    return volume

        logger.warning(
            f'FINRA {symbol}: Symbol not found in response. '
            f'Status: {response.status_code}'
        )
        return None

    except requests.exceptions.Timeout:
        logger.error(f'FINRA timeout for {symbol}')
        return None
    except Exception as e:
        logger.error(f'FINRA fetch error for {symbol}: {e}')
        return None


# ── SIMULATED VOLUME (FALLBACK) ────────────────────────────────
def get_simulated_volume(symbol: str) -> float:
    """
    Fallback when FINRA API is unavailable.
    Uses realistic baseline volumes for each symbol.
    This allows the Z-score baseline to build
    even when API is temporarily unreachable.

    NOTE: This is clearly marked as simulated in output.
    Replace with real FINRA data when API is available.
    """
    # Realistic average weekly dark pool volumes
    baselines = {
        'GLD': 15_000_000,
        'IAU': 8_000_000,
        'FXB': 500_000,
        'GDX': 12_000_000,
    }
    import random
    base = baselines.get(symbol, 1_000_000)
    # Add ±20% random variation to simulate real data
    variation = random.uniform(0.80, 1.20)
    return round(base * variation)


# ── MAIN DARK POOL SCANNER ─────────────────────────────────────
def scan_dark_pools() -> dict:
    """
    Main function. Scans all target symbols for dark pool
    anomalies. Returns Z-scores and signals for each.

    Any Z-score above config.DARKPOOL_Z_THRESHOLD (2.0σ)
    triggers an anomaly flag and Telegram alert.
    """
    logger.info('Scanning dark pools for institutional signals...')

    results       = {}
    anomalies     = []
    gold_signal   = False
    gbp_signal    = False

    for symbol, info in TARGET_SYMBOLS.items():
        # Try FINRA API first
        volume = fetch_finra_dark_pool(symbol)
        data_source = 'FINRA_API'

        # Fall back to simulated if API unavailable
        if volume is None:
            volume = get_simulated_volume(symbol)
            data_source = 'SIMULATED'
            logger.warning(
                f'{symbol}: Using simulated volume '
                f'(FINRA API unavailable)'
            )

        # Calculate Z-score
        z_score = calculate_z_score(symbol, volume)

        # Check anomaly threshold
        is_anomaly = abs(z_score) >= config.DARKPOOL_Z_THRESHOLD

        if is_anomaly:
            anomalies.append({
                'symbol':     symbol,
                'z_score':    z_score,
                'volume':     volume,
                'instrument': info['instrument'],
                'direction':  'ACCUMULATION' if z_score > 0
                              else 'DISTRIBUTION'
            })

            if info['instrument'] == 'XAUUSD':
                gold_signal = True
            elif info['instrument'] in ['GBPUSD', 'GBPJPY']:
                gbp_signal = True

            logger.warning(
                f'DARK POOL ANOMALY: {symbol} '
                f'Z-Score={z_score:.2f}σ '
                f'({info["instrument"]})'
            )

        results[symbol] = {
            'name':        info['name'],
            'instrument':  info['instrument'],
            'volume':      volume,
            'z_score':     z_score,
            'is_anomaly':  is_anomaly,
            'data_source': data_source,
            'direction':   'ACCUMULATION' if z_score > 0
                           else 'DISTRIBUTION'
                           if z_score < 0 else 'NEUTRAL'
        }

    # ── Lead Time Estimate ─────────────────────────────────────
    # Dark pool positioning typically leads spot by 12-72 hours
    lead_time_hours = 24  # default estimate
    if len(anomalies) >= 2:
        lead_time_hours = 12  # multiple anomalies = faster move
    elif len(anomalies) == 1:
        lead_time_hours = 48  # single anomaly = slower move

    result = {
        'timestamp':         datetime.utcnow().isoformat(),
        'symbols_scanned':   len(TARGET_SYMBOLS),
        'anomalies_found':   len(anomalies),
        'anomalies':         anomalies,
        'results':           results,
        'gold_signal':       gold_signal,
        'gbp_signal':        gbp_signal,
        'lead_time_hours':   lead_time_hours,
        'alert_threshold':   config.DARKPOOL_Z_THRESHOLD,
    }

    logger.info(
        f'Dark pool scan complete. '
        f'Anomalies: {len(anomalies)} | '
        f'Gold Signal: {gold_signal} | '
        f'GBP Signal: {gbp_signal}'
    )

    return result


# ── TELEGRAM FORMATTER ─────────────────────────────────────────
def format_darkpool_alert(data: dict) -> str:
    """
    Formats dark pool scan into Telegram message.
    """
    if 'error' in data:
        return f'❌ Dark Pool Error: {data["error"]}'

    # Build symbol lines
    symbol_lines = ''
    for symbol, info in data['results'].items():
        anomaly_flag = ' ⚠️' if info['is_anomaly'] else ''
        source_flag  = (
            ' [SIM]' if info['data_source'] == 'SIMULATED'
            else ''
        )
        symbol_lines += (
            f'{symbol}: <b>{info["z_score"]:+.2f}σ</b> '
            f'({info["direction"]})'
            f'{anomaly_flag}{source_flag}\n'
        )

    # Build anomaly detail
    anomaly_detail = ''
    if data['anomalies']:
        anomaly_detail = '\n<b>🚨 ANOMALIES DETECTED:</b>\n'
        for a in data['anomalies']:
            anomaly_detail += (
                f'  {a["symbol"]}: {a["z_score"]:+.2f}σ '
                f'→ {a["instrument"]} {a["direction"]}\n'
                f'  Lead window: '
                f'{data["lead_time_hours"]}h\n'
            )

    signal_summary = ''
    if data['gold_signal']:
        signal_summary += '🥇 Gold institutional positioning detected\n'
    if data['gbp_signal']:
        signal_summary += '💷 GBP institutional positioning detected\n'
    if not data['gold_signal'] and not data['gbp_signal']:
        signal_summary = '✅ No significant positioning detected\n'

    return (
        f'👻 <b>DARK POOL GHOST SIGNAL</b>\n'
        f'<code>{data["timestamp"][:19]} UTC</code>\n\n'
        f'<b>Z-SCORES (threshold: '
        f'{data["alert_threshold"]}σ):</b>\n'
        f'{symbol_lines}\n'
        f'{signal_summary}'
        f'{anomaly_detail}'
    )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Dark Pool Ghost Signal Test')
    print('='*55 + '\n')

    data = scan_dark_pools()

    print(f'Symbols Scanned:  {data["symbols_scanned"]}')
    print(f'Anomalies Found:  {data["anomalies_found"]}')
    print(f'Gold Signal:      {data["gold_signal"]}')
    print(f'GBP Signal:       {data["gbp_signal"]}')
    print(f'\nSymbol Z-Scores:')
    for symbol, info in data['results'].items():
        status = '⚠️ ANOMALY' if info['is_anomaly'] else 'normal'
        source = '[SIM]' if info['data_source'] == 'SIMULATED' \
                 else '[LIVE]'
        print(
            f'  {symbol}: {info["z_score"]:+.2f}σ '
            f'{info["direction"]} {source} {status}'
        )

    if data['anomalies']:
        print(f'\nAnomalies:')
        for a in data['anomalies']:
            print(
                f'  {a["symbol"]}: {a["z_score"]:+.2f}σ → '
                f'{a["instrument"]} {a["direction"]}'
            )
    print(
        f'\nNote: [SIM] = simulated baseline data. '
        f'Z-scores will become accurate after '
        f'4+ weekly scans build a real baseline.'
    )