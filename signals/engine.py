# ════════════════════════════════════════════════════════════════
# OMNINEXUS — signals/engine.py
# Unified Signal Engine
# Combines all data sources into BUY/SELL/HOLD signals
# Calculates Entry, Stop Loss, Take Profit for each pair
# Monitors price against active signals and fires alerts
# Brain studies SL hits and logs improvement notes
# ════════════════════════════════════════════════════════════════

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.signals.engine')

SIGNALS_FILE = Path(
    os.path.dirname(os.path.abspath(__file__))
) / 'active_signals.json'

_active_signals: dict = {}
_signal_history: list = []


def _save_signals():
    with open(SIGNALS_FILE, 'w') as f:
        json.dump({
            'signals': _active_signals,
            'history': _signal_history[-50:],
            'updated': datetime.utcnow().isoformat(),
        }, f, indent=2, default=str)


def _load_signals():
    global _active_signals, _signal_history
    if not SIGNALS_FILE.exists():
        return
    try:
        with open(SIGNALS_FILE, 'r') as f:
            data = json.load(f)
        _active_signals = data.get('signals', {})
        _signal_history = data.get('history', [])
        logger.info(f'Signals loaded: {len(_active_signals)} active')
    except Exception as e:
        logger.warning(f'Signal load error: {e}')


_load_signals()


def calculate_signal(instrument: str) -> dict:
    """
    Main signal calculation function.
    Combines technical indicators, macro signals,
    friction index, and circuit breaker checks.
    Returns full signal dict with direction, entry, SL, TP.
    """
    logger.info(f'Calculating signal for {instrument}...')

    # ── Circuit breaker check ──────────────────────────────────
    try:
        from brain.drawdown_circuit import is_trading_allowed
        allowed, reason = is_trading_allowed()
        if not allowed:
            from data.market_data import get_live_price
            price_data = get_live_price(instrument)
            current_price = price_data['price'] if price_data else 0
            return {
                'instrument': instrument,
                'direction':  'HOLD',
                'confidence': 0,
                'reason':     f'CIRCUIT BREAKER: {reason}',
                'price':      current_price,
                'emoji':      '⚪',
                'timestamp':  datetime.utcnow().isoformat(),
            }
    except Exception as e:
        logger.warning(f'Circuit breaker check error: {e}')

    # ── Load optimized weights ─────────────────────────────────
    try:
        from brain.backtester import load_weights
        opt_weights = load_weights()
        min_conf    = opt_weights.get('min_confidence', 65.0)
        min_confl   = opt_weights.get('min_confluence', 60.0)
    except Exception:
        opt_weights = None
        min_conf    = config.SIGNAL_STRONG_BIAS
        min_confl   = config.SIGNAL_MIN_CONFLUENCE

    # ── Event context check ────────────────────────────────────
    event_ctx = {'pre_event': False, 'confidence_boost': 0,
                 'volatility_warning': False, 'event_name': None}
    try:
        from brain.event_interrupt import get_interrupt
        event_ctx = get_interrupt().get_event_context(instrument)
        if event_ctx['pre_event']:
            min_conf  += event_ctx['confidence_boost']
            min_confl += event_ctx['confidence_boost']
    except Exception as e:
        logger.warning(f'Event context error: {e}')

    signals_votes  = []
    signal_details = {}

    # ── Get live price ─────────────────────────────────────────
    from data.market_data import get_live_price, get_market_status
    price_data = get_live_price(instrument)
    if not price_data:
        logger.error(f'No price data for {instrument}')
        return {'error': 'NO_PRICE_DATA', 'instrument': instrument}

    current_price = price_data['price']

    # ── Market session check ───────────────────────────────────
    market = get_market_status()
    if not market['is_open']:
        return {
            'instrument': instrument,
            'direction':  'HOLD',
            'confidence': 0,
            'reason':     'MARKET_CLOSED',
            'session':    market['session'],
            'price':      current_price,
            'emoji':      '⚪',
            'timestamp':  datetime.utcnow().isoformat(),
        }

    # ── Technical Indicators ───────────────────────────────────
    atr = None
    try:
        from data.indicators import (
            get_rsi, get_macd, get_bbands, get_atr, get_ema
        )

        rsi = get_rsi(instrument)
        if rsi:
            signal_details['rsi'] = rsi
            if rsi['signal'] in ['BUY', 'BULLISH']:
                signals_votes.append(
                    ('BUY', 2 if rsi['signal'] == 'BUY' else 1)
                )
            elif rsi['signal'] in ['SELL', 'BEARISH']:
                signals_votes.append(
                    ('SELL', 2 if rsi['signal'] == 'SELL' else 1)
                )

        macd = get_macd(instrument)
        if macd:
            signal_details['macd'] = macd
            if 'BULL' in macd['direction']:
                signals_votes.append(
                    ('BUY', 2 if 'WEAKLY' not in macd['direction'] else 1)
                )
            elif 'BEAR' in macd['direction']:
                signals_votes.append(
                    ('SELL', 2 if 'WEAKLY' not in macd['direction'] else 1)
                )

        bb = get_bbands(instrument)
        if bb:
            signal_details['bbands'] = bb
            if bb['signal'] in ['BUY', 'BULLISH']:
                signals_votes.append(
                    ('BUY', 2 if bb['signal'] == 'BUY' else 1)
                )
            elif bb['signal'] in ['SELL', 'BEARISH']:
                signals_votes.append(
                    ('SELL', 2 if bb['signal'] == 'SELL' else 1)
                )

        ema200 = get_ema(instrument, 200)
        if ema200:
            signal_details['ema200'] = ema200
            if ema200['trend'] == 'UPTREND':
                signals_votes.append(('BUY', 1))
            else:
                signals_votes.append(('SELL', 1))

        atr = get_atr(instrument)

    except Exception as e:
        logger.error(f'Indicator error for {instrument}: {e}')

    # ── Instrument-specific signals ────────────────────────────
    if instrument == 'XAUUSD':
        try:
            from ingestion.fred_yield import calculate_real_yield
            yield_data = calculate_real_yield()
            if 'error' not in yield_data:
                signal_details['real_yield'] = yield_data
                bias = yield_data.get('gold_bias', '')
                if 'BULLISH' in bias:
                    weight = 3 if 'STRONG' in bias else 2
                    signals_votes.append(('BUY', weight))
                elif 'BEARISH' in bias:
                    weight = 3 if 'STRONG' in bias else 2
                    signals_votes.append(('SELL', weight))
        except Exception as e:
            logger.warning(f'Real yield signal error: {e}')

    if instrument in ['GBPUSD', 'GBPJPY']:
        try:
            from signals.gbp import calculate_boe_boj_spread
            spread_data = calculate_boe_boj_spread()
            if 'error' not in spread_data:
                signal_details['boe_boj'] = spread_data
                bias = spread_data.get('bias', '')
                if bias == 'BULLISH':
                    signals_votes.append(('BUY', 2))
                elif bias == 'BEARISH':
                    signals_votes.append(('SELL', 2))
        except Exception as e:
            logger.warning(f'BoE/BoJ signal error: {e}')

    # ── Friction Index ─────────────────────────────────────────
    try:
        from ingestion.friction import calculate_friction_index
        friction = calculate_friction_index()
        if 'error' not in friction:
            signal_details['friction'] = friction
            score = friction.get('friction_score', 0)
            if score >= 70 and instrument == 'XAUUSD':
                signals_votes.append(('BUY', 2))
            # Check for friction spike interrupt
            try:
                from brain.event_interrupt import get_interrupt
                get_interrupt().check_friction_spike(
                    score, [instrument]
                )
            except Exception:
                pass
    except Exception as e:
        logger.warning(f'Friction signal error: {e}')

    # ── Tally votes ────────────────────────────────────────────
    buy_score  = sum(w for d, w in signals_votes if d == 'BUY')
    sell_score = sum(w for d, w in signals_votes if d == 'SELL')
    total      = buy_score + sell_score or 1

    buy_pct  = round(buy_score  / total * 100, 1)
    sell_pct = round(sell_score / total * 100, 1)

    if buy_pct >= min_conf:
        direction  = 'BUY'
        confidence = buy_pct
        emoji      = '🟢'
    elif sell_pct >= min_conf:
        direction  = 'SELL'
        confidence = sell_pct
        emoji      = '🔴'
    elif buy_pct >= min_confl:
        direction  = 'WEAK BUY'
        confidence = buy_pct
        emoji      = '🟡'
    elif sell_pct >= min_confl:
        direction  = 'WEAK SELL'
        confidence = sell_pct
        emoji      = '🟠'
    else:
        direction  = 'HOLD'
        confidence = 50.0
        emoji      = '⚪'

    # ── Calculate Entry, SL, TP ────────────────────────────────
    entry_price, stop_loss, take_profit, order_type = \
        _calculate_levels(instrument, direction, current_price, atr)

    # ── Build result ───────────────────────────────────────────
    result = {
        'instrument':  instrument,
        'direction':   direction,
        'confidence':  confidence,
        'buy_pct':     buy_pct,
        'sell_pct':    sell_pct,
        'emoji':       emoji,
        'price':       current_price,
        'entry':       entry_price,
        'stop_loss':   stop_loss,
        'take_profit': take_profit,
        'order_type':  order_type,
        'risk_reward': config.TP_MULTIPLIER,
        'session':     market['session'],
        'signals':     signal_details,
        'votes':       signals_votes,
        'timestamp':   datetime.utcnow().isoformat(),
    }

    # ── Risk sizing ────────────────────────────────────────────
    if direction not in ['HOLD'] and entry_price and stop_loss:
        try:
            from brain.risk_manager import get_risk_manager
            rm      = get_risk_manager()
            balance = getattr(config, 'CHALLENGE_CAPITAL', 1000.0)
            if balance <= 0:
                balance = 1000.0
            sizing  = rm.calculate_lot_size(
                instrument, entry_price, stop_loss, balance
            )
            result['lot_size']    = sizing.get('lot_size', 0.01)
            result['dollar_risk'] = sizing.get('dollar_risk', 0)
            result['sl_pips']     = sizing.get('sl_pips', 0)
        except Exception as e:
            logger.warning(f'Risk sizing error: {e}')
            result['lot_size'] = 0.01

    # ── Event warning ──────────────────────────────────────────
    if event_ctx.get('volatility_warning'):
        result['volatility_warning'] = True
        result['event_name']         = event_ctx.get('event_name')

    # ── Activate signal ────────────────────────────────────────
    if direction not in ['HOLD'] and entry_price:
        _activate_signal(result)

    logger.info(
        f'Signal: {instrument} {direction} {emoji} | '
        f'Confidence: {confidence}% | '
        f'Entry: {entry_price} | '
        f'SL: {stop_loss} | TP: {take_profit}'
    )
    return result


def _calculate_levels(
    instrument:    str,
    direction:     str,
    current_price: float,
    atr:           Optional[dict],
) -> tuple:
    if 'HOLD' in direction:
        return None, None, None, None

    is_buy = 'BUY' in direction

    if atr and atr.get('atr', 0) > 0:
        sl_dist = atr['atr'] * 1.5
        tp_dist = atr['atr'] * 3.0
    else:
        pip_sizes = {
            'XAUUSD': 0.1,
            'GBPUSD': 0.0001,
            'GBPJPY': 0.01,
        }
        pip     = pip_sizes.get(instrument, 0.0001)
        sl_pips = config.SL_PIPS.get(instrument, 30)
        sl_dist = sl_pips * pip
        tp_dist = sl_dist * config.TP_MULTIPLIER

    offset = sl_dist * 0.1

    if is_buy:
        entry       = round(current_price - offset, 5)
        stop_loss   = round(entry - sl_dist,         5)
        take_profit = round(entry + tp_dist,         5)
        order_type  = 'BUY LIMIT'
    else:
        entry       = round(current_price + offset, 5)
        stop_loss   = round(entry + sl_dist,         5)
        take_profit = round(entry - tp_dist,         5)
        order_type  = 'SELL LIMIT'

    return entry, stop_loss, take_profit, order_type


def _activate_signal(signal: dict):
    key = signal['instrument']
    signal['status']    = 'PENDING'
    signal['activated'] = datetime.utcnow().isoformat()
    _active_signals[key] = signal
    _save_signals()
    logger.info(
        f'Signal activated: {key} {signal["direction"]} | '
        f'Entry: {signal["entry"]}'
    )


def check_signal_levels() -> list:
    alerts = []
    for instrument, signal in list(_active_signals.items()):
        try:
            from data.market_data import get_live_price
            price_data = get_live_price(instrument)
            if not price_data:
                continue

            current   = price_data['price']
            direction = signal.get('direction', '')
            entry     = signal.get('entry')
            sl        = signal.get('stop_loss')
            tp        = signal.get('take_profit')
            status    = signal.get('status', 'PENDING')
            is_buy    = 'BUY' in direction

            if not entry or not sl or not tp:
                continue

            if status == 'PENDING':
                entry_touched = (
                    (is_buy     and current <= entry) or
                    (not is_buy and current >= entry)
                )
                if entry_touched:
                    signal['status']           = 'ACTIVE'
                    signal['entry_touched_at'] = (
                        datetime.utcnow().isoformat()
                    )
                    _active_signals[instrument] = signal
                    _save_signals()
                    alerts.append({
                        'type':       'ENTRY_HIT',
                        'instrument': instrument,
                        'direction':  direction,
                        'price':      current,
                        'entry':      entry,
                        'sl':         sl,
                        'tp':         tp,
                    })
                    logger.info(
                        f'ENTRY HIT: {instrument} {direction} '
                        f'@ {current}'
                    )

            elif status == 'ACTIVE':
                tp_hit = (
                    (is_buy     and current >= tp) or
                    (not is_buy and current <= tp)
                )
                sl_hit = (
                    (is_buy     and current <= sl) or
                    (not is_buy and current >= sl)
                )

                if tp_hit:
                    signal['status']    = 'TP_HIT'
                    signal['closed_at'] = datetime.utcnow().isoformat()
                    signal['result']    = 'WIN'
                    _signal_history.append(signal.copy())
                    del _active_signals[instrument]
                    _save_signals()
                    alerts.append({
                        'type':       'TP_HIT',
                        'instrument': instrument,
                        'direction':  direction,
                        'price':      current,
                        'tp':         tp,
                        'result':     'WIN',
                    })
                    logger.info(f'TP HIT: {instrument} WIN @ {current}')

                elif sl_hit:
                    signal['status']    = 'SL_HIT'
                    signal['closed_at'] = datetime.utcnow().isoformat()
                    signal['result']    = 'LOSS'
                    _signal_history.append(signal.copy())
                    del _active_signals[instrument]
                    _save_signals()
                    alerts.append({
                        'type':       'SL_HIT',
                        'instrument': instrument,
                        'direction':  direction,
                        'price':      current,
                        'sl':         sl,
                        'result':     'LOSS',
                        'signal':     signal,
                    })
                    logger.info(f'SL HIT: {instrument} LOSS @ {current}')
                    _study_loss(signal, current)

        except Exception as e:
            logger.error(f'Signal monitor error {instrument}: {e}')

    return alerts


def _study_loss(signal: dict, exit_price: float):
    logger.info(
        f'Brain studying SL hit: '
        f'{signal["instrument"]} {signal["direction"]}'
    )

    instrument = signal['instrument']
    direction  = signal['direction']
    entry      = signal.get('entry', 0)
    sl         = signal.get('stop_loss', 0)
    loss_pips  = abs(entry - exit_price)
    confidence = signal.get('confidence', 0)
    votes      = signal.get('votes', [])
    buy_votes  = [(d, w) for d, w in votes if d == 'BUY']
    sell_votes = [(d, w) for d, w in votes if d == 'SELL']
    session    = signal.get('session', '')
    notes      = []

    if confidence < 75:
        notes.append(
            f'Low confidence signal ({confidence}%) — '
            f'consider raising minimum to 75%'
        )
    if len(buy_votes) + len(sell_votes) < 4:
        notes.append(
            'Too few confirming signals — '
            'need at least 4 indicators agreeing'
        )
    if 'SYDNEY' in session or 'TOKYO' in session:
        notes.append(
            f'Entered during {session} — '
            f'lower liquidity session, consider avoiding'
        )

    ema_signal = signal.get('signals', {}).get('ema200', {})
    if ema_signal:
        ema_trend = ema_signal.get('trend', '')
        if 'BUY' in direction and ema_trend == 'DOWNTREND':
            notes.append(
                'Counter-trend trade against EMA200 — '
                'trade only with the trend'
            )
        elif 'SELL' in direction and ema_trend == 'UPTREND':
            notes.append(
                'Counter-trend trade against EMA200 — '
                'trade only with the trend'
            )

    study = {
        'timestamp':  datetime.utcnow().isoformat(),
        'instrument': instrument,
        'direction':  direction,
        'entry':      entry,
        'sl':         sl,
        'exit':       exit_price,
        'loss_pips':  round(loss_pips, 5),
        'confidence': confidence,
        'session':    session,
        'notes':      notes,
        'signal':     signal,
    }

    study_file = Path(
        os.path.dirname(os.path.abspath(__file__))
    ) / 'loss_studies.json'

    studies = []
    if study_file.exists():
        try:
            with open(study_file, 'r') as f:
                studies = json.load(f)
        except Exception:
            studies = []

    studies.append(study)
    with open(study_file, 'w') as f:
        json.dump(studies[-100:], f, indent=2, default=str)

    logger.info(f'Loss study saved: {len(notes)} improvement notes')
    return study


def format_signal_message(signal: dict) -> str:
    if 'error' in signal:
        return f'Signal error: {signal["error"]}'

    instrument = signal['instrument']
    direction  = signal['direction']
    confidence = signal.get('confidence', 0)
    emoji      = signal.get('emoji', '')
    price      = signal.get('price', 0)
    entry      = signal.get('entry')
    sl         = signal.get('stop_loss')
    tp         = signal.get('take_profit')
    session    = signal.get('session', 'UNKNOWN')
    ts         = signal.get('timestamp', '')[:19]

    if direction == 'HOLD':
        reason = signal.get('reason', 'No clear direction')
        return (
            f'⚪ <b>{instrument} — HOLD</b>\n'
            f'Reason: {reason}\n'
            f'Session: {session}'
        )

    decimals = 2 if instrument == 'XAUUSD' else 5
    fmt = f'{{:.{decimals}f}}'

    levels_line = ''
    if entry and sl and tp:
        lot  = signal.get('lot_size', 0.01)
        risk = signal.get('dollar_risk', 0)
        levels_line = (
            f'\n━━━━━━━━━━━━━━━━━━━━\n'
            f'Entry:       <b>{fmt.format(entry)}</b>\n'
            f'Stop Loss:   <b>{fmt.format(sl)}</b>\n'
            f'Take Profit: <b>{fmt.format(tp)}</b>\n'
            f'Risk/Reward: 1:{config.TP_MULTIPLIER}\n'
            f'Lot Size:    {lot} lots\n'
            f'Dollar Risk: ${risk:.2f}\n'
        )

    signals   = signal.get('signals', {})
    rsi       = signals.get('rsi', {})
    macd      = signals.get('macd', {})
    bb        = signals.get('bbands', {})
    ema       = signals.get('ema200', {})

    indicators = ''
    if rsi:
        indicators += (
            f'RSI({rsi.get("period",14)}): '
            f'{rsi.get("value","?")} '
            f'{rsi.get("emoji","")}\n'
        )
    if macd:
        indicators += (
            f'MACD: {macd.get("direction","?")} '
            f'{macd.get("emoji","")}\n'
        )
    if bb:
        indicators += (
            f'BBands: {bb.get("position","?")} '
            f'{bb.get("emoji","")}\n'
        )
    if ema:
        indicators += (
            f'EMA200: {ema.get("trend","?")} '
            f'{ema.get("emoji","")}\n'
        )

    event_warn = ''
    if signal.get('volatility_warning'):
        event_warn = (
            f'\n⚡ <b>EVENT WARNING:</b> '
            f'{signal.get("event_name","Unknown event")}\n'
        )

    return (
        f'{emoji} <b>{instrument} — {direction}</b>\n'
        f'<code>{ts} UTC</code>\n\n'
        f'Confidence:  <b>{confidence}%</b>\n'
        f'Buy votes:   {signal.get("buy_pct",0)}%\n'
        f'Sell votes:  {signal.get("sell_pct",0)}%\n'
        f'Price:       {fmt.format(price)}\n'
        f'Session:     {session}\n'
        f'{levels_line}'
        f'{event_warn}\n'
        f'<b>INDICATORS:</b>\n'
        f'{indicators}'
    )


def format_all_signals() -> str:
    lines = ['📡 <b>LIVE SIGNAL REPORT</b>\n']
    for instrument in config.INSTRUMENTS:
        signal = calculate_signal(instrument)
        lines.append(format_signal_message(signal))
        lines.append('')
    return '\n'.join(lines)


def get_active_signals_summary() -> str:
    if not _active_signals:
        return 'No active signals.'

    lines = [f'<b>ACTIVE SIGNALS ({len(_active_signals)})</b>\n']
    for inst, sig in _active_signals.items():
        decimals = 2 if inst == 'XAUUSD' else 5
        fmt      = f'{{:.{decimals}f}}'
        lines.append(
            f'{sig.get("emoji","⚪")} <b>{inst}</b> '
            f'{sig.get("direction","?")} — '
            f'{sig.get("status","PENDING")}\n'
            f'Entry: {fmt.format(sig["entry"])} | '
            f'SL: {fmt.format(sig["stop_loss"])} | '
            f'TP: {fmt.format(sig["take_profit"])}\n'
        )
    return '\n'.join(lines)


if __name__ == '__main__':
    import re
    print('\n' + '='*55)
    print('OMNINEXUS — Signal Engine Test')
    print('='*55 + '\n')

    for inst in config.INSTRUMENTS:
        print(f'Calculating {inst} signal...')
        sig = calculate_signal(inst)
        msg = format_signal_message(sig)
        clean = re.sub(r'<[^>]+>', '', msg)
        print(clean)
        print('-'*40)