# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/correlation_guard.py
# Cross-Pair Correlation Filter
# Prevents doubling risk when XAUUSD and GBPUSD move together
# Uses 20-day rolling correlation from historical JSON data
# If correlation > 0.7 between two active signals:
#   → Only allows the higher-confidence signal
# ════════════════════════════════════════════════════════════════

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.correlation_guard')

HISTORY_DIR = Path(
    os.path.dirname(os.path.abspath(__file__))
).parent / 'data' / 'history'

CORRELATION_THRESHOLD = 0.70  # block if correlation > this


def _get_closes(instrument: str, n: int = 20) -> list:
    """Returns last n daily closes from history JSON."""
    filepath = HISTORY_DIR / f'{instrument}_1d.json'
    if not filepath.exists():
        return []
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        candles = data.get('data', [])
        # Data is newest first
        return [c['close'] for c in candles[:n]]
    except Exception as e:
        logger.warning(f'Closes load error {instrument}: {e}')
        return []


def _pearson_correlation(x: list, y: list) -> float:
    """Calculates Pearson correlation between two price series."""
    n = min(len(x), len(y))
    if n < 5:
        return 0.0

    x = x[:n]
    y = y[:n]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    num   = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den_x = sum((x[i] - mean_x) ** 2 for i in range(n)) ** 0.5
    den_y = sum((y[i] - mean_y) ** 2 for i in range(n)) ** 0.5

    if den_x == 0 or den_y == 0:
        return 0.0

    return num / (den_x * den_y)


def get_correlation_matrix(n_days: int = 20) -> dict:
    """
    Returns rolling correlation matrix for all instrument pairs.
    Uses last n_days of daily closes.
    """
    matrix = {}
    instruments = config.INSTRUMENTS
    closes = {
        inst: _get_closes(inst, n_days)
        for inst in instruments
    }

    for i, inst_a in enumerate(instruments):
        for j, inst_b in enumerate(instruments):
            if j <= i:
                continue
            key = f'{inst_a}_{inst_b}'
            corr = _pearson_correlation(
                closes.get(inst_a, []),
                closes.get(inst_b, []),
            )
            matrix[key] = round(corr, 3)
            logger.debug(f'Correlation {key}: {corr:.3f}')

    return matrix


def filter_correlated_signals(signals: dict) -> dict:
    """
    Main function. Takes dict of signals keyed by instrument.
    Removes correlated lower-confidence signals.

    signals = {
        'XAUUSD': {'direction': 'BUY', 'confidence': 72, ...},
        'GBPUSD': {'direction': 'BUY', 'confidence': 65, ...},
        'GBPJPY': {'direction': 'SELL', 'confidence': 58, ...},
    }

    Returns filtered signals dict with blocked ones removed
    and a 'blocked_reason' added to blocked signals.
    """
    if not signals:
        return signals

    # Only check signals that are tradeable
    active = {
        k: v for k, v in signals.items()
        if v.get('direction') not in ['HOLD', None]
    }

    if len(active) < 2:
        return signals

    matrix   = get_correlation_matrix()
    blocked  = set()
    reasons  = {}

    instruments = list(active.keys())

    for i in range(len(instruments)):
        for j in range(i + 1, len(instruments)):
            inst_a = instruments[i]
            inst_b = instruments[j]

            if inst_a in blocked or inst_b in blocked:
                continue

            key  = f'{inst_a}_{inst_b}'
            rkey = f'{inst_b}_{inst_a}'
            corr = matrix.get(key, matrix.get(rkey, 0.0))

            sig_a = active[inst_a]
            sig_b = active[inst_b]

            # Only block if both signals are in same direction
            same_direction = (
                sig_a.get('direction', '') ==
                sig_b.get('direction', '')
            )

            if abs(corr) >= CORRELATION_THRESHOLD and same_direction:
                conf_a = sig_a.get('confidence', 0)
                conf_b = sig_b.get('confidence', 0)

                if conf_a >= conf_b:
                    blocked.add(inst_b)
                    reasons[inst_b] = (
                        f'Blocked: corr with {inst_a}='
                        f'{corr:.2f} (>{CORRELATION_THRESHOLD}) | '
                        f'Keeping higher confidence {inst_a} '
                        f'({conf_a:.0f}% vs {conf_b:.0f}%)'
                    )
                    logger.info(
                        f'Correlation guard: blocking {inst_b} '
                        f'(corr={corr:.2f} with {inst_a})'
                    )
                else:
                    blocked.add(inst_a)
                    reasons[inst_a] = (
                        f'Blocked: corr with {inst_b}='
                        f'{corr:.2f} (>{CORRELATION_THRESHOLD}) | '
                        f'Keeping higher confidence {inst_b} '
                        f'({conf_b:.0f}% vs {conf_a:.0f}%)'
                    )
                    logger.info(
                        f'Correlation guard: blocking {inst_a} '
                        f'(corr={corr:.2f} with {inst_b})'
                    )

    # Apply blocks to output
    result = dict(signals)
    for inst in blocked:
        result[inst] = dict(signals[inst])
        result[inst]['direction']      = 'HOLD'
        result[inst]['blocked_reason'] = reasons.get(inst, '')
        result[inst]['blocked_by']     = 'CORRELATION_GUARD'

    if blocked:
        logger.info(
            f'Correlation guard blocked: {blocked} | '
            f'Kept: {set(active.keys()) - blocked}'
        )

    return result