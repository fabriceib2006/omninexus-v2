# ════════════════════════════════════════════════════════════════
# OMNINEXUS — signals/gold.py
# Gold Signal Aggregator
# Pulls all XAUUSD signals from ingestion layer
# Aggregates into unified Gold Bias Score
# Writes results to Cosmos DB World Graph
# ════════════════════════════════════════════════════════════════

import logging
from datetime import datetime
from typing import Optional
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.signals.gold')


class GoldSignalAggregator:
    """
    Aggregates all gold-related signals from the
    ingestion layer into a unified bias score.
    Writes live signal values to Cosmos DB graph.
    """

    def __init__(self, gremlin_client=None):
        self.gc = gremlin_client
        self.last_result = {}

    # ── INDIVIDUAL SIGNAL FETCHERS ─────────────────────────────

    def fetch_real_yield(self) -> dict:
        """Fetches Real Yield from FRED engine."""
        try:
            from ingestion.fred_yield import calculate_real_yield
            data = calculate_real_yield()
            if 'error' not in data:
                logger.info(
                    f'Real Yield: {data["real_yield"]:.3f}% '
                    f'— {data["gold_bias"]}'
                )
            return data
        except Exception as e:
            logger.error(f'Real Yield fetch error: {e}')
            return {'error': str(e)}

    def fetch_friction(self) -> dict:
        """Fetches Geopolitical Friction Index."""
        try:
            from ingestion.friction import (
                calculate_friction_index
            )
            data = calculate_friction_index()
            if 'error' not in data:
                logger.info(
                    f'Friction Score: '
                    f'{data["friction_score"]}/100 '
                    f'— {data["level"]}'
                )
            return data
        except Exception as e:
            logger.error(f'Friction fetch error: {e}')
            return {'error': str(e)}

    def fetch_dark_pool(self) -> dict:
        """Fetches Dark Pool Ghost Signal for gold."""
        try:
            from ingestion.darkpool import scan_dark_pools
            data = scan_dark_pools()
            logger.info(
                f'Dark Pool: '
                f'{data["anomalies_found"]} anomalies | '
                f'Gold signal: {data["gold_signal"]}'
            )
            return data
        except Exception as e:
            logger.error(f'Dark pool fetch error: {e}')
            return {'error': str(e)}

    def fetch_behavioral(self) -> dict:
        """Fetches Behavioral Exhaust for gold."""
        try:
            from ingestion.behavioral import (
                calculate_behavioral_exhaust
            )
            data = calculate_behavioral_exhaust()
            logger.info(
                f'Gold Behavioral: '
                f'{data["gold_behavioral_score"]}/100 '
                f'— {data["gold_signal"]}'
            )
            return data
        except Exception as e:
            logger.error(f'Behavioral fetch error: {e}')
            return {'error': str(e)}

    def fetch_satellite(self) -> dict:
        """Fetches Satellite Proxy data."""
        try:
            from ingestion.satellite import (
                scan_satellite_zones
            )
            data = scan_satellite_zones()
            logger.info(
                f'Satellite: '
                f'{data["gold_zones_active"]} gold zones active'
            )
            return data
        except Exception as e:
            logger.error(f'Satellite fetch error: {e}')
            return {'error': str(e)}

    # ── GRAPH WRITER ───────────────────────────────────────────

    def write_to_graph(
        self,
        signal_id: str,
        value: float,
        metadata: dict = None
    ):
        """Writes a signal value to Cosmos DB graph."""
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
            logger.error(f'Graph write error {signal_id}: {e}')

    # ── BIAS CALCULATOR ────────────────────────────────────────

    def calculate_gold_bias(
        self,
        real_yield_data: dict,
        friction_data: dict,
        darkpool_data: dict,
        behavioral_data: dict,
        satellite_data: dict
    ) -> dict:
        """
        Calculates unified Gold Bias Score from all signals.

        Weighting:
          Real Yield:    35% (primary driver)
          Friction:      25% (safe-haven demand)
          Dark Pool:     20% (institutional positioning)
          Behavioral:    12% (retail sentiment exhaust)
          Satellite:      8% (physical world proxy)

        Score 0-100:
          80-100: STRONG BULLISH
          60-79:  BULLISH
          40-59:  NEUTRAL
          20-39:  BEARISH
          0-19:   STRONG BEARISH
        """
        scores      = {}
        weights     = {}
        total_weight = 0.0
        weighted_sum = 0.0

        # ── Real Yield Score (35%) ─────────────────────────────
        if 'error' not in real_yield_data:
            ry_score = real_yield_data.get('bias_score', 50)
            scores['real_yield']  = ry_score
            weights['real_yield'] = 0.35
            weighted_sum  += ry_score * 0.35
            total_weight  += 0.35

            # Write to graph
            self.write_to_graph(
                'real_yield',
                real_yield_data.get('real_yield', 0.0),
                {
                    'bias':      real_yield_data.get('gold_bias'),
                    'nominal':   real_yield_data.get('nominal_yield'),
                    'breakeven': real_yield_data.get('breakeven_rate'),
                    'breakout':  real_yield_data.get('breakout_signal'),
                }
            )

        # ── Friction Score (25%) ───────────────────────────────
        if 'error' not in friction_data:
            fr_score = friction_data.get('friction_score', 0)
            scores['friction']  = fr_score
            weights['friction'] = 0.25
            weighted_sum  += fr_score * 0.25
            total_weight  += 0.25

            self.write_to_graph(
                'friction_score',
                fr_score,
                {
                    'level':    friction_data.get('level'),
                    'articles': friction_data.get('total_articles'),
                    'alert':    friction_data.get('alert_triggered'),
                }
            )

        # ── Dark Pool Score (20%) ──────────────────────────────
        if 'error' not in darkpool_data:
            # Convert dark pool signal to 0-100 score
            gold_anomalies = [
                a for a in darkpool_data.get('anomalies', [])
                if a['instrument'] == 'XAUUSD'
            ]
            if gold_anomalies:
                max_z = max(
                    abs(a['z_score'])
                    for a in gold_anomalies
                )
                # Z-score of 2.0 = score 50, 4.0 = score 100
                dp_score = min(100, (max_z / 4.0) * 100)
                # Direction matters
                if gold_anomalies[0]['direction'] == 'ACCUMULATION':
                    dp_score = 50 + (dp_score / 2)
                else:
                    dp_score = 50 - (dp_score / 2)
            else:
                dp_score = 50  # neutral when no anomaly

            scores['dark_pool']  = dp_score
            weights['dark_pool'] = 0.20
            weighted_sum  += dp_score * 0.20
            total_weight  += 0.20

            self.write_to_graph(
                'gold_dark_pool',
                dp_score,
                {
                    'anomalies': len(gold_anomalies),
                    'gold_signal': darkpool_data.get('gold_signal'),
                }
            )

        # ── Behavioral Score (12%) ─────────────────────────────
        if 'error' not in behavioral_data:
            beh_score = behavioral_data.get(
                'gold_behavioral_score', 50
            )
            scores['behavioral']  = beh_score
            weights['behavioral'] = 0.12
            weighted_sum  += beh_score * 0.12
            total_weight  += 0.12

            self.write_to_graph(
                'gold_behavioral',
                beh_score,
                {
                    'trend_score': behavioral_data.get(
                        'gold_trend_score'
                    ),
                    'github_urgency': behavioral_data.get(
                        'github_urgency_score'
                    ),
                    'signal': behavioral_data.get('gold_signal'),
                }
            )

        # ── Satellite Score (8%) ───────────────────────────────
        if 'error' not in satellite_data:
            gold_anomalies_sat = satellite_data.get('anomalies', [])
            gold_sat = [
                a for a in gold_anomalies_sat
                if a.get('instrument') == 'XAUUSD'
            ]
            sat_score = 60 if gold_sat else 50
            scores['satellite']  = sat_score
            weights['satellite'] = 0.08
            weighted_sum  += sat_score * 0.08
            total_weight  += 0.08

            self.write_to_graph(
                'gold_satellite',
                sat_score,
                {
                    'zones_active': satellite_data.get(
                        'gold_zones_active'
                    ),
                    'anomalies': len(gold_sat),
                }
            )

        # ── Final Bias Score ───────────────────────────────────
        if total_weight > 0:
            bias_score = weighted_sum / total_weight
        else:
            bias_score = 50.0

        bias_score = round(bias_score, 1)

        # ── Bias Label ─────────────────────────────────────────
        if bias_score >= 80:
            bias_label = 'STRONG BULLISH'
            bias_emoji = '🟢🟢'
        elif bias_score >= 60:
            bias_label = 'BULLISH'
            bias_emoji = '🟢'
        elif bias_score >= 40:
            bias_label = 'NEUTRAL'
            bias_emoji = '⚪'
        elif bias_score >= 20:
            bias_label = 'BEARISH'
            bias_emoji = '🔴'
        else:
            bias_label = 'STRONG BEARISH'
            bias_emoji = '🔴🔴'

        # ── Confluence Check ───────────────────────────────────
        # Confluence = how many signals agree on direction
        bullish_signals = sum(
            1 for s in scores.values() if s >= 55
        )
        bearish_signals = sum(
            1 for s in scores.values() if s <= 45
        )
        total_signals = len(scores)

        if total_signals > 0:
            confluence_pct = (
                max(bullish_signals, bearish_signals)
                / total_signals * 100
            )
        else:
            confluence_pct = 0.0

        high_confluence = confluence_pct >= 60

        # Write final bias to graph
        self.write_to_graph(
            'gold_bias',
            bias_score,
            {
                'label':      bias_label,
                'confluence': confluence_pct,
                'signals':    len(scores),
            }
        )

        return {
            'bias_score':      bias_score,
            'bias_label':      bias_label,
            'bias_emoji':      bias_emoji,
            'confluence_pct':  round(confluence_pct, 1),
            'high_confluence': high_confluence,
            'signal_scores':   scores,
            'signals_used':    len(scores),
            'breakout_signal': real_yield_data.get(
                'breakout_signal', False
            ) if 'error' not in real_yield_data else False,
            'friction_alert':  friction_data.get(
                'alert_triggered', False
            ) if 'error' not in friction_data else False,
            'dark_pool_alert': darkpool_data.get(
                'gold_signal', False
            ) if 'error' not in darkpool_data else False,
        }

    # ── MAIN AGGREGATOR ────────────────────────────────────────

    def aggregate(self, fast_mode: bool = False) -> dict:
        """
        Main function. Fetches all gold signals and
        returns unified Gold Signal payload.

        fast_mode=True skips behavioral and satellite
        for speed during session transitions.
        """
        logger.info('Running Gold Signal Aggregation...')
        start = datetime.utcnow()

        # Fetch all signals
        real_yield_data  = self.fetch_real_yield()
        friction_data    = self.fetch_friction()
        darkpool_data    = self.fetch_dark_pool()

        if not fast_mode:
            behavioral_data  = self.fetch_behavioral()
            satellite_data   = self.fetch_satellite()
        else:
            behavioral_data  = {'error': 'fast_mode'}
            satellite_data   = {'error': 'fast_mode'}

        # Calculate unified bias
        bias = self.calculate_gold_bias(
            real_yield_data,
            friction_data,
            darkpool_data,
            behavioral_data,
            satellite_data
        )

        elapsed = (
            datetime.utcnow() - start
        ).total_seconds()

        result = {
            'timestamp':        datetime.utcnow().isoformat(),
            'instrument':       'XAUUSD',
            'elapsed_seconds':  round(elapsed, 1),
            'fast_mode':        fast_mode,

            # Bias
            'bias_score':       bias['bias_score'],
            'bias_label':       bias['bias_label'],
            'bias_emoji':       bias['bias_emoji'],
            'confluence_pct':   bias['confluence_pct'],
            'high_confluence':  bias['high_confluence'],
            'signal_scores':    bias['signal_scores'],
            'signals_used':     bias['signals_used'],

            # Alert flags
            'breakout_signal':  bias['breakout_signal'],
            'friction_alert':   bias['friction_alert'],
            'dark_pool_alert':  bias['dark_pool_alert'],

            # Raw data
            'real_yield':       real_yield_data,
            'friction':         friction_data,
            'darkpool':         darkpool_data,
            'behavioral':       behavioral_data,
            'satellite':        satellite_data,
        }

        self.last_result = result
        logger.info(
            f'Gold Aggregation complete: '
            f'Bias={bias["bias_score"]}/100 '
            f'({bias["bias_label"]}) | '
            f'Confluence={bias["confluence_pct"]}% | '
            f'Time={elapsed:.1f}s'
        )

        return result

    def generate_signal(self) -> dict:
        """
        Alias for aggregate() — returns normalized
        signal payload for CFR engine consumption.
        """
        return self.aggregate()

    # ── TELEGRAM FORMATTER ────────────────────────────────────

    def format_telegram(self, data: dict) -> str:
        """Formats gold signal for Telegram push."""
        if 'error' in data:
            return f'❌ Gold Signal Error: {data["error"]}'

        scores = data.get('signal_scores', {})
        score_lines = ''
        score_map = {
            'real_yield':  'Real Yield',
            'friction':    'Friction',
            'dark_pool':   'Dark Pool',
            'behavioral':  'Behavioral',
            'satellite':   'Satellite',
        }
        for key, label in score_map.items():
            if key in scores:
                bar = '█' * int(scores[key] / 10)
                score_lines += (
                    f'{label:<12}: '
                    f'{scores[key]:>5.1f}/100 {bar}\n'
                )

        alerts = ''
        if data.get('breakout_signal'):
            alerts += '⚡ BREAKOUT SIGNAL ACTIVE\n'
        if data.get('friction_alert'):
            alerts += '🚨 FRICTION THRESHOLD EXCEEDED\n'
        if data.get('dark_pool_alert'):
            alerts += '👻 DARK POOL ANOMALY DETECTED\n'

        confluence_emoji = '✅' if data['high_confluence'] else '⚠️'

        return (
            f'🥇 <b>XAUUSD SIGNAL REPORT</b>\n'
            f'<code>{data["timestamp"][:19]} UTC</code>\n\n'
            f'Bias Score:  <b>{data["bias_score"]}/100</b>\n'
            f'Direction:   {data["bias_emoji"]} '
            f'<b>{data["bias_label"]}</b>\n'
            f'Confluence:  {confluence_emoji} '
            f'<b>{data["confluence_pct"]}%</b>\n\n'
            f'<b>SIGNAL BREAKDOWN:</b>\n'
            f'<code>{score_lines}</code>'
            f'{alerts}'
        )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Gold Signal Aggregator Test')
    print('='*55 + '\n')
    print('Running fast mode (skips behavioral/satellite)...\n')

    agg = GoldSignalAggregator(gremlin_client=None)
    result = agg.aggregate(fast_mode=True)

    print(f'Bias Score:    {result["bias_score"]}/100')
    print(f'Direction:     {result["bias_label"]}')
    print(f'Confluence:    {result["confluence_pct"]}%')
    print(f'Signals Used:  {result["signals_used"]}')
    print(f'\nSignal Scores:')
    for k, v in result['signal_scores'].items():
        print(f'  {k:<15}: {v:.1f}/100')
    print(f'\nAlert Flags:')
    print(f'  Breakout:    {result["breakout_signal"]}')
    print(f'  Friction:    {result["friction_alert"]}')
    print(f'  Dark Pool:   {result["dark_pool_alert"]}')
    print(f'\nElapsed: {result["elapsed_seconds"]}s')