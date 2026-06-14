# ════════════════════════════════════════════════════════════════
# OMNINEXUS — signals/gbp.py
# GBP Signal Aggregator
# Aggregates all GBPUSD and GBPJPY signals
# BoE/BoJ spread + Session detector + Dark Pool + Behavioral
# Writes results to Cosmos DB World Graph
# ════════════════════════════════════════════════════════════════

import logging
import requests
from datetime import datetime
from typing import Optional
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.signals.gbp')

# ── BOE/BOJ SPREAD FETCHER ─────────────────────────────────────
# UK 10Y Gilt yield vs Japan 10Y JGB yield
# Spread expansion = GBPJPY bullish (carry trade)
# Spread compression = GBPJPY bearish

BOE_YIELD_SERIES  = 'IRLTLT01GBM156N'  # UK 10Y Government Bond
BOJ_YIELD_SERIES  = 'IRLTLT01JPM156N'  # Japan 10Y Government Bond
FRED_BASE_URL     = 'https://api.stlouisfed.org/fred/series/observations'


def fetch_yield(series_id: str) -> Optional[float]:
    """Fetches latest yield value from FRED."""
    try:
        params = {
            'series_id':  series_id,
            'api_key':    config.FRED_API_KEY,
            'file_type':  'json',
            'sort_order': 'desc',
            'limit':      3,
        }
        response = requests.get(
            FRED_BASE_URL,
            params=params,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        for obs in data.get('observations', []):
            if obs['value'] != '.':
                return float(obs['value'])
        return None

    except Exception as e:
        logger.error(f'FRED yield fetch error {series_id}: {e}')
        return None


def calculate_boe_boj_spread() -> dict:
    """
    Calculates BoE/BoJ yield spread.
    Positive spread = UK yields above Japan = carry trade
    supports GBPJPY upside.
    """
    logger.info('Fetching BoE/BoJ yield spread...')

    boe_yield = fetch_yield(BOE_YIELD_SERIES)
    boj_yield = fetch_yield(BOJ_YIELD_SERIES)

    if boe_yield is None or boj_yield is None:
        logger.warning(
            'BoE/BoJ spread: Missing yield data. '
            f'BoE={boe_yield}, BoJ={boj_yield}'
        )
        return {
            'error':      'Missing yield data',
            'boe_yield':  boe_yield,
            'boj_yield':  boj_yield,
        }

    spread = boe_yield - boj_yield

    # Spread interpretation
    # Above +4.0%: strong carry → STRONG BULLISH GBPJPY
    # +2.0 to +4.0: carry supportive → BULLISH
    # 0 to +2.0: mild carry → NEUTRAL
    # Negative: carry unwind → BEARISH

    if spread >= 4.0:
        bias       = 'STRONG BULLISH'
        bias_score = 85
        emoji      = '🟢🟢'
    elif spread >= 2.0:
        bias       = 'BULLISH'
        bias_score = 65
        emoji      = '🟢'
    elif spread >= 0.5:
        bias       = 'NEUTRAL'
        bias_score = 50
        emoji      = '⚪'
    elif spread >= 0.0:
        bias       = 'SLIGHT BEARISH'
        bias_score = 40
        emoji      = '🟡'
    else:
        bias       = 'BEARISH'
        bias_score = 25
        emoji      = '🔴'

    logger.info(
        f'BoE: {boe_yield:.3f}% | '
        f'BoJ: {boj_yield:.3f}% | '
        f'Spread: {spread:.3f}% | '
        f'Bias: {bias}'
    )

    return {
        'boe_yield':   boe_yield,
        'boj_yield':   boj_yield,
        'spread':      round(spread, 3),
        'bias':        bias,
        'bias_score':  bias_score,
        'emoji':       emoji,
    }


def detect_session_state() -> dict:
    """
    Detects current trading session and transition state.
    Session transitions are when stop-hunts occur.

    Sessions (UTC):
      Tokyo:    00:00 - 09:00
      London:   07:00 - 16:00
      New York: 12:00 - 21:00

    Overlaps (highest volatility):
      Tokyo-London:    07:00 - 09:00
      London-New York: 12:00 - 16:00
    """
    now_hour = datetime.utcnow().hour
    now_min  = datetime.utcnow().minute

    # Determine active sessions
    in_tokyo    = 0 <= now_hour < 9
    in_london   = 7 <= now_hour < 16
    in_newyork  = 12 <= now_hour < 21

    # Detect overlaps
    tokyo_london_overlap   = 7 <= now_hour < 9
    london_newyork_overlap = 12 <= now_hour < 16

    # Transition zones (30 min before overlap)
    approaching_london  = now_hour == 6 and now_min >= 30
    approaching_newyork = now_hour == 11 and now_min >= 30

    # Session state scoring for GBPJPY
    # Highest score during overlaps = highest volatility
    if tokyo_london_overlap:
        session        = 'TOKYO-LONDON OVERLAP'
        volatility     = 'VERY HIGH'
        session_score  = 85
        stop_hunt_risk = 'HIGH'
        emoji          = '⚡'
    elif london_newyork_overlap:
        session        = 'LONDON-NEW YORK OVERLAP'
        volatility     = 'VERY HIGH'
        session_score  = 85
        stop_hunt_risk = 'HIGH'
        emoji          = '⚡'
    elif approaching_london or approaching_newyork:
        session        = 'PRE-OVERLAP SETUP'
        volatility     = 'ELEVATED'
        session_score  = 65
        stop_hunt_risk = 'MODERATE'
        emoji          = '🟡'
    elif in_london:
        session        = 'LONDON SESSION'
        volatility     = 'HIGH'
        session_score  = 60
        stop_hunt_risk = 'MODERATE'
        emoji          = '🟠'
    elif in_newyork:
        session        = 'NEW YORK SESSION'
        volatility     = 'HIGH'
        session_score  = 55
        stop_hunt_risk = 'MODERATE'
        emoji          = '🟠'
    elif in_tokyo:
        session        = 'TOKYO SESSION'
        volatility     = 'MODERATE'
        session_score  = 40
        stop_hunt_risk = 'LOW'
        emoji          = '⚪'
    else:
        session        = 'OFF-HOURS'
        volatility     = 'LOW'
        session_score  = 20
        stop_hunt_risk = 'LOW'
        emoji          = '😴'

    logger.info(
        f'Session: {session} | '
        f'Volatility: {volatility} | '
        f'Stop-Hunt Risk: {stop_hunt_risk}'
    )

    return {
        'session':          session,
        'volatility':       volatility,
        'session_score':    session_score,
        'stop_hunt_risk':   stop_hunt_risk,
        'emoji':            emoji,
        'tokyo_london':     tokyo_london_overlap,
        'london_newyork':   london_newyork_overlap,
        'approaching':      approaching_london or approaching_newyork,
        'current_hour_utc': now_hour,
    }


class GBPSignalAggregator:
    """
    Aggregates all GBP-related signals into unified
    GBPUSD and GBPJPY bias scores.
    """

    def __init__(self, gremlin_client=None):
        self.gc          = gremlin_client
        self.last_result = {}

    def write_to_graph(
        self,
        signal_id: str,
        value: float,
        metadata: dict = None
    ):
        """Writes signal to Cosmos DB graph."""
        if self.gc is None:
            return
        try:
            from graph.cosmos import update_signal
            update_signal(
                self.gc,
                signal_id,
                value,
                metadata
            )
        except Exception as e:
            logger.error(
                f'Graph write error {signal_id}: {e}'
            )

    def fetch_dark_pool_gbp(self) -> dict:
        """Fetches Dark Pool data for GBP."""
        try:
            from ingestion.darkpool import scan_dark_pools
            data = scan_dark_pools()
            return data
        except Exception as e:
            logger.error(f'GBP dark pool error: {e}')
            return {'error': str(e)}

    def fetch_behavioral_gbp(self) -> dict:
        """Fetches Behavioral Exhaust for GBP."""
        try:
            from ingestion.behavioral import (
                calculate_behavioral_exhaust
            )
            data = calculate_behavioral_exhaust()
            return data
        except Exception as e:
            logger.error(f'GBP behavioral error: {e}')
            return {'error': str(e)}

    def calculate_gbp_bias(
        self,
        spread_data: dict,
        session_data: dict,
        darkpool_data: dict,
        behavioral_data: dict,
    ) -> dict:
        """
        Calculates unified GBP bias scores.

        Weighting:
          BoE/BoJ Spread:    40% (primary structural driver)
          Session State:     25% (timing/volatility context)
          Dark Pool:         20% (institutional positioning)
          Behavioral:        15% (retail sentiment)
        """
        scores       = {}
        weighted_sum = 0.0
        total_weight = 0.0

        # ── BoE/BoJ Spread (40%) ───────────────────────────────
        if 'error' not in spread_data:
            sp_score = spread_data.get('bias_score', 50)
            scores['boe_boj_spread'] = sp_score
            weighted_sum  += sp_score * 0.40
            total_weight  += 0.40

            self.write_to_graph(
                'boe_boj_spread',
                spread_data.get('spread', 0.0),
                {
                    'boe':   spread_data.get('boe_yield'),
                    'boj':   spread_data.get('boj_yield'),
                    'bias':  spread_data.get('bias'),
                }
            )

        # ── Session State (25%) ────────────────────────────────
        sess_score = session_data.get('session_score', 50)
        scores['session'] = sess_score
        weighted_sum  += sess_score * 0.25
        total_weight  += 0.25

        self.write_to_graph(
            'session_detector',
            sess_score,
            {
                'session':    session_data.get('session'),
                'volatility': session_data.get('volatility'),
                'stop_hunt':  session_data.get('stop_hunt_risk'),
            }
        )

        # ── Dark Pool (20%) ────────────────────────────────────
        if 'error' not in darkpool_data:
            gbp_anomalies = [
                a for a in darkpool_data.get('anomalies', [])
                if a['instrument'] in ['GBPUSD', 'GBPJPY']
            ]
            dp_score = 55 if gbp_anomalies else 50
            scores['dark_pool'] = dp_score
            weighted_sum  += dp_score * 0.20
            total_weight  += 0.20

            self.write_to_graph(
                'gbp_dark_pool',
                dp_score,
                {
                    'anomalies':   len(gbp_anomalies),
                    'gbp_signal':  darkpool_data.get('gbp_signal'),
                }
            )

        # ── Behavioral (15%) ───────────────────────────────────
        if 'error' not in behavioral_data:
            beh_score = behavioral_data.get(
                'gbp_behavioral_score', 50
            )
            scores['behavioral'] = beh_score
            weighted_sum  += beh_score * 0.15
            total_weight  += 0.15

            self.write_to_graph(
                'gbp_behavioral',
                beh_score,
                {
                    'trend':  behavioral_data.get(
                        'gbp_trend_score'
                    ),
                    'signal': behavioral_data.get('gbp_signal'),
                }
            )

        # ── Final Bias ─────────────────────────────────────────
        if total_weight > 0:
            bias_score = weighted_sum / total_weight
        else:
            bias_score = 50.0

        bias_score = round(bias_score, 1)

        if bias_score >= 75:
            bias_label = 'STRONG BULLISH'
            bias_emoji = '🟢🟢'
        elif bias_score >= 58:
            bias_label = 'BULLISH'
            bias_emoji = '🟢'
        elif bias_score >= 42:
            bias_label = 'NEUTRAL'
            bias_emoji = '⚪'
        elif bias_score >= 25:
            bias_label = 'BEARISH'
            bias_emoji = '🔴'
        else:
            bias_label = 'STRONG BEARISH'
            bias_emoji = '🔴🔴'

        # Confluence
        bullish = sum(1 for s in scores.values() if s >= 55)
        bearish = sum(1 for s in scores.values() if s <= 45)
        total   = len(scores)
        confluence_pct = (
            max(bullish, bearish) / total * 100
            if total > 0 else 0.0
        )

        self.write_to_graph(
            'gbp_bias',
            bias_score,
            {
                'label':      bias_label,
                'confluence': confluence_pct,
            }
        )

        return {
            'bias_score':       bias_score,
            'bias_label':       bias_label,
            'bias_emoji':       bias_emoji,
            'confluence_pct':   round(confluence_pct, 1),
            'high_confluence':  confluence_pct >= 60,
            'signal_scores':    scores,
            'stop_hunt_active': (
                session_data.get('tokyo_london') or
                session_data.get('london_newyork')
            ),
            'spread_value':     spread_data.get('spread', 0.0)
                                if 'error' not in spread_data
                                else 0.0,
        }

    def aggregate(self, fast_mode: bool = False) -> dict:
        """Main aggregation function."""
        logger.info('Running GBP Signal Aggregation...')
        start = datetime.utcnow()

        spread_data   = calculate_boe_boj_spread()
        session_data  = detect_session_state()
        darkpool_data = self.fetch_dark_pool_gbp()

        if not fast_mode:
            behavioral_data = self.fetch_behavioral_gbp()
        else:
            behavioral_data = {'error': 'fast_mode'}

        bias = self.calculate_gbp_bias(
            spread_data,
            session_data,
            darkpool_data,
            behavioral_data,
        )

        elapsed = (
            datetime.utcnow() - start
        ).total_seconds()

        result = {
            'timestamp':        datetime.utcnow().isoformat(),
            'instruments':      ['GBPUSD', 'GBPJPY'],
            'elapsed_seconds':  round(elapsed, 1),
            'fast_mode':        fast_mode,

            'bias_score':       bias['bias_score'],
            'bias_label':       bias['bias_label'],
            'bias_emoji':       bias['bias_emoji'],
            'confluence_pct':   bias['confluence_pct'],
            'high_confluence':  bias['high_confluence'],
            'signal_scores':    bias['signal_scores'],
            'stop_hunt_active': bias['stop_hunt_active'],
            'spread_value':     bias['spread_value'],

            'spread':    spread_data,
            'session':   session_data,
            'darkpool':  darkpool_data,
            'behavioral': behavioral_data,
        }

        self.last_result = result
        logger.info(
            f'GBP Aggregation complete: '
            f'Bias={bias["bias_score"]}/100 '
            f'({bias["bias_label"]}) | '
            f'Session={session_data["session"]} | '
            f'Time={elapsed:.1f}s'
        )

        return result

    def generate_signal(self) -> dict:
        """Alias for aggregate()."""
        return self.aggregate()

    def format_telegram(self, data: dict) -> str:
        """Formats GBP signal for Telegram."""
        scores = data.get('signal_scores', {})
        score_lines = ''
        score_map = {
            'boe_boj_spread': 'BoE/BoJ',
            'session':        'Session',
            'dark_pool':      'Dark Pool',
            'behavioral':     'Behavioral',
        }
        for key, label in score_map.items():
            if key in scores:
                bar = '█' * int(scores[key] / 10)
                score_lines += (
                    f'{label:<12}: '
                    f'{scores[key]:>5.1f}/100 {bar}\n'
                )

        session = data.get('session', {})
        stop_hunt = ''
        if data.get('stop_hunt_active'):
            stop_hunt = (
                f'\n⚡ <b>STOP-HUNT WINDOW ACTIVE</b>\n'
                f'Session: {session.get("session")}\n'
                f'Liquidity sweep imminent\n'
            )

        spread = data.get('spread', {})
        spread_line = (
            f'BoE/BoJ Spread: '
            f'<b>{spread.get("spread", 0):.3f}%</b> '
            f'({spread.get("bias", "N/A")})\n'
            if 'error' not in spread else ''
        )

        return (
            f'💷 <b>GBP SIGNAL REPORT</b>\n'
            f'<code>{data["timestamp"][:19]} UTC</code>\n\n'
            f'Bias Score:  <b>{data["bias_score"]}/100</b>\n'
            f'Direction:   {data["bias_emoji"]} '
            f'<b>{data["bias_label"]}</b>\n'
            f'Confluence:  <b>{data["confluence_pct"]}%</b>\n'
            f'{spread_line}\n'
            f'<b>SIGNAL BREAKDOWN:</b>\n'
            f'<code>{score_lines}</code>'
            f'{stop_hunt}'
        )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — GBP Signal Aggregator Test')
    print('='*55 + '\n')

    agg    = GBPSignalAggregator(gremlin_client=None)
    result = agg.aggregate(fast_mode=True)

    print(f'Bias Score:       {result["bias_score"]}/100')
    print(f'Direction:        {result["bias_label"]}')
    print(f'Confluence:       {result["confluence_pct"]}%')
    print(f'Session:          {result["session"]["session"]}')
    print(f'Stop-Hunt Active: {result["stop_hunt_active"]}')

    spread = result.get('spread', {})
    if 'error' not in spread:
        print(f'BoE Yield:        {spread["boe_yield"]:.3f}%')
        print(f'BoJ Yield:        {spread["boj_yield"]:.3f}%')
        print(f'Spread:           {spread["spread"]:.3f}%')

    print(f'\nSignal Scores:')
    for k, v in result['signal_scores'].items():
        print(f'  {k:<18}: {v:.1f}/100')
    print(f'\nElapsed: {result["elapsed_seconds"]}s')