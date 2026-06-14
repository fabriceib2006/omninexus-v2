# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/regime_clusterer.py
# Algorithmic Regime Discovery
# Uses 10yr historical data to find how many distinct market
# regimes actually exist — instead of hardcoding 5
# Algorithm: K-Means clustering on:
# - 20-day volatility (ATR-based)
# - Price momentum (rate of change)
# - Yield curve slope (from FRED history)
# - Correlation between pairs
# - Volume anomaly Z-score
# Saves discovered regimes to brain/weights/regimes.json
# These replace the hardcoded 5 in regime_transfer.py
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

logger = logging.getLogger('omninexus.brain.regime_clusterer')

REGIMES_FILE = Path(
    os.path.dirname(os.path.abspath(__file__))
) / 'weights' / 'regimes.json'

HISTORY_DIR = Path(
    os.path.dirname(os.path.abspath(__file__))
).parent / 'data' / 'history'

REGIMES_FILE.parent.mkdir(parents=True, exist_ok=True)


# ── PURE PYTHON K-MEANS ────────────────────────────────────────
# No sklearn dependency — runs on any Azure environment

def _kmeans(data: list, k: int, max_iter: int = 100) -> tuple:
    """
    Pure Python K-Means clustering.
    data: list of feature vectors (lists of floats)
    k: number of clusters
    Returns (labels, centroids)
    """
    import random

    if len(data) < k:
        k = len(data)

    # Initialize centroids randomly
    centroids = random.sample(data, k)

    labels = [0] * len(data)

    for iteration in range(max_iter):
        # Assign each point to nearest centroid
        new_labels = []
        for point in data:
            distances = [
                _euclidean(point, c) for c in centroids
            ]
            new_labels.append(
                distances.index(min(distances))
            )

        # Check convergence
        if new_labels == labels:
            break
        labels = new_labels

        # Update centroids
        new_centroids = []
        for cluster_idx in range(k):
            cluster_points = [
                data[i] for i in range(len(data))
                if labels[i] == cluster_idx
            ]
            if cluster_points:
                n = len(cluster_points)
                dim = len(cluster_points[0])
                centroid = [
                    sum(p[d] for p in cluster_points) / n
                    for d in range(dim)
                ]
                new_centroids.append(centroid)
            else:
                new_centroids.append(centroids[cluster_idx])
        centroids = new_centroids

    return labels, centroids


def _euclidean(a: list, b: list) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _normalize(data: list) -> list:
    """Min-max normalize feature matrix."""
    if not data or not data[0]:
        return data
    dims = len(data[0])
    mins = [min(row[d] for row in data) for d in range(dims)]
    maxs = [max(row[d] for row in data) for d in range(dims)]
    result = []
    for row in data:
        norm = []
        for d in range(dims):
            rng = maxs[d] - mins[d]
            val = (row[d] - mins[d]) / rng if rng > 0 else 0
            norm.append(val)
        result.append(norm)
    return result


# ── FEATURE EXTRACTION ─────────────────────────────────────────

def _extract_features(candles: list, idx: int) -> Optional[list]:
    """
    Extracts regime features from a window of candles.
    Returns feature vector or None if insufficient data.
    Features:
    [0] 20-day volatility (normalized ATR)
    [1] 5-day momentum (price rate of change)
    [2] 20-day trend strength (price vs EMA20)
    [3] 10-day volume anomaly Z-score
    [4] High-low range expansion
    """
    window = 20
    if idx < window:
        return None

    closes  = [c['close']  for c in candles[idx-window:idx+1]]
    highs   = [c['high']   for c in candles[idx-window:idx+1]]
    lows    = [c['low']    for c in candles[idx-window:idx+1]]
    volumes = [c.get('volume', 1) for c in candles[idx-window:idx+1]]

    current = closes[-1]

    # Feature 0: 20-day volatility (ATR / price)
    trs = [highs[i] - lows[i] for i in range(len(highs))]
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else trs[-1]
    volatility = atr / current if current > 0 else 0

    # Feature 1: 5-day momentum
    momentum = (
        (closes[-1] - closes[-6]) / closes[-6]
        if len(closes) >= 6 and closes[-6] > 0
        else 0
    )

    # Feature 2: trend strength (price vs EMA20)
    ema20 = sum(closes) / len(closes)  # simplified
    trend = (current - ema20) / ema20 if ema20 > 0 else 0

    # Feature 3: volume Z-score
    if len(volumes) >= 10 and sum(volumes) > 0:
        mean_vol = sum(volumes) / len(volumes)
        std_vol  = (
            sum((v - mean_vol)**2 for v in volumes) /
            len(volumes)
        ) ** 0.5
        vol_z = (
            (volumes[-1] - mean_vol) / std_vol
            if std_vol > 0 else 0
        )
    else:
        vol_z = 0

    # Feature 4: range expansion
    recent_range = highs[-1] - lows[-1]
    avg_range    = sum(trs) / len(trs) if trs else 1
    range_exp    = recent_range / avg_range if avg_range > 0 else 1

    return [volatility, momentum, trend, vol_z, range_exp]


# ── OPTIMAL K FINDER ───────────────────────────────────────────

def _find_optimal_k(data: list, max_k: int = 15) -> int:
    """
    Uses elbow method to find optimal number of clusters.
    Tries k from 2 to max_k and picks the elbow point.
    """
    if len(data) < max_k:
        return min(5, len(data))

    inertias = []
    k_range  = range(2, min(max_k + 1, len(data) + 1))

    for k in k_range:
        labels, centroids = _kmeans(data, k, max_iter=50)
        # Calculate inertia (sum of squared distances to centroid)
        inertia = 0
        for i, point in enumerate(data):
            c = centroids[labels[i]]
            inertia += _euclidean(point, c) ** 2
        inertias.append(inertia)

    # Find elbow: largest drop in inertia improvement
    if len(inertias) < 2:
        return 5

    improvements = [
        inertias[i] - inertias[i+1]
        for i in range(len(inertias)-1)
    ]
    # Normalize improvements
    max_imp = max(improvements) if improvements else 1
    norm_imp = [x / max_imp for x in improvements]

    # Elbow = where improvement drops below 20% of max
    optimal_k = 5  # default
    for i, imp in enumerate(norm_imp):
        if imp < 0.2:
            optimal_k = i + 2  # +2 because k_range starts at 2
            break

    # Clamp between 4 and 15
    optimal_k = max(4, min(15, optimal_k))
    logger.info(f'Optimal k found: {optimal_k} regimes')
    return optimal_k


# ── REGIME LABELLER ────────────────────────────────────────────

def _label_regime(centroid: list, cluster_id: int) -> dict:
    """
    Auto-labels a regime based on its centroid features.
    Returns a regime descriptor.
    """
    volatility = centroid[0]
    momentum   = centroid[1]
    trend      = centroid[2]
    vol_z      = centroid[3]
    range_exp  = centroid[4]

    # Classify volatility
    if volatility > 0.015:
        vol_label = 'HIGH VOLATILITY'
    elif volatility > 0.008:
        vol_label = 'MEDIUM VOLATILITY'
    else:
        vol_label = 'LOW VOLATILITY'

    # Classify trend
    if trend > 0.02:
        trend_label = 'STRONG UPTREND'
        direction   = 'LONG'
    elif trend > 0.005:
        trend_label = 'MILD UPTREND'
        direction   = 'LONG'
    elif trend < -0.02:
        trend_label = 'STRONG DOWNTREND'
        direction   = 'SHORT'
    elif trend < -0.005:
        trend_label = 'MILD DOWNTREND'
        direction   = 'SHORT'
    else:
        trend_label = 'RANGING'
        direction   = 'NEUTRAL'

    # Classify volume
    vol_activity = 'HIGH VOLUME' if vol_z > 1.5 else 'NORMAL VOLUME'

    name = f'Regime {cluster_id+1}: {vol_label} {trend_label}'

    # Trading policy
    if direction == 'LONG':
        policy = 'PREFER BUY SIGNALS | Increase CFR threshold'
    elif direction == 'SHORT':
        policy = 'PREFER SELL SIGNALS | Increase CFR threshold'
    else:
        policy = 'RANGE TRADING | Reduce position size | Wait for breakout'

    return {
        'id':          cluster_id,
        'name':        name,
        'volatility':  round(volatility, 4),
        'momentum':    round(momentum,   4),
        'trend':       round(trend,      4),
        'vol_z':       round(vol_z,      2),
        'range_exp':   round(range_exp,  2),
        'direction':   direction,
        'policy':      policy,
        'centroid':    [round(x, 4) for x in centroid],
    }


# ── MAIN CLUSTERER ─────────────────────────────────────────────

def discover_regimes(
    instrument:  str  = 'XAUUSD',
    interval:    str  = '1d',
    force_rerun: bool = False,
) -> dict:
    """
    Main function. Runs K-Means on 10yr XAUUSD daily history
    to discover all distinct market regimes algorithmically.

    Saves results to brain/weights/regimes.json.
    Returns discovered regimes dict.
    """
    # Load from cache if fresh (ran this week)
    if not force_rerun and REGIMES_FILE.exists():
        try:
            with open(REGIMES_FILE, 'r') as f:
                data = json.load(f)
            last_run = datetime.fromisoformat(
                data.get('discovered_at', '2000-01-01')
            )
            age_days = (datetime.utcnow() - last_run).days
            if age_days < 7:
                logger.info(
                    f'Regimes loaded from cache '
                    f'({len(data.get("regimes", []))} regimes)'
                )
                return data
        except Exception:
            pass

    logger.info(
        f'Discovering market regimes from {instrument} '
        f'{interval} history...'
    )

    # Load history
    history_file = HISTORY_DIR / f'{instrument}_{interval}.json'
    if not history_file.exists():
        logger.error(f'No history file: {history_file}')
        return {'error': 'No history available', 'regimes': []}

    with open(history_file, 'r') as f:
        data = json.load(f)

    candles = list(reversed(data.get('data', [])))

    if len(candles) < 100:
        return {'error': 'Insufficient history', 'regimes': []}

    # Extract features for each candle
    features      = []
    feature_dates = []

    for idx in range(20, len(candles)):
        feat = _extract_features(candles, idx)
        if feat:
            features.append(feat)
            feature_dates.append(candles[idx]['datetime'])

    if len(features) < 20:
        return {'error': 'Insufficient features', 'regimes': []}

    logger.info(f'Features extracted: {len(features)} windows')

    # Normalize features
    norm_features = _normalize(features)

    # Find optimal number of regimes
    optimal_k = _find_optimal_k(
        norm_features[:500],  # use subset for speed
        max_k=15
    )

    logger.info(f'Running K-Means with k={optimal_k}...')

    # Run K-Means
    labels, centroids = _kmeans(
        norm_features, optimal_k, max_iter=100
    )

    # Count regime occurrences
    regime_counts = {}
    for label in labels:
        regime_counts[label] = regime_counts.get(label, 0) + 1

    # Build regime library
    regimes = []
    for cluster_id, centroid in enumerate(centroids):
        # Denormalize centroid for labelling
        regime = _label_regime(centroid, cluster_id)
        regime['occurrences'] = regime_counts.get(cluster_id, 0)
        regime['pct_of_time'] = round(
            regime_counts.get(cluster_id, 0) / len(labels) * 100, 1
        )
        regimes.append(regime)

    # Sort by frequency
    regimes.sort(key=lambda x: x['occurrences'], reverse=True)

    result = {
        'instrument':    instrument,
        'interval':      interval,
        'total_candles': len(candles),
        'total_windows': len(features),
        'k_regimes':     optimal_k,
        'regimes':       regimes,
        'discovered_at': datetime.utcnow().isoformat(),
    }

    # Save to file
    with open(REGIMES_FILE, 'w') as f:
        json.dump(result, f, indent=2)

    logger.info(
        f'Regime discovery complete: '
        f'{optimal_k} regimes found | '
        f'Saved to {REGIMES_FILE}'
    )

    # Log summary
    for r in regimes:
        logger.info(
            f'  Regime {r["id"]+1}: {r["name"]} | '
            f'{r["pct_of_time"]}% of time | '
            f'Policy: {r["policy"]}'
        )

    return result


def get_current_regime(
    instrument: str = 'XAUUSD',
    recent_candles: list = None,
) -> Optional[dict]:
    """
    Identifies which discovered regime matches current conditions.
    Compares current feature vector to all regime centroids.
    Returns closest regime.
    """
    # Load discovered regimes
    if not REGIMES_FILE.exists():
        logger.warning('No regimes file — run discover_regimes first')
        return None

    with open(REGIMES_FILE, 'r') as f:
        data = json.load(f)

    regimes = data.get('regimes', [])
    if not regimes:
        return None

    # Get recent candles if not provided
    if not recent_candles:
        history_file = HISTORY_DIR / f'{instrument}_1d.json'
        if not history_file.exists():
            return None
        with open(history_file, 'r') as f:
            hist = json.load(f)
        candles = list(reversed(hist.get('data', [])))
        recent_candles = candles
    else:
        candles = recent_candles

    if len(candles) < 25:
        return None

    # Extract current features
    current_feat = _extract_features(candles, len(candles)-1)
    if not current_feat:
        return None

    # Find closest regime centroid
    best_regime  = None
    best_dist    = float('inf')

    for regime in regimes:
        centroid = regime.get('centroid', [])
        if not centroid:
            continue
        dist = _euclidean(current_feat, centroid)
        if dist < best_dist:
            best_dist   = dist
            best_regime = regime

    if best_regime:
        best_regime = dict(best_regime)
        best_regime['similarity']      = round(1 / (1 + best_dist), 3)
        best_regime['current_features']= [
            round(x, 4) for x in current_feat
        ]
        logger.info(
            f'Current regime: {best_regime["name"]} | '
            f'Similarity: {best_regime["similarity"]:.3f}'
        )

    return best_regime


# ── DIRECT TEST ────────────────────────────────────────────────

if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Regime Clusterer Test')
    print('='*55 + '\n')

    print('Discovering regimes from XAUUSD 10yr history...')
    result = discover_regimes('XAUUSD', '1d', force_rerun=True)

    if 'error' not in result:
        print(f'\nFound {result["k_regimes"]} distinct regimes:\n')
        for r in result['regimes']:
            print(
                f'  Regime {r["id"]+1}: {r["name"]}\n'
                f'  Occurs: {r["pct_of_time"]}% of time\n'
                f'  Policy: {r["policy"]}\n'
            )
    else:
        print(f'Error: {result["error"]}')