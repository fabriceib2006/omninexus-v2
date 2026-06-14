# ════════════════════════════════════════════════════════════════
# OMNINEXUS — memory/half_life.py
# Signal Half-Life Tracker
# Measures rolling predictive power of every signal
# When Pearson correlation drops below threshold:
#   → Telegram alert fires automatically
#   → Signal flagged for investigation
# The system monitors its own edge decay in real-time
# ════════════════════════════════════════════════════════════════

import logging
import json
import os
import math
from datetime import datetime, timedelta
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.memory.half_life')

HALFLIFE_FILE    = 'logs/memory/half_life_data.json'
HALFLIFE_DIR     = 'logs/memory'
ROLLING_WINDOW   = 90   # days for rolling Pearson calculation
MIN_OBSERVATIONS = 10   # minimum data points for valid correlation


# ── SIGNALS TO TRACK ──────────────────────────────────────────
TRACKED_SIGNALS = {
    'real_yield': {
        'name':        'Real Yield Divergence',
        'description': 'US 10Y real yield vs gold price',
        'baseline_corr': -0.82,  # expected inverse correlation
        'min_threshold': 0.40,
    },
    'friction': {
        'name':        'Geopolitical Friction Index',
        'description': 'Friction score vs gold safe-haven demand',
        'baseline_corr': 0.68,
        'min_threshold': 0.35,
    },
    'boe_boj': {
        'name':        'BoE/BoJ Spread',
        'description': 'Yield spread vs GBPJPY price action',
        'baseline_corr': 0.74,
        'min_threshold': 0.40,
    },
    'dark_pool': {
        'name':        'Dark Pool Ghost Signal',
        'description': 'Dark pool Z-score vs subsequent price move',
        'baseline_corr': 0.61,
        'min_threshold': 0.30,
    },
    'behavioral': {
        'name':        'Behavioral Exhaust',
        'description': 'Google Trends acceleration vs price',
        'baseline_corr': 0.52,
        'min_threshold': 0.25,
    },
    'session': {
        'name':        'Session Transition Detector',
        'description': 'Session overlap timing vs volatility',
        'baseline_corr': 0.58,
        'min_threshold': 0.30,
    },
}


# ── PEARSON CORRELATION ────────────────────────────────────────

def pearson_correlation(x: list, y: list) -> float:
    """
    Calculates Pearson correlation coefficient between
    two lists of values.
    Returns value between -1.0 and 1.0.
    Returns 0.0 if insufficient data.
    """
    n = min(len(x), len(y))
    if n < 3:
        return 0.0

    x = x[:n]
    y = y[:n]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    numerator = sum(
        (x[i] - mean_x) * (y[i] - mean_y)
        for i in range(n)
    )

    sum_sq_x = sum((xi - mean_x) ** 2 for xi in x)
    sum_sq_y = sum((yi - mean_y) ** 2 for yi in y)

    denominator = math.sqrt(sum_sq_x * sum_sq_y)

    if denominator == 0:
        return 0.0

    return round(numerator / denominator, 4)


class SignalHalfLifeTracker:
    """
    Tracks the rolling predictive power of each signal.
    Stores signal values and subsequent price outcomes.
    Calculates rolling Pearson correlation.
    Alerts when correlation drops below minimum threshold.
    """

    def __init__(self, gremlin_client=None):
        self.gc      = gremlin_client
        self.history = {}   # signal_name → list of observations
        self.alerts  = []   # fired alerts

        self._load()

    # ── PERSISTENCE ────────────────────────────────────────────

    def _load(self):
        """Loads half-life data from file."""
        os.makedirs(HALFLIFE_DIR, exist_ok=True)
        if os.path.exists(HALFLIFE_FILE):
            try:
                with open(HALFLIFE_FILE, 'r') as f:
                    data = json.load(f)
                self.history = data.get('history', {})
                self.alerts  = data.get('alerts', [])[-50:]
                logger.info(
                    f'Half-life data loaded: '
                    f'{len(self.history)} signals tracked'
                )
            except Exception as e:
                logger.error(f'Half-life load error: {e}')
                self.history = {}

    def _save(self):
        """Saves half-life data to file."""
        try:
            os.makedirs(HALFLIFE_DIR, exist_ok=True)

            # Trim history to last 500 per signal
            trimmed = {
                k: v[-500:]
                for k, v in self.history.items()
            }

            data = {
                'history':  trimmed,
                'alerts':   self.alerts[-50:],
                'saved_at': datetime.utcnow().isoformat(),
            }
            with open(HALFLIFE_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f'Half-life save error: {e}')

    # ── CORE UPDATE ────────────────────────────────────────────

    def update(self, signal_values: dict) -> dict:
        """
        Records current signal values.
        signal_values: dict mapping signal_name → current value

        Expected keys:
          real_yield, friction, boe_boj, dark_pool,
          behavioral, session

        These are the raw signal readings that will be
        correlated against subsequent price outcomes.
        """
        timestamp = datetime.utcnow().isoformat()

        for signal_name, value in signal_values.items():
            if signal_name not in self.history:
                self.history[signal_name] = []

            self.history[signal_name].append({
                'timestamp': timestamp,
                'value':     float(value),
                'outcome':   None,  # filled in after trade
            })

        self._save()
        logger.info(
            f'Half-life updated: '
            f'{len(signal_values)} signals recorded'
        )

        return signal_values

    def record_outcome(
        self,
        signal_name: str,
        outcome_pct: float,
        lookback_minutes: int = 60
    ):
        """
        Records the price outcome associated with a
        past signal reading.
        Links the signal value from lookback_minutes ago
        to the actual price move that followed.
        """
        if signal_name not in self.history:
            return

        cutoff = (
            datetime.utcnow() -
            timedelta(minutes=lookback_minutes)
        ).isoformat()

        # Find the most recent observation before cutoff
        for obs in reversed(self.history[signal_name]):
            if obs['timestamp'] <= cutoff and \
               obs['outcome'] is None:
                obs['outcome'] = outcome_pct
                break

        self._save()

    # ── CORRELATION CALCULATOR ─────────────────────────────────

    def compute_half_life(self) -> dict:
        """
        Calculates rolling Pearson correlation for all
        tracked signals against their recorded outcomes.

        Returns dict with:
          signal_name → {
            correlation, baseline, pct_of_baseline,
            status, degrading, alert_triggered
          }
        """
        results = {}

        for signal_name, signal_config in TRACKED_SIGNALS.items():
            history = self.history.get(signal_name, [])

            # Filter to observations with recorded outcomes
            paired = [
                obs for obs in history
                if obs.get('outcome') is not None
            ]

            if len(paired) < MIN_OBSERVATIONS:
                results[signal_name] = {
                    'name':          signal_config['name'],
                    'correlation':   None,
                    'baseline':      signal_config['baseline_corr'],
                    'pct_baseline':  None,
                    'observations':  len(paired),
                    'status':        'BUILDING',
                    'degrading':     False,
                    'alert':         False,
                    'message':       (
                        f'Building baseline '
                        f'({len(paired)}/{MIN_OBSERVATIONS})'
                    ),
                }
                continue

            # Use most recent 90 days
            cutoff = (
                datetime.utcnow() -
                timedelta(days=ROLLING_WINDOW)
            ).isoformat()

            recent = [
                obs for obs in paired
                if obs['timestamp'] >= cutoff
            ]

            if len(recent) < 3:
                recent = paired[-20:]  # fallback

            signal_vals  = [obs['value']   for obs in recent]
            outcome_vals = [obs['outcome'] for obs in recent]

            correlation = abs(
                pearson_correlation(signal_vals, outcome_vals)
            )

            baseline       = signal_config['baseline_corr']
            min_threshold  = signal_config['min_threshold']
            pct_baseline   = (
                correlation / baseline * 100
                if baseline > 0 else 0
            )

            degrading = correlation < min_threshold

            # Calculate degradation % vs baseline
            degradation_pct = (
                (baseline - correlation) / baseline * 100
                if baseline > 0 else 0
            )

            # Status classification
            if correlation >= baseline * 0.8:
                status = 'HEALTHY'
                emoji  = '✅'
            elif correlation >= min_threshold:
                status = 'DECLINING'
                emoji  = '⚠️'
            else:
                status = 'DEGRADED'
                emoji  = '🔴'

            alert_triggered = degrading

            if alert_triggered:
                self._fire_alert(
                    signal_name, signal_config,
                    correlation, degradation_pct
                )

            results[signal_name] = {
                'name':           signal_config['name'],
                'correlation':    round(correlation, 4),
                'baseline':       baseline,
                'pct_baseline':   round(pct_baseline, 1),
                'degradation_pct': round(degradation_pct, 1),
                'min_threshold':  min_threshold,
                'observations':   len(recent),
                'status':         status,
                'emoji':          emoji,
                'degrading':      degrading,
                'alert':          alert_triggered,
                'message':        (
                    f'Corr={correlation:.4f} | '
                    f'Baseline={baseline:.4f} | '
                    f'{pct_baseline:.1f}% of baseline'
                ),
            }

        self._save()
        return results

    def _fire_alert(
        self,
        signal_name:    str,
        signal_config:  dict,
        correlation:    float,
        degradation_pct: float
    ):
        """Records and logs a signal degradation alert."""
        alert = {
            'timestamp':       datetime.utcnow().isoformat(),
            'signal':          signal_name,
            'name':            signal_config['name'],
            'correlation':     correlation,
            'degradation_pct': degradation_pct,
        }

        # Avoid duplicate alerts within 24h
        last_alert = next(
            (a for a in reversed(self.alerts)
             if a['signal'] == signal_name),
            None
        )
        if last_alert:
            last_time = datetime.fromisoformat(
                last_alert['timestamp']
            )
            if (datetime.utcnow() - last_time).total_seconds() \
               < 86400:
                return  # Already alerted today

        self.alerts.append(alert)

        logger.warning(
            f'HALF-LIFE ALERT: {signal_config["name"]} | '
            f'Correlation={correlation:.4f} | '
            f'Degradation={degradation_pct:.1f}%'
        )

    def get_signal_health(self) -> dict:
        """
        Quick health check for /halflife command.
        Returns overall signal health summary.
        """
        half_lives = self.compute_half_life()

        healthy   = sum(
            1 for v in half_lives.values()
            if v.get('status') == 'HEALTHY'
        )
        declining = sum(
            1 for v in half_lives.values()
            if v.get('status') == 'DECLINING'
        )
        degraded  = sum(
            1 for v in half_lives.values()
            if v.get('status') == 'DEGRADED'
        )
        building  = sum(
            1 for v in half_lives.values()
            if v.get('status') == 'BUILDING'
        )

        total     = len(half_lives)
        all_green = degraded == 0 and declining == 0

        return {
            'signals':        half_lives,
            'healthy':        healthy,
            'declining':      declining,
            'degraded':       degraded,
            'building':       building,
            'total':          total,
            'all_healthy':    all_green,
            'alerts_fired':   len(self.alerts),
            'timestamp':      datetime.utcnow().isoformat(),
        }

    def simulate_with_live_signals(
        self,
        gold_data: dict,
        gbp_data:  dict
    ):
        """
        Updates half-life tracker with live signal readings.
        Called after each signal aggregation cycle.
        """
        signal_values = {}

        # Extract real yield
        if 'real_yield' in gold_data:
            ry = gold_data['real_yield']
            if isinstance(ry, dict) and 'real_yield' in ry:
                signal_values['real_yield'] = abs(
                    ry.get('real_yield', 0.0)
                )

        # Extract friction
        friction = gold_data.get('friction', {})
        if isinstance(friction, dict):
            signal_values['friction'] = friction.get(
                'friction_score', 0.0
            )

        # Extract session
        session = gbp_data.get('session', {})
        if isinstance(session, dict):
            signal_values['session'] = session.get(
                'session_score', 0.0
            )

        # Extract behavioral
        behavioral = gold_data.get('behavioral', {})
        if isinstance(behavioral, dict) and \
           'error' not in behavioral:
            signal_values['behavioral'] = behavioral.get(
                'gold_behavioral_score', 0.0
            )

        # Extract dark pool
        darkpool = gold_data.get('darkpool', {})
        if isinstance(darkpool, dict):
            anomalies = darkpool.get('anomalies', [])
            dp_z = max(
                (abs(a.get('z_score', 0)) for a in anomalies),
                default=0.0
            )
            signal_values['dark_pool'] = dp_z

        # Extract BoE/BoJ spread
        spread = gbp_data.get('spread', {})
        if isinstance(spread, dict) and \
           'error' not in spread:
            signal_values['boe_boj'] = abs(
                spread.get('spread', 0.0)
            )

        if signal_values:
            self.update(signal_values)

        return signal_values

    # ── TELEGRAM FORMATTERS ────────────────────────────────────

    def format_halflife_telegram(self, health: dict) -> str:
        """Formats half-life report for /halflife command."""
        signals = health.get('signals', {})

        signal_lines = ''
        for sig_id, data in signals.items():
            emoji = data.get('emoji', '❓')
            name  = data.get('name', sig_id)[:20]
            corr  = data.get('correlation')
            obs   = data.get('observations', 0)
            status = data.get('status', 'UNKNOWN')

            if corr is not None:
                signal_lines += (
                    f'{emoji} {name:<20} '
                    f'Corr: {corr:.3f} '
                    f'({data.get("pct_baseline", 0):.0f}% baseline) '
                    f'[{obs} obs]\n'
                )
            else:
                signal_lines += (
                    f'⏳ {name:<20} '
                    f'Building... '
                    f'({obs}/{MIN_OBSERVATIONS})\n'
                )

        overall = (
            '✅ ALL SIGNALS HEALTHY'
            if health['all_healthy']
            else f'⚠️ {health["degraded"]} SIGNAL(S) DEGRADED'
        )

        return (
            f'📉 <b>SIGNAL HALF-LIFE REPORT</b>\n'
            f'<code>{health["timestamp"][:19]} UTC</code>\n\n'
            f'Overall: <b>{overall}</b>\n'
            f'Healthy: {health["healthy"]} | '
            f'Declining: {health["declining"]} | '
            f'Degraded: {health["degraded"]} | '
            f'Building: {health["building"]}\n\n'
            f'<b>SIGNAL CORRELATIONS:</b>\n'
            f'<code>{signal_lines}</code>\n'
            f'Window: {ROLLING_WINDOW} days | '
            f'Min obs: {MIN_OBSERVATIONS} | '
            f'Alerts: {health["alerts_fired"]}'
        )

    def format_alert_telegram(
        self,
        signal_name:    str,
        correlation:    float,
        degradation_pct: float
    ) -> str:
        """Formats a degradation alert for Telegram push."""
        config_data = TRACKED_SIGNALS.get(signal_name, {})
        name        = config_data.get('name', signal_name)
        min_thresh  = config_data.get('min_threshold', 0.30)

        return (
            f'⚠️ <b>SIGNAL DEGRADING</b>\n\n'
            f'Signal:      <b>{name}</b>\n'
            f'Correlation: <b>{correlation:.4f}</b>\n'
            f'Minimum:     {min_thresh:.4f}\n'
            f'Degradation: <b>-{degradation_pct:.1f}%</b> '
            f'vs baseline\n\n'
            f'<b>Action required:</b>\n'
            f'Investigate signal source before '
            f'next deployment cycle.\n'
            f'Signal will not pass Constitutional Gate '
            f'until half-life recovers.'
        )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Signal Half-Life Tracker Test')
    print('='*55 + '\n')

    tracker = SignalHalfLifeTracker()

    # Update with test signal values
    print('Recording signal values...')
    for i in range(15):
        tracker.update({
            'real_yield':  2.0 + (i * 0.05),
            'friction':    35.0 + (i * 2.0),
            'boe_boj':     3.2 + (i * 0.1),
            'dark_pool':   abs(i - 7) * 0.3,
            'behavioral':  25.0 + (i * 3.0),
            'session':     50.0 + (i * 4.0),
        })
        # Record fake outcome
        for sig in ['real_yield', 'friction', 'boe_boj',
                    'dark_pool', 'behavioral', 'session']:
            if tracker.history.get(sig):
                tracker.history[sig][-1]['outcome'] = (
                    float(i * 0.5 - 2.0)
                )

    # Compute half-life
    print('\nComputing signal half-lives...')
    health = tracker.get_signal_health()

    print(f'Total Signals:  {health["total"]}')
    print(f'Healthy:        {health["healthy"]}')
    print(f'Declining:      {health["declining"]}')
    print(f'Degraded:       {health["degraded"]}')
    print(f'Building:       {health["building"]}')
    print(f'All Healthy:    {health["all_healthy"]}')
    print(f'\nSignal Details:')
    for sig, data in health['signals'].items():
        corr   = data.get('correlation', 'N/A')
        status = data.get('status', 'UNKNOWN')
        emoji  = data.get('emoji', '❓')
        corr_str = (
            f'{corr:.4f}' if isinstance(corr, float)
            else corr
        )
        print(
            f'  {emoji} {sig:<15}: '
            f'corr={corr_str} | '
            f'{status}'
        )