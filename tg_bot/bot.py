# ════════════════════════════════════════════════════════════════
# OMNINEXUS — tg_bot/bot.py
# Telegram Command & Control Terminal v2
# Full live signal bot with entry/SL/TP alert system
# Deployed on Azure App Service — runs 24/7
# ════════════════════════════════════════════════════════════════

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)
from config import config

logger = logging.getLogger('omninexus.telegram')

# ── CHALLENGE STATE FILE ───────────────────────────────────────
CHALLENGE_STATE_FILE = Path(
    os.path.dirname(os.path.abspath(__file__))
) / 'challenge_state.json'

# ── CONVERSATION STATES ────────────────────────────────────────
CAPITAL, TARGET, DAYS = range(3)


# ════════════════════════════════════════════════════════════════
# CHALLENGE PERSISTENCE
# ════════════════════════════════════════════════════════════════

def save_challenge_state():
    state = {
        'active':     config.CHALLENGE_ACTIVE,
        'capital':    getattr(config, 'CHALLENGE_CAPITAL',    0),
        'target_pct': getattr(config, 'CHALLENGE_TARGET_PCT', 0),
        'days':       getattr(config, 'CHALLENGE_DAYS',       0),
        'start_date': (
            config.CHALLENGE_START_DATE.isoformat()
            if getattr(config, 'CHALLENGE_START_DATE', None)
            else None
        ),
    }
    try:
        with open(CHALLENGE_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f'Challenge save error: {e}')


def load_challenge_state():
    if not CHALLENGE_STATE_FILE.exists():
        return
    try:
        with open(CHALLENGE_STATE_FILE, 'r') as f:
            state = json.load(f)
        config.CHALLENGE_ACTIVE     = state.get('active', False)
        config.CHALLENGE_CAPITAL    = state.get('capital', 0)
        config.CHALLENGE_TARGET_PCT = state.get('target_pct', 0)
        config.CHALLENGE_DAYS       = state.get('days', 0)
        raw = state.get('start_date')
        config.CHALLENGE_START_DATE = (
            datetime.fromisoformat(raw) if raw else None
        )
    except Exception as e:
        logger.warning(f'Challenge load error: {e}')


# ════════════════════════════════════════════════════════════════
# ALERT SENDER — called by all system components
# ════════════════════════════════════════════════════════════════

async def send_alert(message: str, parse_mode: str = 'HTML'):
    """Sends a push alert to your Telegram chat."""
    try:
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id    = config.TELEGRAM_CHAT_ID,
            text       = message,
            parse_mode = parse_mode,
        )
        logger.info(f'Alert sent: {message[:60]}...')
    except Exception as e:
        logger.error(f'Alert send error: {e}')


# ════════════════════════════════════════════════════════════════
# ORDER ALERT FORMATTERS
# ════════════════════════════════════════════════════════════════

def _fmt(inst: str, price: float) -> str:
    dec = 2 if inst == 'XAUUSD' else 5
    return f'{price:.{dec}f}'


def format_buy_limit_alert(inst, entry, sl, tp, size, reason=''):
    return (
        f'📥 <b>[BUY LIMIT PLACED]</b>\n\n'
        f'Instrument:  <b>{inst}</b>\n'
        f'Order Type:  BUY LIMIT\n'
        f'Entry Price: <b>{_fmt(inst, entry)}</b>\n'
        f'Stop Loss:   {_fmt(inst, sl)}\n'
        f'Take Profit: {_fmt(inst, tp)}\n'
        f'Position:    <b>{size:.2f}%</b>\n'
        f'{f"Reason: {reason}" if reason else ""}'
    )


def format_sell_limit_alert(inst, entry, sl, tp, size, reason=''):
    return (
        f'📤 <b>[SELL LIMIT PLACED]</b>\n\n'
        f'Instrument:  <b>{inst}</b>\n'
        f'Order Type:  SELL LIMIT\n'
        f'Entry Price: <b>{_fmt(inst, entry)}</b>\n'
        f'Stop Loss:   {_fmt(inst, sl)}\n'
        f'Take Profit: {_fmt(inst, tp)}\n'
        f'Position:    <b>{size:.2f}%</b>\n'
        f'{f"Reason: {reason}" if reason else ""}'
    )


def format_buy_stop_alert(inst, entry, sl, tp, size, reason=''):
    return (
        f'🔼 <b>[BUY STOP PLACED]</b>\n\n'
        f'Instrument:  <b>{inst}</b>\n'
        f'Order Type:  BUY STOP\n'
        f'Entry Price: <b>{_fmt(inst, entry)}</b>\n'
        f'Stop Loss:   {_fmt(inst, sl)}\n'
        f'Take Profit: {_fmt(inst, tp)}\n'
        f'Position:    <b>{size:.2f}%</b>\n'
        f'{f"Reason: {reason}" if reason else ""}'
    )


def format_sell_stop_alert(inst, entry, sl, tp, size, reason=''):
    return (
        f'🔽 <b>[SELL STOP PLACED]</b>\n\n'
        f'Instrument:  <b>{inst}</b>\n'
        f'Order Type:  SELL STOP\n'
        f'Entry Price: <b>{_fmt(inst, entry)}</b>\n'
        f'Stop Loss:   {_fmt(inst, sl)}\n'
        f'Take Profit: {_fmt(inst, tp)}\n'
        f'Position:    <b>{size:.2f}%</b>\n'
        f'{f"Reason: {reason}" if reason else ""}'
    )


def format_order_cancelled_alert(inst, order_type, price, reason=''):
    return (
        f'❌ <b>[ORDER CANCELLED]</b>\n\n'
        f'Instrument:  <b>{inst}</b>\n'
        f'Order Type:  {order_type}\n'
        f'Cancelled At: {_fmt(inst, price)}\n'
        f'{f"Reason: {reason}" if reason else ""}'
    )


def format_order_modified_alert(inst, mod_type, old_val, new_val, reason=''):
    return (
        f'✏️ <b>[ORDER MODIFIED]</b>\n\n'
        f'Instrument:   <b>{inst}</b>\n'
        f'Modification: {mod_type}\n'
        f'Old Value:    {old_val}\n'
        f'New Value:    <b>{new_val}</b>\n'
        f'{f"Reason: {reason}" if reason else ""}'
    )


def _format_entry_alert(alert: dict) -> str:
    inst = alert['instrument']
    return (
        f'🎯 <b>ENTRY HIT — {inst}</b>\n\n'
        f'Direction:   <b>{alert["direction"]}</b>\n'
        f'Entry Price: <b>{_fmt(inst, alert["entry"])}</b>\n'
        f'Stop Loss:   {_fmt(inst, alert["sl"])}\n'
        f'Take Profit: {_fmt(inst, alert["tp"])}\n'
        f'Current:     {_fmt(inst, alert["price"])}\n\n'
        f'<i>Trade is now active. Monitoring SL/TP...</i>'
    )


def _format_tp_alert(alert: dict) -> str:
    inst = alert['instrument']
    return (
        f'✅ <b>TAKE PROFIT HIT — {inst}</b>\n\n'
        f'Direction: <b>{alert["direction"]}</b>\n'
        f'TP Price:  <b>{_fmt(inst, alert["tp"])}</b>\n'
        f'Exit:      {_fmt(inst, alert["price"])}\n\n'
        f'🏆 <b>RESULT: WIN</b>'
    )


def _format_sl_alert(alert: dict, study: dict = None) -> str:
    inst = alert['instrument']
    notes_line = ''
    if study and study.get('notes'):
        notes = '\n'.join(f'• {n}' for n in study['notes'])
        notes_line = f'\n\n🧠 <b>BRAIN ANALYSIS:</b>\n{notes}'
    return (
        f'❌ <b>STOP LOSS HIT — {inst}</b>\n\n'
        f'Direction: <b>{alert["direction"]}</b>\n'
        f'SL Price:  <b>{_fmt(inst, alert["sl"])}</b>\n'
        f'Exit:      {_fmt(inst, alert["price"])}\n\n'
        f'📉 <b>RESULT: LOSS</b>'
        f'{notes_line}'
    )


def _format_trade_closed_win(inst, direction, entry, exit_price, pips, pnl):
    return (
        f'🟢 <b>[TRADE CLOSED — WIN]</b>\n\n'
        f'Instrument: <b>{inst}</b>\n'
        f'Direction:  {direction}\n'
        f'Entry:      {_fmt(inst, entry)}\n'
        f'Exit:       <b>{_fmt(inst, exit_price)}</b> (TP hit)\n'
        f'Pips:       <b>+{pips:.1f}</b>\n'
        f'P&L:        <b>+${pnl:.2f}</b>\n\n'
        f'Risk/Reward: Achieved ✅'
    )


def _format_trade_closed_loss(inst, direction, entry, exit_price, pips, pnl):
    return (
        f'🔴 <b>[TRADE CLOSED — LOSS]</b>\n\n'
        f'Instrument: <b>{inst}</b>\n'
        f'Direction:  {direction}\n'
        f'Entry:      {_fmt(inst, entry)}\n'
        f'Exit:       <b>{_fmt(inst, exit_price)}</b> (SL hit)\n'
        f'Pips:       <b>{pips:.1f}</b>\n'
        f'P&L:        <b>-${abs(pnl):.2f}</b>\n\n'
        f'⚠️ Loss analysis triggered...'
    )


# ════════════════════════════════════════════════════════════════
# LOSS DIAGNOSTIC ALERT
# ════════════════════════════════════════════════════════════════

def format_loss_diagnostic(
    inst, direction, pnl, pips, cfr_score,
    similar_wins, similar_losses,
    key_differences, corrections,
    pattern_detected
):
    diff_lines = ''
    for d in key_differences[:3]:
        diff_lines += (
            f'  {d["signal"]}: '
            f'{d["loss_value"]} (loss) vs '
            f'{d["win_value"]} (win)\n'
        )

    correction_lines = ''
    for i, c in enumerate(corrections[:3], 1):
        correction_lines += f'{i}. {c[:80]}\n'

    pattern_line = ''
    if pattern_detected:
        pattern_line = (
            '\n⚠️ <b>PATTERN DETECTED</b>\n'
            'This loss type has occurred before.\n'
            'CFR threshold adjustment applied.\n'
        )

    return (
        f'🔴 <b>LOSS DIAGNOSTIC REPORT</b>\n\n'
        f'Trade:      {inst} {direction}\n'
        f'P&L:        <b>{pnl:+.2f}</b> ({pips:+.1f} pips)\n'
        f'CFR Score:  {cfr_score:.3f}\n\n'
        f'Similar Wins:   {similar_wins}\n'
        f'Similar Losses: {similar_losses}\n\n'
        f'<b>KEY DIFFERENCES:</b>\n'
        f'<code>{diff_lines}</code>\n'
        f'<b>SELF-CORRECTIONS:</b>\n'
        f'{correction_lines}'
        f'{pattern_line}'
    )


# ════════════════════════════════════════════════════════════════
# WEEKLY LOSS PATTERN REPORT
# ════════════════════════════════════════════════════════════════

def format_weekly_report(
    week_trades, week_wins, week_losses,
    week_pnl, win_rate
):
    pnl_emoji = '🟢' if week_pnl > 0 else '🔴'
    return (
        f'📊 <b>WEEKLY LOSS PATTERN REPORT</b>\n\n'
        f'Trades:    {week_trades}\n'
        f'Wins:      {week_wins}\n'
        f'Losses:    {week_losses}\n'
        f'Win Rate:  <b>{win_rate}%</b>\n'
        f'Week P&L:  {pnl_emoji} <b>{week_pnl:+.2f}</b>\n'
    )


# ════════════════════════════════════════════════════════════════
# SIGNAL ALERT — pushed automatically when signal fires
# ════════════════════════════════════════════════════════════════

def format_signal_alert(
    inst, direction, bias_score, confluence,
    entry, sl, tp, cfr_score, kelly_size,
    order_type, reasons=None
):
    reason_line = ''
    if reasons:
        reason_line = (
            f'Reasons:     {" · ".join(reasons[:3])}\n'
        )
    direction_emoji = '🟢' if 'BUY' in direction or direction == 'LONG' else '🔴'
    return (
        f'{direction_emoji} <b>SIGNAL — {inst}</b>\n\n'
        f'Order Type:  <b>{order_type}</b>\n'
        f'Direction:   <b>{direction}</b>\n'
        f'Entry:       <b>{_fmt(inst, entry)}</b>\n'
        f'Stop Loss:   {_fmt(inst, sl)}\n'
        f'Take Profit: {_fmt(inst, tp)}\n\n'
        f'Bias Score:  {bias_score:.1f}/100\n'
        f'Confluence:  {confluence:.1f}%\n'
        f'CFR Score:   {cfr_score:.3f}\n'
        f'Kelly Size:  {kelly_size:.2f}%\n'
        f'{reason_line}'
    )


# ════════════════════════════════════════════════════════════════
# PRICE MONITOR LOOP — runs every 10 seconds
# ════════════════════════════════════════════════════════════════

async def _monitor_loop(app: Application):
    """
    Background loop that checks active signal levels
    every 10 seconds and sends alerts when levels are hit.
    """
    logger.info('Signal monitor loop started')
    while True:
        try:
            from signals.engine import check_signal_levels
            alerts = check_signal_levels()

            for alert in alerts:
                alert_type = alert.get('type')
                if alert_type == 'ENTRY_HIT':
                    msg = _format_entry_alert(alert)
                elif alert_type == 'TP_HIT':
                    msg = _format_tp_alert(alert)
                elif alert_type == 'SL_HIT':
                    # Try to get brain study for this loss
                    study = None
                    try:
                        from memory.fingerprint import (
                            MemoryFingerprintStore
                        )
                        store = MemoryFingerprintStore()
                        fp_id = store.save_trade_fingerprint(
                            instrument  = alert['instrument'],
                            direction   = alert['direction'],
                            entry_state = alert,
                            exit_state  = alert,
                            cfr_score   = 0.0,
                            kelly_size  = 0.0,
                            pnl         = alert.get('pnl', 0),
                            pips        = alert.get('pips', 0),
                            outcome     = 'loss',
                            order_type  = 'LIMIT',
                        )
                        analysis = store.analyze_loss(fp_id)
                        study    = analysis
                    except Exception:
                        pass
                    msg = _format_sl_alert(alert, study)
                else:
                    continue

                await send_alert(msg)

        except Exception as e:
            logger.error(f'Monitor loop error: {e}')

        await asyncio.sleep(10)


# ════════════════════════════════════════════════════════════════
# HALF-LIFE ALERT LOOP — checks signal health every 6 hours
# ════════════════════════════════════════════════════════════════

async def _halflife_monitor_loop():
    """Fires Telegram alert if any signal degrades below threshold."""
    while True:
        try:
            from memory.half_life import (
                SignalHalfLifeTracker, TRACKED_SIGNALS
            )
            tracker = SignalHalfLifeTracker()
            health  = tracker.get_signal_health()

            for sig_id, data in health['signals'].items():
                if data.get('alert') and data.get('correlation') is not None:
                    cfg = TRACKED_SIGNALS.get(sig_id, {})
                    msg = tracker.format_alert_telegram(
                        sig_id,
                        data['correlation'],
                        data.get('degradation_pct', 0),
                    )
                    await send_alert(msg)

        except Exception as e:
            logger.error(f'Half-life monitor error: {e}')

        # Check every 6 hours
        await asyncio.sleep(21600)


# ════════════════════════════════════════════════════════════════
# DARK POOL ALERT LOOP — checks every 4 hours
# ════════════════════════════════════════════════════════════════

async def _darkpool_monitor_loop():
    """Fires Telegram alert if dark pool Z-score exceeds 2.0σ."""
    while True:
        try:
            from ingestion.darkpool import scan_dark_pools
            data = scan_dark_pools()

            if data.get('anomalies_found', 0) > 0:
                for anomaly in data.get('anomalies', []):
                    msg = (
                        f'👻 <b>[DARK POOL ANOMALY]</b>\n\n'
                        f'Symbol:      <b>{anomaly["symbol"]}</b>\n'
                        f'Z-Score:     <b>{anomaly["z_score"]:+.2f}σ</b>\n'
                        f'Direction:   {anomaly["direction"]}\n'
                        f'Instrument:  {anomaly["instrument"]}\n'
                        f'Lead Window: 12–72 hours\n\n'
                        f'<i>Institutional positioning detected</i>'
                    )
                    await send_alert(msg)

        except Exception as e:
            logger.error(f'Dark pool monitor error: {e}')

        await asyncio.sleep(14400)


# ════════════════════════════════════════════════════════════════
# COMMANDS
# ════════════════════════════════════════════════════════════════

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        '⚡ <b>OMNINEXUS v2 — ONLINE</b>\n\n'
        'Synthetic World-Mirror Engine\n'
        'XAUUSD · GBPUSD · GBPJPY\n\n'
        'Powered by Twelve Data + FRED + Finnhub\n\n'
        'Type /status for system health.\n'
        'Type /signal for live signals.\n'
        'Type /help for all commands.',
        parse_mode='HTML',
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        '⚡ <b>OMNINEXUS COMMAND LIST</b>\n\n'
        '<b>MARKET DATA</b>\n'
        '/status    — System health + live prices\n'
        '/prices    — Live prices all pairs\n'
        '/signal    — Signal analysis (select pair)\n'
        '/signals   — All pair signals + levels\n\n'
        '<b>ACTIVE SIGNALS</b>\n'
        '/active    — Current pending signals\n'
        '/losses    — Recent SL brain studies\n\n'
        '<b>INTELLIGENCE</b>\n'
        '/regime    — Market regime report\n'
        '/halflife  — Signal health report\n'
        '/darkpool  — Dark pool Z-scores\n'
        '/memory    — Episodic memory matches\n'
        '/history   — Historical data status\n'
        '/api       — API usage stats\n\n'
        '<b>CHALLENGE MODE</b>\n'
        '/challenge        — Start challenge\n'
        '/challenge_status — Live P&L dashboard\n'
        '/challenge_stop   — End challenge\n\n'
        '<b>SYSTEM</b>\n'
        '/kill      — Emergency stop all signals\n',
        parse_mode='HTML',
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    try:
        from data.market_data import (
            get_all_prices, get_market_status,
            get_api_usage, _streamer
        )
        prices  = get_all_prices()
        market  = get_market_status()
        api_use = get_api_usage()
        ws_status = 'CONNECTED' if _streamer.connected else 'RECONNECTING'

        price_lines = ''
        for inst, price in prices.items():
            dec = 2 if inst == 'XAUUSD' else 5
            val = f'{price:.{dec}f}' if price else 'LOADING...'
            price_lines += f'{inst}:    <b>{val}</b>\n'

        challenge_line = ''
        if config.CHALLENGE_ACTIVE:
            challenge_line = f'\n⚡ Challenge Mode: <b>ACTIVE</b>\n'

        await update.message.reply_text(
            f'📊 <b>SYSTEM STATUS</b>\n'
            f'<code>{now} UTC</code>\n\n'
            f'Engine:      ACTIVE\n'
            f'WebSocket:   {ws_status}\n'
            f'Session:     {market["session"]}\n'
            f'Market Open: {"YES" if market["is_open"] else "NO"}\n'
            f'{challenge_line}\n'
            f'<b>LIVE PRICES</b>\n'
            f'{price_lines}\n'
            f'<b>API BUDGET</b>\n'
            f'Used today:  {api_use["calls_today"]}/'
            f'{api_use["daily_budget"]}\n'
            f'Remaining:   {api_use["remaining"]}\n'
            f'Used:        {api_use["pct_used"]}%\n\n'
            f'<i>Signal data: Twelve Data + FRED + Finnhub</i>',
            parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(
            f'📊 <b>SYSTEM STATUS</b>\n'
            f'<code>{now} UTC</code>\n\n'
            f'Engine: ACTIVE\n'
            f'Live data loading: {e}\n\n'
            f'<i>Run /signal to force refresh</i>',
            parse_mode='HTML',
        )


async def prices_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        from data.market_data import get_live_price
        now   = datetime.utcnow().strftime('%H:%M:%S')
        lines = [f'💰 <b>LIVE PRICES</b>\n<code>{now} UTC</code>\n']
        for inst in config.INSTRUMENTS:
            data = get_live_price(inst)
            dec  = 2 if inst == 'XAUUSD' else 5
            if data:
                price  = f'{data["price"]:.{dec}f}'
                source = data.get('source', 'ws')
                lines.append(
                    f'<b>{inst}</b>: {price} '
                    f'<code>[{source}]</code>'
                )
            else:
                lines.append(f'<b>{inst}</b>: LOADING...')
        await update.message.reply_text(
            '\n'.join(lines), parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(f'❌ Price error: {e}')


async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """Asks user to choose a pair — saves API credits."""
    keyboard = [
        [InlineKeyboardButton('🥇 XAUUSD (Gold)', callback_data='sig_XAUUSD')],
        [
            InlineKeyboardButton('💷 GBPUSD', callback_data='sig_GBPUSD'),
            InlineKeyboardButton('⚡ GBPJPY', callback_data='sig_GBPJPY'),
        ],
        [InlineKeyboardButton('📡 ALL PAIRS (3x credits)', callback_data='sig_ALL')],
    ]
    await update.message.reply_text(
        '📡 <b>SELECT PAIR FOR SIGNAL</b>\n\n'
        'Choose one pair to save API credits.\n'
        'ALL PAIRS uses 3× more requests.',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def signal_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()
    choice = query.data

    if choice == 'sig_ALL':
        instruments = config.INSTRUMENTS
        await query.edit_message_text(
            '🔍 Analysing all 3 pairs... please wait.\n'
            '<i>(Uses ~12 API credits)</i>',
            parse_mode='HTML',
        )
    else:
        instruments = [choice.replace('sig_', '')]
        await query.edit_message_text(
            f'🔍 Analysing {instruments[0]}... please wait.\n'
            f'<i>(Uses ~4 API credits)</i>',
            parse_mode='HTML',
        )

    try:
        from signals.engine import calculate_signal, format_signal_message
        from data.market_data import get_api_usage

        for inst in instruments:
            sig = calculate_signal(inst)
            msg = format_signal_message(sig)
            await context.bot.send_message(
                chat_id    = query.message.chat_id,
                text       = msg,
                parse_mode = 'HTML',
            )
            await asyncio.sleep(0.3)

        usage = get_api_usage()
        await context.bot.send_message(
            chat_id = query.message.chat_id,
            text    = (
                f'<i>API budget: '
                f'{usage["remaining"]} requests remaining today</i>'
            ),
            parse_mode='HTML',
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id    = query.message.chat_id,
            text       = f'❌ Signal error: {e}',
            parse_mode = 'HTML',
        )


async def signals_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await signal_command(update, context)


async def active_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        from signals.engine import get_active_signals_summary
        msg = get_active_signals_summary()
        await update.message.reply_text(
            f'📌 <b>ACTIVE SIGNALS</b>\n\n{msg}',
            parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(f'❌ Error: {e}')


async def losses_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """Shows last 3 SL studies from brain analysis."""
    try:
        study_file = (
            Path(os.path.dirname(os.path.abspath(__file__)))
            .parent / 'signals' / 'loss_studies.json'
        )

        if not study_file.exists():
            await update.message.reply_text(
                '🧠 No loss studies yet.\n'
                'Brain studies each SL hit automatically.',
                parse_mode='HTML',
            )
            return

        with open(study_file, 'r') as f:
            studies = json.load(f)

        if not studies:
            await update.message.reply_text('🧠 No loss studies yet.')
            return

        lines = ['🧠 <b>RECENT LOSS STUDIES</b>\n']
        for study in studies[-3:]:
            notes = '\n'.join(
                f'  • {n}' for n in study.get('notes', [])
            )
            lines.append(
                f'<b>{study["instrument"]} '
                f'{study.get("direction","?")}</b>\n'
                f'{study.get("timestamp","")[:16]}\n'
                f'{notes}\n'
            )
        await update.message.reply_text(
            '\n'.join(lines), parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(f'🧠 Loss studies error: {e}')


async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        from data.history import get_history_stats
        stats = get_history_stats()

        if not stats:
            await update.message.reply_text(
                '📚 No history files yet.\n'
                'History downloads automatically on startup.'
            )
            return

        lines = ['📚 <b>HISTORICAL DATA</b>\n']
        for name, info in sorted(stats.items()):
            if 'error' in info:
                lines.append(f'❌ {name}: corrupted')
            else:
                lines.append(
                    f'<b>{name}</b>: '
                    f'{info["bars"]} bars | '
                    f'{info["from"]} → {info["to"]} | '
                    f'{info["size_kb"]} KB'
                )

        await update.message.reply_text(
            '\n'.join(lines),
            parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(f'❌ Error: {e}')

async def api_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        from data.market_data import get_api_usage
        usage = get_api_usage()
        await update.message.reply_text(
            f'📡 <b>API USAGE</b>\n\n'
            f'Used today:  {usage["calls_today"]}/'
            f'{usage["daily_budget"]}\n'
            f'Remaining:   <b>{usage["remaining"]}</b>\n'
            f'Used:        {usage["pct_used"]}%\n\n'
            f'WebSocket:   Live prices (0 credits)\n'
            f'Finnhub:     News + Sentiment (60/min)\n'
            f'FRED:        Yields (unlimited)\n'
            f'yfinance:    History (unlimited)',
            parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(f'❌ API error: {e}')


async def regime_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        from brain.autoencoder import AutoencoderRegimeDetector
        detector = AutoencoderRegimeDetector()
        obs      = detector.build_observation()
        result   = detector.detect_anomaly(obs)

        from brain.regime_transfer import RegimeTransfer
        rt    = RegimeTransfer()
        match = rt.match_state({
            'real_yield':     2.18,
            'friction_score': 100.0,
            'gold_bias':      48.0,
            'gbp_bias':       48.0,
        })

        policy = match.get('inherited_policy', {})

        await update.message.reply_text(
            f'🔬 <b>REGIME REPORT</b>\n\n'
            f'Recon Error:  <b>{result["reconstruction_error"]}</b>\n'
            f'Threshold:    {result["threshold"]}\n'
            f'Is Anomaly:   <b>{result["is_anomaly"]}</b>\n'
            f'Regime:       <b>{result["regime"]}</b> {result["emoji"]}\n'
            f'Grey Zone:    {result["grey_zone"]}\n\n'
            f'<b>REGIME TRANSFER MATCH:</b>\n'
            f'Matched:      {match.get("matched_regime","None")}\n'
            f'Similarity:   {match.get("similarity",0):.4f}\n'
            f'Policy:       {policy.get("direction","?")} '
            f'{policy.get("instrument","?")}',
            parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(
            f'🔬 <b>REGIME REPORT</b>\n\nError: {e}',
            parse_mode='HTML',
        )


async def halflife_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        from memory.half_life import SignalHalfLifeTracker
        tracker = SignalHalfLifeTracker()
        health  = tracker.get_signal_health()
        await update.message.reply_text(
            tracker.format_halflife_telegram(health),
            parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(
            f'📉 <b>SIGNAL HALF-LIFE</b>\n\nError: {e}',
            parse_mode='HTML',
        )


async def darkpool_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        from ingestion.darkpool import scan_dark_pools
        data    = scan_dark_pools()
        results = data.get('results', {})

        lines = ['👻 <b>DARK POOL REPORT</b>\n']
        for symbol, info in results.items():
            z     = info.get('z_score', 0)
            anom  = ' ⚠️' if info.get('is_anomaly') else ''
            source= ' [SIM]' if info.get('data_source') == 'SIMULATED' else ''
            lines.append(f'{symbol}: <b>{z:+.2f}σ</b>{anom}{source}')

        lines.append(
            f'\nAnomalies:  {data.get("anomalies_found", 0)}\n'
            f'Threshold:  {data.get("alert_threshold", 2.0)}σ\n'
            f'Lead time:  12–72 hours'
        )
        await update.message.reply_text(
            '\n'.join(lines), parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(
            f'👻 <b>DARK POOL REPORT</b>\n\n'
            f'Ingestion loading...\n<i>{e}</i>',
            parse_mode='HTML',
        )


async def memory_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        from memory.fingerprint import MemoryFingerprintStore
        store  = MemoryFingerprintStore()
        counts = store.count()
        await update.message.reply_text(
            store.format_memory_telegram(top_n=5),
            parse_mode='HTML',
        )
    except Exception as e:
        await update.message.reply_text(
            f'🧠 <b>EPISODIC MEMORY</b>\n\n'
            f'Memory layer loading...\n<i>{e}</i>',
            parse_mode='HTML',
        )


async def kill_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    logger.critical('KILL SWITCH activated via Telegram')
    try:
        from signals.engine import _active_signals, _save_signals
        _active_signals.clear()
        _save_signals()
    except Exception:
        pass

    await update.message.reply_text(
        '🛑 <b>EMERGENCY STOP</b>\n\n'
        'All active signals cleared.\n'
        'Signal monitoring paused.\n\n'
        'Send /signal to re-activate.',
        parse_mode='HTML',
    )


# ════════════════════════════════════════════════════════════════
# CHALLENGE MODE
# ════════════════════════════════════════════════════════════════

async def challenge_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if config.CHALLENGE_ACTIVE:
        await update.message.reply_text(
            '⚠️ <b>Challenge already active.</b>\n'
            'Use /challenge_status or /challenge_stop.',
            parse_mode='HTML',
        )
        return ConversationHandler.END

    await update.message.reply_text(
        '🏆 <b>CHALLENGE MODE SETUP</b>\n\n'
        'Question 1 of 3:\n'
        '<b>Starting capital in USD?</b>\n'
        '<i>Example: 500</i>',
        parse_mode='HTML',
    )
    return CAPITAL


async def challenge_capital(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        capital = float(update.message.text.strip())
        if capital <= 0:
            raise ValueError
        context.user_data['challenge_capital'] = capital
        await update.message.reply_text(
            f'✅ Capital: <b>${capital:,.2f}</b>\n\n'
            f'Question 2 of 3:\n'
            f'<b>Profit target percentage?</b>\n'
            f'<i>Example: 10 for 10%</i>',
            parse_mode='HTML',
        )
        return TARGET
    except ValueError:
        await update.message.reply_text('❌ Enter a valid number. Example: 500')
        return CAPITAL


async def challenge_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        target = float(update.message.text.strip())
        if target <= 0 or target > 100:
            raise ValueError
        context.user_data['challenge_target'] = target
        await update.message.reply_text(
            f'✅ Target: <b>{target}%</b>\n\n'
            f'Question 3 of 3:\n'
            f'<b>How many days?</b>\n'
            f'<i>Example: 30</i>',
            parse_mode='HTML',
        )
        return DAYS
    except ValueError:
        await update.message.reply_text('❌ Enter a number between 1 and 100.')
        return TARGET


async def challenge_days(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:
        days = int(update.message.text.strip())
        if days <= 0:
            raise ValueError

        capital    = context.user_data['challenge_capital']
        target_pct = context.user_data['challenge_target']

        profit_target  = capital * (target_pct / 100)
        daily_target   = profit_target / days
        max_daily_loss = capital * config.MAX_DAILY_LOSS_PCT
        max_drawdown   = capital * config.MAX_TOTAL_DRAWDOWN_PCT

        config.CHALLENGE_ACTIVE     = True
        config.CHALLENGE_CAPITAL    = capital
        config.CHALLENGE_TARGET_PCT = target_pct
        config.CHALLENGE_DAYS       = days
        config.CHALLENGE_START_DATE = datetime.utcnow()
        save_challenge_state()

        await update.message.reply_text(
            f'🏆 <b>CHALLENGE ACTIVATED</b>\n\n'
            f'Capital:        <b>${capital:,.2f}</b>\n'
            f'Target:         <b>{target_pct}% = ${profit_target:,.2f}</b>\n'
            f'Duration:       <b>{days} days</b>\n'
            f'━━━━━━━━━━━━━━━━━━━━\n'
            f'Daily Target:   ${daily_target:,.2f}/day\n'
            f'Max Daily Loss: ${max_daily_loss:,.2f} (2%)\n'
            f'Max Drawdown:   ${max_drawdown:,.2f} (5%)\n'
            f'Min R:R:        1:2 enforced\n'
            f'━━━━━━━━━━━━━━━━━━━━\n\n'
            f'All signals now governed by challenge rules.\n'
            f'Use /challenge_status to track progress.',
            parse_mode='HTML',
        )
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text('❌ Enter a valid number of days.')
        return DAYS


async def challenge_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text('Challenge setup cancelled.')
    return ConversationHandler.END


async def challenge_status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not config.CHALLENGE_ACTIVE:
        await update.message.reply_text(
            '⚠️ No active challenge. Use /challenge to start.'
        )
        return

    now           = datetime.utcnow()
    elapsed       = (now - config.CHALLENGE_START_DATE).days + 1
    remaining     = max(0, config.CHALLENGE_DAYS - elapsed)
    profit_target = config.CHALLENGE_CAPITAL * (config.CHALLENGE_TARGET_PCT / 100)
    daily_target  = profit_target / config.CHALLENGE_DAYS

    try:
        from signals.engine import _signal_history
        wins     = sum(1 for s in _signal_history if s.get('result') == 'WIN')
        losses   = sum(1 for s in _signal_history if s.get('result') == 'LOSS')
        total    = wins + losses
        win_rate = round(wins / total * 100, 1) if total > 0 else 0
    except Exception:
        wins = losses = total = 0
        win_rate = 0

    await update.message.reply_text(
        f'🏆 <b>CHALLENGE STATUS</b>\n'
        f'Day {elapsed} of {config.CHALLENGE_DAYS}\n\n'
        f'Capital:         ${config.CHALLENGE_CAPITAL:,.2f}\n'
        f'Target:          {config.CHALLENGE_TARGET_PCT}%\n'
        f'Daily Target:    ${daily_target:,.2f}\n'
        f'Days Remaining:  {remaining}\n\n'
        f'<b>SIGNAL PERFORMANCE</b>\n'
        f'Total Signals:   {total}\n'
        f'Wins:            {wins}\n'
        f'Losses:          {losses}\n'
        f'Win Rate:        {win_rate}%\n\n'
        f'<i>P&L tracking adds when execution is connected.</i>',
        parse_mode='HTML',
    )


async def challenge_stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not config.CHALLENGE_ACTIVE:
        await update.message.reply_text('⚠️ No active challenge.')
        return

    config.CHALLENGE_ACTIVE = False
    save_challenge_state()
    if CHALLENGE_STATE_FILE.exists():
        CHALLENGE_STATE_FILE.unlink()

    await update.message.reply_text(
        '🏁 <b>CHALLENGE ENDED</b>\n\n'
        'Final report saved.\n'
        'Use /losses to review brain studies.',
        parse_mode='HTML',
    )


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    logger.info('Starting OmniNexus Telegram bot...')
    load_challenge_state()

    # Start live price stream
    try:
        from data.market_data import start_price_stream
        start_price_stream()
        logger.info('Price stream started')
    except Exception as e:
        logger.warning(f'Price stream start error: {e}')

    # Startup backtest in background
    try:
        from brain.brain_update import run_startup_backtest
        threading.Thread(
            target=run_startup_backtest,
            daemon=True,
        ).start()
        logger.info('Startup backtest running in background')
    except Exception as e:
        logger.warning(f'Startup backtest error: {e}')

    # Download history if needed
    try:
        from data.history import download_all_history
        threading.Thread(
            target=download_all_history,
            kwargs={'force_reload': False},
            daemon=True,
        ).start()
        logger.info('History download started in background')
    except Exception as e:
        logger.warning(f'History download error: {e}')

    app = Application.builder().token(
        config.TELEGRAM_BOT_TOKEN
    ).build()

    # Challenge conversation
    challenge_handler = ConversationHandler(
        entry_points=[CommandHandler('challenge', challenge_start)],
        states={
            CAPITAL: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, challenge_capital
            )],
            TARGET: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, challenge_target
            )],
            DAYS: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, challenge_days
            )],
        },
        fallbacks=[CommandHandler('cancel', challenge_cancel)],
    )

    # Register all handlers
    app.add_handler(CommandHandler('start',            start_command))
    app.add_handler(CommandHandler('help',             help_command))
    app.add_handler(CommandHandler('status',           status_command))
    app.add_handler(CommandHandler('prices',           prices_command))
    app.add_handler(CommandHandler('signal',           signal_command))
    app.add_handler(CommandHandler('signals',          signals_command))
    app.add_handler(CommandHandler('active',           active_command))
    app.add_handler(CommandHandler('losses',           losses_command))
    app.add_handler(CommandHandler('history',          history_command))
    app.add_handler(CommandHandler('api',              api_command))
    app.add_handler(CommandHandler('regime',           regime_command))
    app.add_handler(CommandHandler('halflife',         halflife_command))
    app.add_handler(CommandHandler('darkpool',         darkpool_command))
    app.add_handler(CommandHandler('memory',           memory_command))
    app.add_handler(CommandHandler('kill',             kill_command))
    app.add_handler(CommandHandler('challenge_status', challenge_status_command))
    app.add_handler(CommandHandler('challenge_stop',   challenge_stop_command))
    app.add_handler(CallbackQueryHandler(signal_callback, pattern='^sig_'))
    app.add_handler(challenge_handler)

    # Background loops
    async def post_init(application: Application):
        asyncio.create_task(_monitor_loop(application))
        asyncio.create_task(_halflife_monitor_loop())
        asyncio.create_task(_darkpool_monitor_loop())

        try:
            from data.market_data import auto_refresh_loop
            asyncio.create_task(auto_refresh_loop())
        except Exception as e:
            logger.warning(f'auto_refresh_loop error: {e}')

        try:
            from brain.brain_update import weekend_update_loop
            asyncio.create_task(weekend_update_loop())
        except Exception as e:
            logger.warning(f'weekend_update_loop error: {e}')

        try:
            from brain.event_interrupt import get_interrupt
            asyncio.create_task(
                get_interrupt().weekly_calendar_refresh_loop()
            )
        except Exception as e:
            logger.warning(f'event_interrupt error: {e}')

        logger.info('All background loops started')

    app.post_init = post_init

    logger.info('OmniNexus Telegram bot LIVE')
    app.run_polling()


if __name__ == '__main__':
    main()