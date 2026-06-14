# ════════════════════════════════════════════════════════════════
# OMNINEXUS — data/history.py
# Historical Data Manager
# Downloads 10 years of OHLCV data using yfinance (free)
# Saves as raw JSON files in data/history/ folder
# Brain layer reads these JSON files for pattern training
# NO Alpha Vantage — 100% yfinance + local JSON storage
# ════════════════════════════════════════════════════════════════

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.data.history')

# ── HISTORY STORAGE ────────────────────────────────────────────
# All history saved as JSON in this folder
# Each file = one instrument + timeframe
# Example: data/history/XAUUSD_1d.json
HISTORY_DIR = Path(
    os.path.dirname(os.path.abspath(__file__))
) / 'history'

HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _history_file(instrument: str, interval: str) -> Path:
    """Returns path to history JSON file."""
    return HISTORY_DIR / f'{instrument}_{interval}.json'


def _save_history(
    instrument: str,
    interval:   str,
    data:       list,
    meta:       dict = None
):
    """Saves history data as JSON file."""
    filepath = _history_file(instrument, interval)
    payload  = {
        'instrument':   instrument,
        'interval':     interval,
        'bars':         len(data),
        'from':         data[-1]['datetime'] if data else None,
        'to':           data[0]['datetime']  if data else None,
        'downloaded_at': datetime.utcnow().isoformat(),
        'source':       'yfinance',
        'meta':         meta or {},
        'data':         data,
    }
    with open(filepath, 'w') as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info(
        f'History saved: {filepath.name} — {len(data)} bars'
    )


def _load_history(
    instrument: str,
    interval:   str
) -> Optional[dict]:
    """Loads history from JSON file if it exists."""
    filepath = _history_file(instrument, interval)
    if not filepath.exists():
        return None
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f'History load error: {e}')
        return None


def _is_history_fresh(
    instrument: str,
    interval:   str,
    max_age_hours: int = 24
) -> bool:
    """
    Returns True if history file exists and is recent.
    Daily data: re-download once per day.
    Hourly data: re-download every 6 hours.
    """
    filepath = _history_file(instrument, interval)
    if not filepath.exists():
        return False

    modified  = datetime.fromtimestamp(filepath.stat().st_mtime)
    age_hours = (datetime.now() - modified).total_seconds() / 3600
    return age_hours < max_age_hours


# ── DOWNLOADER ─────────────────────────────────────────────────

def download_history(
    instrument:   str,
    interval:     str  = '1d',
    period:       str  = '10y',
    force_reload: bool = False,
) -> Optional[list]:
    """
    Downloads historical OHLCV data using yfinance.
    Saves to JSON file in data/history/ folder.
    Returns list of candle dicts.

    Intervals: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 1wk, 1mo
    Periods:   1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max

    NOTE: yfinance intraday data (< 1d) only goes back 60 days max.
    For 10yr history, use interval='1d' or '1wk'.
    """
    # Check if fresh data already exists
    max_age = 6 if interval in ['1h', '4h'] else 24
    if not force_reload and _is_history_fresh(
        instrument, interval, max_age
    ):
        logger.info(
            f'History fresh: {instrument} {interval} — '
            f'using cached file'
        )
        cached = _load_history(instrument, interval)
        return cached['data'] if cached else None

    try:
        import yfinance as yf

        yf_symbol = config.YF_SYMBOLS.get(instrument)
        if not yf_symbol:
            logger.error(
                f'No yfinance symbol for {instrument}'
            )
            return None

        logger.info(
            f'Downloading {period} of {interval} data '
            f'for {instrument} ({yf_symbol})...'
        )

        # yfinance interval mapping
        yf_interval_map = {
            '1m':  '1m',  '5m':  '5m',
            '15m': '15m', '30m': '30m',
            '1h':  '1h',  '4h':  '1h',
            '1d':  '1d',  '1wk': '1wk',
            '1mo': '1mo',
        }
        yf_interval = yf_interval_map.get(interval, '1d')

        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=yf_interval)

        if df.empty:
            logger.warning(
                f'No data returned for {instrument} '
                f'{interval} {period}'
            )
            return None

        # Convert DataFrame to list of dicts
        candles = []
        for idx, row in df.iterrows():
            candles.append({
                'datetime': idx.isoformat(),
                'open':     round(float(row['Open']),   5),
                'high':     round(float(row['High']),   5),
                'low':      round(float(row['Low']),    5),
                'close':    round(float(row['Close']),  5),
                'volume':   round(float(row.get('Volume', 0)), 2),
            })

        # Reverse so newest is first
        candles.reverse()

        # Save metadata
        meta = {
            'yf_symbol':    yf_symbol,
            'period':       period,
            'yf_interval':  yf_interval,
            'total_bars':   len(candles),
            'date_from':    candles[-1]['datetime'],
            'date_to':      candles[0]['datetime'],
        }

        _save_history(instrument, interval, candles, meta)

        logger.info(
            f'Downloaded: {instrument} {interval} — '
            f'{len(candles)} bars | '
            f'{candles[-1]["datetime"][:10]} to '
            f'{candles[0]["datetime"][:10]}'
        )
        return candles

    except Exception as e:
        logger.error(
            f'Download error for {instrument} {interval}: {e}'
        )
        return None


# ── BULK DOWNLOADER ────────────────────────────────────────────

def download_all_history(force_reload: bool = False) -> dict:
    """
    Downloads history for all instruments and key timeframes.
    Called once at system startup and then daily.

    Timeframes downloaded:
    - 1d: 10 years — for long-term pattern training
    - 1h: 2 years  — for medium-term signal training
    - 1wk: max     — for macro regime analysis

    All saved as JSON in data/history/ folder.
    """
    logger.info('Starting bulk history download...')
    results = {}

    download_plan = [
        ('XAUUSD', '1d',  '10y'),
        ('XAUUSD', '1h',  '2y'),
        ('XAUUSD', '1wk', 'max'),
        ('GBPUSD', '1d',  '10y'),
        ('GBPUSD', '1h',  '2y'),
        ('GBPUSD', '1wk', 'max'),
        ('GBPJPY', '1d',  '10y'),
        ('GBPJPY', '1h',  '2y'),
        ('GBPJPY', '1wk', 'max'),
    ]

    for instrument, interval, period in download_plan:
        key = f'{instrument}_{interval}'
        try:
            candles = download_history(
                instrument   = instrument,
                interval     = interval,
                period       = period,
                force_reload = force_reload,
            )
            if candles:
                results[key] = {
                    'success': True,
                    'bars':    len(candles),
                    'from':    candles[-1]['datetime'][:10],
                    'to':      candles[0]['datetime'][:10],
                }
                logger.info(f'OK: {key} — {len(candles)} bars')
            else:
                results[key] = {'success': False, 'bars': 0}
                logger.warning(f'FAILED: {key}')

            # Small delay to avoid rate limits
            time.sleep(1)

        except Exception as e:
            results[key] = {'success': False, 'error': str(e)}
            logger.error(f'Error: {key} — {e}')

    success_count = sum(
        1 for v in results.values() if v.get('success')
    )
    logger.info(
        f'Bulk download complete: '
        f'{success_count}/{len(download_plan)} successful'
    )
    return results


# ── HISTORY READER ─────────────────────────────────────────────

def get_history(
    instrument: str,
    interval:   str = '1d',
    limit:      int = None,
) -> Optional[list]:
    """
    Returns historical data from JSON file.
    Downloads fresh data if file doesn't exist.
    limit: number of most recent candles to return (None = all)
    """
    data = _load_history(instrument, interval)

    if not data:
        logger.info(
            f'No history file for {instrument} {interval} — '
            f'downloading...'
        )
        candles = download_history(instrument, interval)
        if not candles:
            return None
        return candles[:limit] if limit else candles

    candles = data.get('data', [])
    return candles[:limit] if limit else candles


def get_history_stats() -> dict:
    """Returns summary of all saved history files."""
    stats = {}
    for filepath in HISTORY_DIR.glob('*.json'):
        try:
            with open(filepath, 'r') as f:
                meta = json.load(f)
            name = filepath.stem
            stats[name] = {
                'bars':          meta.get('bars', 0),
                'from':          meta.get('from', '?')[:10],
                'to':            meta.get('to',   '?')[:10],
                'downloaded_at': meta.get(
                    'downloaded_at', '?'
                )[:19],
                'size_kb':       round(
                    filepath.stat().st_size / 1024, 1
                ),
            }
        except Exception:
            stats[filepath.stem] = {'error': 'corrupted file'}
    return stats


def get_closes(
    instrument: str,
    interval:   str = '1d',
    limit:      int = 100,
) -> Optional[list]:
    """
    Returns just the closing prices as a flat list.
    Useful for indicator calculations.
    Most recent price is first.
    """
    candles = get_history(instrument, interval, limit)
    if not candles:
        return None
    return [c['close'] for c in candles]


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — History Manager Test')
    print('='*55 + '\n')

    print('Downloading all history...')
    print('(This may take 1-2 minutes on first run)\n')

    results = download_all_history(force_reload=False)

    print('\nDownload Results:')
    for key, result in results.items():
        status = 'OK' if result.get('success') else 'FAIL'
        bars   = result.get('bars', 0)
        frm    = result.get('from', '?')
        to     = result.get('to',   '?')
        print(f'  [{status}] {key}: {bars} bars | {frm} → {to}')

    print('\nHistory Files:')
    stats = get_history_stats()
    for name, info in stats.items():
        print(
            f'  {name}: {info.get("bars")} bars | '
            f'{info.get("from")} → {info.get("to")} | '
            f'{info.get("size_kb")} KB'
        )

    print('\nSample closes (XAUUSD 1d, last 5):')
    closes = get_closes('XAUUSD', '1d', 5)
    if closes:
        print(f'  {closes}')