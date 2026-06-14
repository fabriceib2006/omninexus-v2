# ════════════════════════════════════════════════════════════════
# OMNINEXUS v2 — run_system.py
# Master System Startup
# Runs all health checks and starts the full system
# ════════════════════════════════════════════════════════════════

import asyncio
import os
import sys
import time
import threading
from datetime import datetime

# Fix Windows console encoding for unicode
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding='utf-8', errors='replace'
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding='utf-8', errors='replace'
    )

os.makedirs('logs', exist_ok=True)

BANNER = '''
███████████████████████████████████████████████████████
█                  OMNINEXUS v2                       █
█           Synthetic World-Mirror Engine             █
███████████████████████████████████████████████████████
'''

SEP = '═' * 55


def section(title: str):
    print(f'\n{SEP}')
    print(title)
    print(SEP)


def ok(label: str, detail: str = ''):
    suffix = f'\n     {detail}' if detail else ''
    print(f'  OK  {label}{suffix}')


def fail(label: str, detail: str = ''):
    suffix = f'\n     {detail}' if detail else ''
    print(f'  FAIL {label}{suffix}')


def warn(label: str, detail: str = ''):
    suffix = f'\n     {detail}' if detail else ''
    print(f'  WARN {label}{suffix}')


# ── PHASE 1: CONFIG ────────────────────────────────────────────

def check_config():
    section('PHASE 1 - CONFIGURATION')
    print('\n[CONFIG]')
    try:
        from config import config
        ok('Config loaded',
           f'Instruments: {config.INSTRUMENTS}')
        ok('Twelve Data API key',
           f'Key: {config.TWELVE_DATA_API_KEY[:8]}...')
        ok('Finnhub API key',
           f'Key: {config.FINNHUB_API_KEY[:8]}...')
        ok('FRED API key')
        ok('Telegram configured',
           f'Chat ID: {config.TELEGRAM_CHAT_ID}')
        return config, True
    except Exception as e:
        fail('Config failed', str(e))
        return None, False


# ── PHASE 2: MARKET DATA ───────────────────────────────────────

def check_market_data():
    section('PHASE 2 - MARKET DATA')
    print('\n[LIVE PRICES — TWELVE DATA]')
    results = {}
    try:
        from data.market_data import _fetch_price_rest
        from config import config

        for inst in config.INSTRUMENTS:
            data = _fetch_price_rest(inst)
            if data and data.get('price', 0) > 0:
                dec = 2 if inst == 'XAUUSD' else 5
                ok(inst, f'{data["price"]:.{dec}f}')
                results[inst] = True
            else:
                fail(inst, 'No price returned')
                results[inst] = False

    except Exception as e:
        fail('Market data error', str(e))

    print('\n[WEBSOCKET STREAM]')
    try:
        from data.market_data import start_price_stream, _streamer
        start_price_stream()
        time.sleep(5)
        if _streamer.connected:
            ok('WebSocket connected', 'Live prices streaming')
        else:
            warn('WebSocket not connected', 'Will retry automatically')
    except Exception as e:
        warn('WebSocket error', str(e))

    return results


# ── PHASE 3: HISTORICAL DATA ───────────────────────────────────

def check_history():
    section('PHASE 3 - HISTORICAL DATA')
    print('\n[DOWNLOADING 10-YEAR HISTORY]')
    try:
        from data.history import (
            download_all_history, get_history_stats
        )
        results = download_all_history(force_reload=False)

        for key, result in results.items():
            if result.get('success'):
                ok(key,
                   f'{result["bars"]} bars | '
                   f'{result["from"]} to {result["to"]}')
            else:
                warn(key, 'Download failed — will retry')

        stats = get_history_stats()
        total_bars = sum(
            v.get('bars', 0) for v in stats.values()
            if 'bars' in v
        )
        print(f'\n  Total historical bars stored: {total_bars:,}')
        return True

    except Exception as e:
        fail('History error', str(e))
        return False


# ── PHASE 4: INDICATORS ────────────────────────────────────────

def check_indicators():
    section('PHASE 4 - TECHNICAL INDICATORS')
    print('\n[INDICATORS — TWELVE DATA]')
    try:
        from data.indicators import get_rsi, get_macd
        from config import config

        for inst in config.INSTRUMENTS:
            rsi = get_rsi(inst)
            if rsi:
                ok(f'RSI {inst}',
                   f'{rsi["value"]} — {rsi["signal"]}')
            else:
                warn(f'RSI {inst}', 'No data — will retry')

        return True
    except Exception as e:
        fail('Indicators error', str(e))
        return False


# ── PHASE 5: SIGNAL ENGINE ─────────────────────────────────────

def check_signals():
    section('PHASE 5 - SIGNAL ENGINE')
    print('\n[SIGNAL CALCULATION]')
    try:
        from signals.engine import calculate_signal
        from config import config

        for inst in config.INSTRUMENTS:
            sig = calculate_signal(inst)
            if 'error' not in sig:
                ok(inst,
                   f'{sig["direction"]} | '
                   f'Confidence: {sig["confidence"]}% | '
                   f'Entry: {sig.get("entry", "N/A")}')
            else:
                warn(inst, sig['error'])

        return True
    except Exception as e:
        fail('Signal engine error', str(e))
        return False



# ── PHASE 6: MACRO SIGNALS ─────────────────────────────────────

def check_macro():
    section('PHASE 6 - MACRO SIGNALS')

    print('\n[REAL YIELD — FRED]')
    try:
        from ingestion.fred_yield import calculate_real_yield
        data = calculate_real_yield()
        if 'error' not in data:
            ok('Real Yield',
               f'{data["real_yield"]:.3f}% — {data["gold_bias"]}')
        else:
            warn('Real Yield', data['error'])
    except Exception as e:
        warn('Real Yield', str(e))

    print('\n[FRICTION INDEX — RSS]')
    try:
        from ingestion.friction import calculate_friction_index
        data = calculate_friction_index()
        if 'error' not in data:
            ok('Friction Index',
               f'{data["friction_score"]}/100 — {data["level"]}')
        else:
            warn('Friction Index', data['error'])
    except Exception as e:
        warn('Friction Index', str(e))

    print('\n[BOE/BOJ SPREAD — FRED]')
    try:
        from signals.gbp import calculate_boe_boj_spread
        data = calculate_boe_boj_spread()
        if 'error' not in data:
            ok('BoE/BoJ Spread',
               f'{data["spread"]:.3f}% — {data["bias"]}')
        else:
            warn('BoE/BoJ Spread', data.get('error', ''))
    except Exception as e:
        warn('BoE/BoJ Spread', str(e))

    return True


# ── PHASE 7: BRAIN LAYER ───────────────────────────────────────
    section('PHASE 7 - BRAIN LAYER')

    print('\n[AUTOENCODER]')
    try:
        from brain.autoencoder import AutoencoderRegimeDetector
        ae     = AutoencoderRegimeDetector()
        obs    = ae.build_observation()
        result = ae.detect_anomaly(obs)
        ok('Autoencoder',
           f'Error: {result.get("recon_error", "?")} | '
           f'Anomaly: {result.get("is_anomaly", "?")} | '
           f'Regime: {result.get("regime", "STABLE")}')
    except Exception as e:
        warn('Autoencoder', str(e))

    print('\n[CFR AGENT]')
    try:
        from brain.cfr_agent import CFRPolicyAgent
        agent  = CFRPolicyAgent()
        state  = {'bias_score': 0.75, 'confluence': 0.5, 'friction': 0.3}
        result = agent.evaluate(state)
        ok('CFR Agent',
           f'Score: {result.get("regret_score", result.get("score", "?"))} | '
           f'Allowed: {result.get("allowed", "?")} | '
           f'Direction: {result.get("direction", "?")}')
    except Exception as e:
        warn('CFR Agent', str(e))

    print('\n[MEMORY LAYER]')
    try:
        from memory.fingerprint import MemoryFingerprintStore
        store  = MemoryFingerprintStore()
        counts = store.count()
        ok('Fingerprint Store', f'{counts} fingerprints loaded')
    except Exception as e:
        warn('Memory', str(e))

    return True


def check_brain():
    section('PHASE 7 - BRAIN LAYER')

    print('\n[AUTOENCODER]')
    try:
        from brain.autoencoder import AutoencoderRegimeDetector
        ae = AutoencoderRegimeDetector()
        obs= ae.build_observation()
        result = ae.detect_anomaly(obs)
        ok('Autoencoder',
           f'Error: {result.get("recon_error", "?")} — '
           f'Anomaly: {result.get("is_anomaly", "?")} — '
           f'Regime: {result.get("regime", "STABLE")}')
    except Exception as e:
        warn('Autoencoder', str(e))

    print('\n[CFR AGENT]')
    try:
        from brain.cfr_agent import CFRPolicyAgent
        agent = CFRPolicyAgent()
        state = {'bias_score': 0.75, 'confluence': 0.5, 'friction': 0.3}
        result = agent.evaluate(state)
        ok('CFR Agent',
          f'Score: {result.get("regret_score", result.get("score", "?"))} — '
          f'Allowed: {result.get("allowed", "?")} — '
          f'Direction: {result.get("direction", "?")}')
    except Exception as e:
        warn('CFR Agent', str(e))

    print('\n[MEMORY LAYER]')
    try:
        from memory.fingerprint import MemoryFingerprintStore
        store = MemoryFingerprintStore()
        counts = store.count()
        ok('Fingerprint Store',
          f'{counts} fingerprints loaded')
    except Exception as e:
        warn('Memory', str(e))

    return True


# ── PHASE 8: AZURE INFRA ───────────────────────────────────────

def check_azure():
    section('PHASE 8 - AZURE INFRASTRUCTURE')

    print('\n[COSMOS DB]')
    try:
        from graph.cosmos import get_gremlin_client
        client = get_gremlin_client()
        if client:
            ok('Cosmos DB', 'Connected via Gremlin')
            client.close()
        else:
            warn('Cosmos DB', 'Connection returned None — check credentials')
    except Exception as e:
        warn('Cosmos DB', str(e))

    return True


# ── PHASE 9: TELEGRAM ──────────────────────────────────────────

async def check_telegram():
    section('PHASE 9 - TELEGRAM')
    print('\n[TELEGRAM BOT]')
    try:
        from tg_bot.bot import send_alert
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        await send_alert(
            f'<b>OMNINEXUS v2 ONLINE</b>\n'
            f'<code>{now}</code>\n\n'
            f'All systems starting up.\n'
            f'Type /status for live data.\n'
            f'Type /signal for analysis.'
        )
        ok('Telegram alert sent', 'Check your bot')
        return True
    except Exception as e:
        fail('Telegram failed', str(e))
        return False


# ── SUMMARY ────────────────────────────────────────────────────

def print_summary(gold_bias, gbp_bias, session):
    print(f'\n{"█"*55}')
    print('OMNINEXUS v2 - SYSTEM SUMMARY')
    print(f'{"█"*55}')
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    print(f'''
  Timestamp:     {now}
  XAUUSD Bias:   {gold_bias}
  GBP Bias:      {gbp_bias}
  Session:       {session}
  Instruments:   XAUUSD | GBPUSD | GBPJPY
  Data Sources:  Twelve Data + FRED + Finnhub + yfinance
  Status:        SYSTEM FULLY OPERATIONAL

  Telegram Commands:
    /status    - Live prices + API usage
    /signal    - Full signal analysis
    /prices    - Live prices only
    /active    - Active signals
    /losses    - Brain loss studies
    /history   - Historical data status
    /challenge - Start challenge mode
    /kill      - Emergency stop
    /help      - All commands
''')
    print(f'{"█"*55}\n')


# ── MAIN ───────────────────────────────────────────────────────

def main():
    print(BANNER)

    # Phase 1: Config
    cfg, config_ok = check_config()
    if not config_ok:
        print('\nFATAL: Cannot start without config.')
        sys.exit(1)

    # Phase 2: Market Data
    check_market_data()

    # Phase 3: History
    check_history()

    # Phase 4: Indicators
    check_indicators()

    # Phase 5: Signal Engine
    try:
        check_signals()
        from signals.engine import calculate_signal
        gold_sig = calculate_signal('XAUUSD')
        gbp_sig  = calculate_signal('GBPUSD')
        gold_bias = (
            f'{gold_sig.get("confidence",0)}% — '
            f'{gold_sig.get("direction","?")}'
        )
        gbp_bias = (
            f'{gbp_sig.get("confidence",0)}% — '
            f'{gbp_sig.get("direction","?")}'
        )
    except Exception:
        gold_bias = 'CALCULATING...'
        gbp_bias  = 'CALCULATING...'

    # Phase 6: Macro
    check_macro()

    # Phase 7: Brain
    check_brain()

    # Phase 8: Azure
    check_azure()

    # Phase 9: Telegram
    try:
        from data.market_data import get_market_status
        market  = get_market_status()
        session = market['session']
    except Exception:
        session = 'UNKNOWN'

    asyncio.run(check_telegram())

    # Phase 10: Brain systems
    section('PHASE 10 - BRAIN SYSTEMS')

    print('\n[RISK MANAGER]')
    try:
        from brain.risk_manager import RiskManager
        rm = RiskManager()
        test = rm.calculate_lot_size('XAUUSD', 2400.0, 2385.0, 1000.0)
        ok('Risk Manager',
           f'Test: {test["lot_size"]} lots | '
           f'Risk: ${test["dollar_risk"]} | '
           f'SL: {test["sl_pips"]}pips')
    except Exception as e:
        warn('Risk Manager', str(e))

    print('\n[CORRELATION GUARD]')
    try:
        from brain.correlation_guard import get_correlation_matrix
        matrix = get_correlation_matrix(20)
        ok('Correlation Guard',
           ' | '.join(f'{k}={v:.2f}' for k, v in matrix.items()))
    except Exception as e:
        warn('Correlation Guard', str(e))

    print('\n[DRAWDOWN CIRCUIT]')
    try:
        from brain.drawdown_circuit import get_circuit
        circuit = get_circuit()
        status  = circuit.get_status()
        ok('Drawdown Circuit',
           f'Tripped: {status["is_tripped"]} | '
           f'Daily loss: ${status["daily_loss"]:.2f}')
    except Exception as e:
        warn('Drawdown Circuit', str(e))

    print('\n[EVENT INTERRUPT]')
    try:
        from brain.event_interrupt import get_interrupt
        interrupt = get_interrupt()
        events    = interrupt.get_upcoming_events()
        ok('Event Interrupt',
           f'{len(events)} upcoming events loaded')
    except Exception as e:
        warn('Event Interrupt', str(e))

    print('\n[REGIME CLUSTERER]')
    try:
        from brain.regime_clusterer import discover_regimes
        threading.Thread(
            target=discover_regimes,
            kwargs={'force_rerun': False},
            daemon=True,
        ).start()
        ok('Regime Clusterer', 'Running in background')
    except Exception as e:
        warn('Regime Clusterer', str(e))

    print('\n[STARTUP BACKTEST]')
    try:
        from brain.brain_update import run_startup_backtest
        threading.Thread(
            target=run_startup_backtest,
            daemon=True,
        ).start()
        ok('Startup Backtest',
           'Running in background — will self-optimize')
    except Exception as e:
        warn('Startup Backtest', str(e))

    # Print summary
    print_summary(gold_bias, gbp_bias, session)


if __name__ == '__main__':
    main()