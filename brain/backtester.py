# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/backtester.py
# Self-Improving Backtesting Engine
#
# HOW IT WORKS:
# 1. Loads 10yr JSON history from data/history/
# 2. Runs signal engine across every candle (simulated)
# 3. Calculates win rate, profit factor, drawdown
# 4. If win rate < 90%: adjusts signal weights and repeats
# 5. Saves best-performing weights to brain/weights/
# 6. Every Friday after market close: downloads new week,
#    merges with history, reruns backtest until 90% again
#
# This means the brain NEVER stops learning.
# ════════════════════════════════════════════════════════════════

import json
import logging
import os
import time
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.backtester')

# ── PATHS ──────────────────────────────────────────────────────
WEIGHTS_DIR  = Path(os.path.dirname(os.path.abspath(__file__))) / 'weights'
HISTORY_DIR  = Path(os.path.dirname(os.path.abspath(__file__))).parent / 'data' / 'history'
RESULTS_DIR  = Path(os.path.dirname(os.path.abspath(__file__))) / 'backtest_results'

WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── TARGET ─────────────────────────────────────────────────────
TARGET_WIN_RATE     = 0.68   # realistic live target
MIN_EXPECTANCY      = 0.15   # minimum 15% positive expectancy
MIN_PROFIT_FACTOR   = 1.5    # profits must be 1.5x losses
MAX_ITERATIONS      = 200    # max optimization loops per session
MIN_TRADES_REQUIRED = 50     # need at least 50 trades to be valid

# ── DEFAULT SIGNAL WEIGHTS ─────────────────────────────────────
# These are what engine.py uses to vote on direction
# Backtester adjusts these until 90% win rate is achieved
DEFAULT_WEIGHTS = {
    'rsi_strong':       2.0,   # RSI overbought/oversold
    'rsi_weak':         1.0,   # RSI bullish/bearish zone
    'macd_strong':      2.0,   # MACD crossover
    'macd_weak':        1.0,   # MACD direction
    'bbands_strong':    2.0,   # Price outside bands
    'bbands_weak':      1.0,   # Price near bands
    'ema200':           1.0,   # Trend filter
    'real_yield':       3.0,   # FRED real yield (XAUUSD)
    'real_yield_strong':4.0,   # Strong real yield signal
    'boe_boj':          2.0,   # BoE/BoJ spread (GBP pairs)
    'friction_high':    2.0,   # High geopolitical friction
    'dark_pool':        1.5,   # Dark pool anomaly
    'min_confluence':   60.0,  # Min % votes to take signal
    'min_confidence':   65.0,  # Min confidence score
    'sl_atr_multiplier':1.5,   # SL = ATR × this
    'tp_atr_multiplier':3.0,   # TP = ATR × this
}

WEIGHTS_FILE = WEIGHTS_DIR / 'signal_weights.json'


def load_weights() -> dict:
    """Loads optimized weights. Falls back to defaults."""
    if WEIGHTS_FILE.exists():
        try:
            with open(WEIGHTS_FILE, 'r') as f:
                data = json.load(f)
            weights = data.get('weights', DEFAULT_WEIGHTS)
            logger.info(
                f'Weights loaded — win rate was '
                f'{data.get("win_rate", "?"):.1%}'
            )
            return weights
        except Exception as e:
            logger.warning(f'Weight load error: {e}')
    return deepcopy(DEFAULT_WEIGHTS)


def save_weights(weights: dict, stats: dict):
    """Saves optimized weights with performance stats."""
    payload = {
        'weights':       weights,
        'win_rate':      stats.get('win_rate', 0),
        'profit_factor': stats.get('profit_factor', 0),
        'total_trades':  stats.get('total_trades', 0),
        'max_drawdown':  stats.get('max_drawdown', 0),
        'optimized_at':  datetime.utcnow().isoformat(),
        'instruments':   stats.get('instruments', []),
    }
    with open(WEIGHTS_FILE, 'w') as f:
        json.dump(payload, f, indent=2)
    logger.info(
        f'Weights saved — win rate: '
        f'{stats.get("win_rate", 0):.1%}'
    )


# ── SIMULATED SIGNAL ENGINE ────────────────────────────────────

def _simulate_signal(
    candles:    list,
    idx:        int,
    weights:    dict,
    instrument: str,
) -> Optional[dict]:
    """
    Simulates signal calculation on historical candles.
    Uses simple indicator approximations calculated from
    raw OHLCV data — no API calls needed for backtesting.
    Returns signal dict or None if no signal.
    """
    if idx < 50:
        return None  # need enough history

    # Extract price series
    closes = [c['close'] for c in candles[idx-50:idx+1]]
    highs  = [c['high']  for c in candles[idx-50:idx+1]]
    lows   = [c['low']   for c in candles[idx-50:idx+1]]
    current = closes[-1]

    # ── RSI ────────────────────────────────────────────────
    rsi_val = _calc_rsi(closes, 14)

    # ── MACD ───────────────────────────────────────────────
    macd_val, macd_sig = _calc_macd(closes)
    macd_hist = macd_val - macd_sig

    # ── Bollinger Bands ────────────────────────────────────
    bb_upper, bb_mid, bb_lower = _calc_bbands(closes, 20)
    bb_pct = (
        (current - bb_lower) / (bb_upper - bb_lower)
        if (bb_upper - bb_lower) > 0 else 0.5
    )

    # ── EMA200 ─────────────────────────────────────────────
    ema200 = _calc_ema(closes, min(200, len(closes)))

    # ── ATR ────────────────────────────────────────────────
    atr = _calc_atr(highs, lows, closes, 14)

    # ── Vote counting ───────────────────────────────────────
    buy_score  = 0.0
    sell_score = 0.0

    # RSI
    if rsi_val <= 30:
        buy_score  += weights['rsi_strong']
    elif rsi_val <= 45:
        buy_score  += weights['rsi_weak']
    elif rsi_val >= 70:
        sell_score += weights['rsi_strong']
    elif rsi_val >= 55:
        sell_score += weights['rsi_weak']

    # MACD
    if macd_hist > 0 and macd_val > macd_sig:
        buy_score  += weights['macd_strong']
    elif macd_hist > 0:
        buy_score  += weights['macd_weak']
    elif macd_hist < 0 and macd_val < macd_sig:
        sell_score += weights['macd_strong']
    elif macd_hist < 0:
        sell_score += weights['macd_weak']

    # Bollinger Bands
    if current <= bb_lower:
        buy_score  += weights['bbands_strong']
    elif bb_pct < 0.2:
        buy_score  += weights['bbands_weak']
    elif current >= bb_upper:
        sell_score += weights['bbands_strong']
    elif bb_pct > 0.8:
        sell_score += weights['bbands_weak']

    # EMA200 trend filter
    if current > ema200:
        buy_score  += weights['ema200']
    else:
        sell_score += weights['ema200']

    # Tally
    total = buy_score + sell_score or 1
    buy_pct  = buy_score  / total * 100
    sell_pct = sell_score / total * 100

    min_conf = weights['min_confluence']

    if buy_pct >= weights['min_confidence']:
        direction   = 'BUY'
        confidence  = buy_pct
    elif sell_pct >= weights['min_confidence']:
        direction   = 'SELL'
        confidence  = sell_pct
    else:
        return None  # No signal

    if confidence < min_conf:
        return None

    # Calculate levels
    sl_dist = atr * weights['sl_atr_multiplier']
    tp_dist = atr * weights['tp_atr_multiplier']

    if direction == 'BUY':
        entry      = current
        stop_loss  = entry - sl_dist
        take_profit= entry + tp_dist
    else:
        entry      = current
        stop_loss  = entry + sl_dist
        take_profit= entry - tp_dist

    return {
        'direction':   direction,
        'confidence':  confidence,
        'entry':       entry,
        'stop_loss':   stop_loss,
        'take_profit': take_profit,
        'atr':         atr,
        'rsi':         rsi_val,
        'candle_idx':  idx,
        'datetime':    candles[idx]['datetime'],
    }


def _simulate_trade(
    signal:  dict,
    candles: list,
    idx:     int,
    max_candles_forward: int = 100,
) -> Optional[dict]:
    """
    Simulates trade outcome by looking at future candles.
    Checks if price hits TP or SL first.
    Returns trade result dict.
    """
    is_buy  = signal['direction'] == 'BUY'
    entry   = signal['entry']
    sl      = signal['stop_loss']
    tp      = signal['take_profit']

    for i in range(1, min(max_candles_forward, len(candles) - idx)):
        future = candles[idx + i]
        high   = future['high']
        low    = future['low']

        if is_buy:
            if high >= tp:
                return {
                    'result':     'WIN',
                    'exit_price': tp,
                    'pips':       tp - entry,
                    'candles':    i,
                    'signal':     signal,
                }
            if low <= sl:
                return {
                    'result':     'LOSS',
                    'exit_price': sl,
                    'pips':       entry - sl,
                    'candles':    i,
                    'signal':     signal,
                }
        else:
            if low <= tp:
                return {
                    'result':     'WIN',
                    'exit_price': tp,
                    'pips':       entry - tp,
                    'candles':    i,
                    'signal':     signal,
                }
            if high >= sl:
                return {
                    'result':     'LOSS',
                    'exit_price': sl,
                    'pips':       sl - entry,
                    'candles':    i,
                    'signal':     signal,
                }

    # Timeout — count as neutral, exclude from stats
    return None


# ── BACKTEST RUNNER ────────────────────────────────────────────

def run_backtest(
    instrument: str,
    interval:   str   = '1d',
    weights:    dict  = None,
) -> dict:
    """
    Runs full backtest on stored history JSON.
    Returns performance statistics.
    """
    if weights is None:
        weights = load_weights()

    # Load history from JSON
    history_file = HISTORY_DIR / f'{instrument}_{interval}.json'
    if not history_file.exists():
        logger.error(f'No history file: {history_file}')
        return {'error': f'No history for {instrument} {interval}'}

    with open(history_file, 'r') as f:
        data = json.load(f)

    candles = data.get('data', [])
    # Reverse so oldest first for backtesting
    candles = list(reversed(candles))

    if len(candles) < 100:
        return {'error': 'Insufficient history'}

    logger.info(
        f'Backtesting {instrument} {interval} — '
        f'{len(candles)} candles'
    )

    trades   = []
    wins     = 0
    losses   = 0
    total_profit = 0.0
    total_loss   = 0.0
    peak_equity  = 1.0
    equity       = 1.0
    max_drawdown = 0.0
    risk_per_trade = 0.01  # 1% per trade

    # Skip last 50 candles — keep as out-of-sample test
    backtest_candles = candles[:-50]

    for idx in range(50, len(backtest_candles)):
        signal = _simulate_signal(
            backtest_candles, idx, weights, instrument
        )
        if not signal:
            continue

        trade = _simulate_trade(
            signal, backtest_candles, idx
        )
        if not trade:
            continue

        # Update equity curve
        if trade['result'] == 'WIN':
            wins += 1
            profit = risk_per_trade * weights['tp_atr_multiplier'] / weights['sl_atr_multiplier']
            equity += profit
            total_profit += profit
        else:
            losses += 1
            equity -= risk_per_trade
            total_loss += risk_per_trade

        # Track drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity
        if dd > max_drawdown:
            max_drawdown = dd

        trades.append({
            'datetime':  signal['datetime'],
            'direction': signal['direction'],
            'result':    trade['result'],
            'pips':      trade['pips'],
            'candles':   trade['candles'],
        })

    total_trades  = wins + losses
    win_rate      = wins / total_trades if total_trades > 0 else 0
    profit_factor = (
        total_profit / total_loss
        if total_loss > 0 else float('inf')
    )

    stats = {
        'instrument':    instrument,
        'interval':      interval,
        'total_candles': len(candles),
        'total_trades':  total_trades,
        'wins':          wins,
        'losses':        losses,
        'win_rate':      win_rate,
        'profit_factor': round(profit_factor, 2),
        'max_drawdown':  round(max_drawdown, 4),
        'final_equity':  round(equity, 4),
        'timestamp':     datetime.utcnow().isoformat(),
    }

    logger.info(
        f'Backtest {instrument}: '
        f'Win rate={win_rate:.1%} | '
        f'Trades={total_trades} | '
        f'PF={profit_factor:.2f} | '
        f'DD={max_drawdown:.1%}'
    )
    return stats


# ── SELF-IMPROVEMENT OPTIMIZER ─────────────────────────────────

def optimize_weights(
    instruments:  list = None,
    target_rate:  float = TARGET_WIN_RATE,
    max_iter:     int   = MAX_ITERATIONS,
    verbose:      bool  = True,
) -> dict:
    """
    Repeatedly adjusts signal weights until win rate >= target.
    Uses a hill-climbing approach:
    - Start with current weights
    - Run backtest across all instruments
    - If win rate < target: nudge the weakest parameters
    - Repeat until target reached or max_iter hit
    - Save best weights found

    Returns final performance stats.
    """
    if instruments is None:
        instruments = config.INSTRUMENTS

    weights     = load_weights()
    best_weights = deepcopy(weights)
    best_win_rate = 0.0
    iteration   = 0

    logger.info(
        f'Starting weight optimization — '
        f'target: {target_rate:.0%} | '
        f'max iterations: {max_iter}'
    )

    while iteration < max_iter:
        iteration += 1

        # Run backtest on all instruments
        all_stats  = []
        total_wins = 0
        total_trades = 0

        for inst in instruments:
            for interval in ['1d', '1h']:
                stats = run_backtest(inst, interval, weights)
                if 'error' not in stats and stats['total_trades'] >= 10:
                    all_stats.append(stats)
                    total_wins   += stats['wins']
                    total_trades += stats['total_trades']

        if total_trades < MIN_TRADES_REQUIRED:
            logger.warning(
                f'Iteration {iteration}: insufficient trades '
                f'({total_trades}) — check history files'
            )
            break

        combined_win_rate = (
            total_wins / total_trades if total_trades > 0 else 0
        )
        avg_drawdown = (
            sum(s['max_drawdown'] for s in all_stats) /
            len(all_stats) if all_stats else 0
        )
        avg_pf = (
            sum(s['profit_factor'] for s in all_stats
                if s['profit_factor'] != float('inf')) /
            len(all_stats) if all_stats else 0
        )

        if verbose:
            logger.info(
                f'Iteration {iteration:3d}: '
                f'Win rate={combined_win_rate:.1%} | '
                f'Trades={total_trades} | '
                f'DD={avg_drawdown:.1%} | '
                f'PF={avg_pf:.2f}'
            )

        # Track best
        if combined_win_rate > best_win_rate:
            best_win_rate = combined_win_rate
            best_weights  = deepcopy(weights)

        # Calculate expectancy
        avg_win  = avg_pf / (1 + avg_pf) if avg_pf > 0 else 0
        avg_loss = 1 / (1 + avg_pf) if avg_pf > 0 else 1
        expectancy = (
                     (combined_win_rate * avg_win) -
                     ((1 - combined_win_rate) * avg_loss)
    )
        if verbose:
            logger.info(
            f'Expectancy={expectancy:.3f} | '
            f'PF={avg_pf:.2f}'
    )

        # Target: win rate AND positive expectancy AND profit factor
        if (combined_win_rate >= target_rate and expectancy >= MIN_EXPECTANCY and avg_pf >= MIN_PROFIT_FACTOR            
    ):
            
         logger.info(
        f'TARGET REACHED at iteration {iteration}! '
        f'Win={combined_win_rate:.1%} | '
        f'Expectancy={expectancy:.3f}'
    )
         break

        # ── Adjust weights based on what's underperforming ──
        weights = _adjust_weights(
            weights, all_stats, combined_win_rate, target_rate
        )

    # Save best weights found
    final_stats = {
        'win_rate':      best_win_rate,
        'total_trades':  total_trades,
        'profit_factor': avg_pf if all_stats else 0,
        'max_drawdown':  avg_drawdown if all_stats else 0,
        'instruments':   instruments,
        'iterations':    iteration,
        'target_reached': best_win_rate >= target_rate,
    }
    save_weights(best_weights, final_stats)

    logger.info(
        f'Optimization complete: '
        f'Best win rate={best_win_rate:.1%} | '
        f'Iterations={iteration} | '
        f'Target reached={best_win_rate >= target_rate}'
    )
    return final_stats


def _adjust_weights(
    weights:      dict,
    stats:        list,
    current_rate: float,
    target_rate:  float,
) -> dict:
    """
    Adjusts signal weights to improve win rate.
    Strategy:
    - If win rate is far from target: make larger adjustments
    - If close: make smaller fine-tuning adjustments
    - Tighten min_confidence to filter out weak signals
    - Adjust SL/TP multipliers to improve R:R
    """
    import random
    w = deepcopy(weights)
    gap = target_rate - current_rate

    # Scale adjustment size by how far we are from target
    step = 0.3 if gap > 0.2 else 0.1 if gap > 0.1 else 0.05

    # Randomly pick which parameter to adjust
    adjustable = [
        'rsi_strong', 'rsi_weak',
        'macd_strong', 'macd_weak',
        'bbands_strong', 'bbands_weak',
        'ema200', 'real_yield', 'boe_boj',
        'min_confluence', 'min_confidence',
        'sl_atr_multiplier', 'tp_atr_multiplier',
    ]

    # Pick 2-3 parameters to adjust per iteration
    params_to_adjust = random.sample(
        adjustable, min(3, len(adjustable))
    )

    for param in params_to_adjust:
        if param in ('min_confluence', 'min_confidence'):
            # Increase thresholds to filter weak signals
            w[param] = min(
                90.0, w[param] + step * 5
            )
        elif param == 'sl_atr_multiplier':
            # Tighter SL = more losses, wider = more wins
            # Find sweet spot
            w[param] = max(
                0.5, w[param] + random.choice([-1, 1]) * step
            )
        elif param == 'tp_atr_multiplier':
            # Wider TP = harder to hit
            w[param] = max(
                1.0, w[param] + random.choice([-1, 1]) * step
            )
        else:
            # Adjust signal weights
            w[param] = max(
                0.1, w[param] + random.choice([-1, 1]) * step
            )

    return w


# ── WEEKLY UPDATE JOB ──────────────────────────────────────────

def run_weekly_update():
    """
    Runs every Friday after market close (22:00 UTC).
    1. Downloads the new week's candle data
    2. Merges with existing 10yr history JSON files
    3. Re-runs optimization until 90% win rate
    4. Saves new weights
    5. Sends Telegram summary

    Called by run_system.py or Azure Function scheduler.
    """
    logger.info('Starting weekly brain update...')
    now = datetime.utcnow()

    # Step 1: Download new weekly data
    logger.info('Downloading new weekly candles...')
    try:
        from data.history import download_history
        results = {}
        for instrument in config.INSTRUMENTS:
            for interval in ['1d', '1h']:
                candles = download_history(
                    instrument   = instrument,
                    interval     = interval,
                    period       = '1mo',   # last month
                    force_reload = True,    # always fresh
                )
                if candles:
                    results[f'{instrument}_{interval}'] = len(candles)
                    logger.info(
                        f'Downloaded: {instrument} {interval} — '
                        f'{len(candles)} new candles'
                    )
        logger.info(f'Download complete: {results}')
    except Exception as e:
        logger.error(f'Weekly download error: {e}')

    # Step 2: Re-run full optimization
    logger.info('Re-optimizing weights on updated history...')
    stats = optimize_weights(
        instruments = config.INSTRUMENTS,
        target_rate = TARGET_WIN_RATE,
        max_iter    = MAX_ITERATIONS,
    )

    # Step 3: Save results report
    report = {
        'week_ending':   now.strftime('%Y-%m-%d'),
        'win_rate':      stats.get('win_rate', 0),
        'total_trades':  stats.get('total_trades', 0),
        'profit_factor': stats.get('profit_factor', 0),
        'max_drawdown':  stats.get('max_drawdown', 0),
        'iterations':    stats.get('iterations', 0),
        'target_reached':stats.get('target_reached', False),
        'timestamp':     now.isoformat(),
    }

    report_file = RESULTS_DIR / f'weekly_{now.strftime("%Y%m%d")}.json'
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)

    logger.info(
        f'Weekly update complete: '
        f'Win rate={stats.get("win_rate", 0):.1%} | '
        f'Target reached={stats.get("target_reached", False)}'
    )

    # Step 4: Send Telegram alert
    try:
        import asyncio
        from tg_bot.bot import send_alert
        target_emoji = (
            '✅' if stats.get('target_reached') else '⚠️'
        )
        msg = (
            f'🧠 <b>WEEKLY BRAIN UPDATE</b>\n'
            f'Week ending: {now.strftime("%Y-%m-%d")}\n\n'
            f'{target_emoji} Win Rate:      '
            f'<b>{stats.get("win_rate", 0):.1%}</b>\n'
            f'Total Trades:  {stats.get("total_trades", 0)}\n'
            f'Profit Factor: {stats.get("profit_factor", 0):.2f}\n'
            f'Max Drawdown:  {stats.get("max_drawdown", 0):.1%}\n'
            f'Iterations:    {stats.get("iterations", 0)}\n\n'
            f'{"Brain calibrated and ready." if stats.get("target_reached") else "Still optimizing — will retry next cycle."}'
        )
        asyncio.run(send_alert(msg))
    except Exception as e:
        logger.warning(f'Weekly alert error: {e}')

    return report


# ── FRIDAY SCHEDULER ───────────────────────────────────────────

def is_friday_close() -> bool:
    """Returns True if it's Friday between 22:00-23:59 UTC."""
    now = datetime.utcnow()
    return now.weekday() == 4 and now.hour >= 22


async def weekly_scheduler_loop():
    """
    Async loop that checks every hour if it's Friday close.
    If yes, triggers the weekly brain update.
    Attach to bot's event loop in main().
    """
    import asyncio
    last_run_week = None

    while True:
        try:
            now = datetime.utcnow()
            current_week = now.isocalendar()[1]

            if is_friday_close() and last_run_week != current_week:
                logger.info(
                    'Friday market close detected — '
                    'starting weekly brain update'
                )
                last_run_week = current_week
                # Run in thread so it doesn't block bot
                import threading
                t = threading.Thread(
                    target=run_weekly_update,
                    daemon=True,
                )
                t.start()

        except Exception as e:
            logger.error(f'Scheduler error: {e}')

        # Check every hour
        await asyncio.sleep(3600)


# ── INDICATOR CALCULATORS ──────────────────────────────────────
# Pure Python — no API calls needed for backtesting

def _calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains)  / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs  = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0
    k   = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def _calc_macd(closes: list) -> tuple:
    if len(closes) < 26:
        return 0.0, 0.0
    ema12 = _calc_ema(closes, 12)
    ema26 = _calc_ema(closes, 26)
    macd  = ema12 - ema26
    # Signal line = 9-period EMA of MACD
    # Simplified: use last 9 MACD values
    macd_vals = []
    for i in range(max(0, len(closes)-9), len(closes)):
        e12 = _calc_ema(closes[:i+1], 12)
        e26 = _calc_ema(closes[:i+1], 26)
        macd_vals.append(e12 - e26)
    signal = _calc_ema(macd_vals, min(9, len(macd_vals)))
    return macd, signal


def _calc_bbands(
    closes: list,
    period: int = 20
) -> tuple:
    if len(closes) < period:
        c = closes[-1]
        return c, c, c
    window = closes[-period:]
    mid    = sum(window) / period
    std    = (sum((x - mid)**2 for x in window) / period) ** 0.5
    return mid + 2*std, mid, mid - 2*std


def _calc_atr(
    highs:  list,
    lows:   list,
    closes: list,
    period: int = 14,
) -> float:
    if len(closes) < period + 1:
        return abs(highs[-1] - lows[-1])
    trs = []
    for i in range(1, period + 1):
        idx = len(closes) - period + i - 1
        tr  = max(
            highs[idx]  - lows[idx],
            abs(highs[idx]  - closes[idx-1]),
            abs(lows[idx]   - closes[idx-1]),
        )
        trs.append(tr)
    return sum(trs) / period


# ── DIRECT TEST ────────────────────────────────────────────────

if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Backtester Self-Improvement Test')
    print('='*55 + '\n')

    print('Running quick backtest on XAUUSD daily...')
    stats = run_backtest('XAUUSD', '1d')
    if 'error' not in stats:
        print(f'Win rate:      {stats["win_rate"]:.1%}')
        print(f'Total trades:  {stats["total_trades"]}')
        print(f'Profit factor: {stats["profit_factor"]:.2f}')
        print(f'Max drawdown:  {stats["max_drawdown"]:.1%}')
    else:
        print(f'Error: {stats["error"]}')

    print('\nStarting weight optimization (max 10 iterations for test)...')
    result = optimize_weights(
        instruments=['XAUUSD'],
        target_rate=0.90,
        max_iter=10,
    )
    print(f'\nBest win rate:   {result["win_rate"]:.1%}')
    print(f'Target reached:  {result["target_reached"]}')
    print(f'Iterations used: {result["iterations"]}')