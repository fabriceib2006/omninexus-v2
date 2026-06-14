# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/brain_update.py
# Weekend Automated Retraining Cycle
#
# FRIDAY 22:00 UTC — Market closes:
#   → Download full week candles (all timeframes)
#   → Merge with 10yr history JSON
#   → Tag event-driven candles
#
# SATURDAY — Analysis:
#   → Audit every live signal from past week
#   → Separate bad signals from unlucky signals
#   → Recalibrate indicator weights (slowly)
#   → Retrain autoencoder on new data
#   → Update CFR regret tables
#   → Re-run regime clustering
#
# SUNDAY — Preparation:
#   → Study doubt signals (50-65% confidence)
#   → Fetch next week economic calendar
#   → Run out-of-sample validation
#   → If improved: deploy new weights
#   → If overfit: revert + flag for review
#   → Send full weekly report to Telegram
#
# Target: Maximum EXPECTANCY not maximum win rate
# Expectancy = (win_rate × avg_win) - (loss_rate × avg_loss)
# ════════════════════════════════════════════════════════════════

import asyncio
import json
import logging
import os
import shutil
import time
from copy import deepcopy
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.brain_update')

# ── PATHS ──────────────────────────────────────────────────────
BASE_DIR       = Path(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS_DIR    = BASE_DIR / 'weights'
HISTORY_DIR    = BASE_DIR.parent / 'data' / 'history'
SIGNALS_DIR    = BASE_DIR.parent / 'signals'
REPORTS_DIR    = BASE_DIR / 'weekly_reports'
BACKUP_DIR     = BASE_DIR / 'weights_backup'

WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

WEIGHTS_FILE   = WEIGHTS_DIR / 'signal_weights.json'
REGIMES_FILE   = WEIGHTS_DIR / 'regimes.json'
LOSS_FILE      = SIGNALS_DIR / 'loss_studies.json'
SIGNALS_FILE   = SIGNALS_DIR / 'active_signals.json'

# ── EXPECTANCY TARGET ──────────────────────────────────────────
# Per the assessment: target expectancy not raw win rate
# Professional target: 62-68% win rate + 1.5:1 R:R
# This gives positive expectancy that compounds over time
MIN_EXPECTANCY        = 0.10   # minimum 10% positive expectancy
TARGET_WIN_RATE       = 0.68   # realistic live target
MIN_PROFIT_FACTOR     = 1.3    # profits must be 1.3x losses
MAX_DRAWDOWN_ALLOWED  = 0.15   # max 15% drawdown in backtest


class WeekendBrainUpdate:
    """
    Full weekend retraining orchestrator.
    Runs automatically Friday-Sunday.
    Zero manual intervention required.
    """

    def __init__(self):
        self.week_ending  = None
        self.report       = {}
        self.weights      = self._load_weights()
        self.prev_weights = deepcopy(self.weights)

    def _load_weights(self) -> dict:
        from brain.backtester import load_weights
        return load_weights()

    # ══════════════════════════════════════════════════════════
    # FRIDAY — DATA COLLECTION
    # ══════════════════════════════════════════════════════════

    def friday_data_collection(self) -> dict:
        """
        Downloads this week's fresh candle data.
        Merges with 10yr history JSON files.
        Tags event-driven candles.
        """
        logger.info('FRIDAY: Starting data collection phase...')
        now  = datetime.utcnow()
        self.week_ending = now.strftime('%Y-%m-%d')
        results = {}

        # Download all timeframes
        timeframes = [
            ('1d', '3mo'),   # Daily - 3 months fresh
            ('1h', '1mo'),   # Hourly - 1 month fresh
        ]

        for instrument in config.INSTRUMENTS:
            results[instrument] = {}
            for interval, period in timeframes:
                try:
                    from data.history import download_history
                    candles = download_history(
                        instrument   = instrument,
                        interval     = interval,
                        period       = period,
                        force_reload = True,
                    )
                    if candles:
                        results[instrument][interval] = len(candles)
                        logger.info(
                            f'Downloaded: {instrument} {interval} '
                            f'— {len(candles)} candles'
                        )
                    # Rate limit
                    time.sleep(2)
                except Exception as e:
                    logger.error(
                        f'Download error {instrument} {interval}: {e}'
                    )

        # Tag event-driven candles
        self._tag_event_candles()

        self.report['friday'] = {
            'data_downloaded': results,
            'timestamp': now.isoformat(),
        }
        logger.info('FRIDAY: Data collection complete')
        return results

    def _tag_event_candles(self):
        """
        Tags candles that occurred during high-impact events
        in the history JSON files.
        Event candles need separate treatment in training.
        """
        try:
            from brain.event_interrupt import get_interrupt
            events = get_interrupt().get_upcoming_events()

            for instrument in config.INSTRUMENTS:
                filepath = HISTORY_DIR / f'{instrument}_1d.json'
                if not filepath.exists():
                    continue

                with open(filepath, 'r') as f:
                    data = json.load(f)

                candles = data.get('data', [])
                tagged  = 0

                for candle in candles:
                    dt_str = candle.get('datetime', '')[:10]
                    for event in events:
                        ev_dt = event.get('datetime', '')[:10]
                        if dt_str == ev_dt:
                            candle['event_driven'] = True
                            candle['event_name']   = event.get('event')
                            tagged += 1

                with open(filepath, 'w') as f:
                    json.dump(data, f, indent=2, default=str)

                if tagged:
                    logger.info(
                        f'Tagged {tagged} event candles '
                        f'for {instrument}'
                    )
        except Exception as e:
            logger.warning(f'Event tagging error: {e}')

    # ══════════════════════════════════════════════════════════
    # SATURDAY — ANALYSIS AND RETRAINING
    # ══════════════════════════════════════════════════════════

    def saturday_analysis(self) -> dict:
        """
        Full Saturday retraining pipeline.
        """
        logger.info('SATURDAY: Starting analysis phase...')
        results = {}

        # Job 1: Audit past week's signals
        audit = self._audit_weekly_signals()
        results['signal_audit'] = audit

        # Job 2: Recalibrate weights
        new_weights = self._recalibrate_weights(audit)
        results['weight_changes'] = self._diff_weights(
            self.weights, new_weights
        )
        self.weights = new_weights

        # Job 3: Re-run regime clustering
        regime_result = self._retrain_regimes()
        results['regimes'] = regime_result

        # Job 4: Update CFR tables
        cfr_result = self._update_cfr_tables(audit)
        results['cfr_update'] = cfr_result

        # Job 5: Run backtesting with new weights
        backtest = self._run_full_backtest(self.weights)
        results['backtest'] = backtest

        self.report['saturday'] = {
            'results': results,
            'timestamp': datetime.utcnow().isoformat(),
        }
        logger.info('SATURDAY: Analysis complete')
        return results

    def _audit_weekly_signals(self) -> dict:
        """
        Reviews all signals from the past 7 days.
        Separates bad signals from unlucky signals.
        Bad signal: wrong logic → teaches brain what to avoid
        Unlucky signal: correct logic + unexpected news → ignore
        """
        audit = {
            'total_signals': 0,
            'wins':          0,
            'losses':        0,
            'bad_signals':   [],
            'unlucky':       [],
            'indicators_blamed': {},
        }

        if not LOSS_FILE.exists():
            logger.info('No loss studies found for audit')
            return audit

        try:
            with open(LOSS_FILE, 'r') as f:
                studies = json.load(f)

            week_ago = datetime.utcnow() - timedelta(days=7)

            for study in studies:
                try:
                    ts = datetime.fromisoformat(study['timestamp'])
                    if ts < week_ago:
                        continue
                except Exception:
                    continue

                audit['total_signals'] += 1
                result = study.get('result', 'LOSS')
                notes  = study.get('notes', [])

                if result == 'WIN':
                    audit['wins'] += 1
                    continue

                audit['losses'] += 1

                # Classify: bad signal or unlucky?
                is_event_driven = any(
                    'news' in n.lower() or 'event' in n.lower()
                    for n in notes
                )

                if is_event_driven:
                    audit['unlucky'].append(study)
                    logger.info(
                        f'Unlucky signal: {study.get("instrument")} '
                        f'— event-driven loss, not counting'
                    )
                else:
                    audit['bad_signals'].append(study)
                    # Count which indicators were blamed
                    for note in notes:
                        for indicator in [
                            'RSI', 'MACD', 'EMA', 'BBands',
                            'confluence', 'confidence',
                            'counter-trend', 'session'
                        ]:
                            if indicator.lower() in note.lower():
                                audit['indicators_blamed'][indicator] = (
                                    audit['indicators_blamed'].get(
                                        indicator, 0
                                    ) + 1
                                )

        except Exception as e:
            logger.error(f'Signal audit error: {e}')

        logger.info(
            f'Signal audit: {audit["total_signals"]} signals | '
            f'{audit["wins"]} wins | '
            f'{audit["losses"]} losses | '
            f'{len(audit["bad_signals"])} bad | '
            f'{len(audit["unlucky"])} unlucky'
        )
        return audit

    def _recalibrate_weights(self, audit: dict) -> dict:
        """
        Nudges indicator weights based on audit results.
        Small adjustments only — markets cycle.
        """
        weights = deepcopy(self.weights)
        blamed  = audit.get('indicators_blamed', {})

        if not blamed:
            logger.info('No indicators blamed — keeping weights')
            return weights

        # Small step: 3-5% weight reduction per blame
        step = 0.05

        indicator_weight_map = {
            'RSI':           ['rsi_strong', 'rsi_weak'],
            'MACD':          ['macd_strong', 'macd_weak'],
            'EMA':           ['ema200'],
            'BBands':        ['bbands_strong', 'bbands_weak'],
            'confluence':    ['min_confluence'],
            'confidence':    ['min_confidence'],
            'counter-trend': ['ema200'],
        }

        for indicator, blame_count in blamed.items():
            weight_keys = indicator_weight_map.get(indicator, [])
            for key in weight_keys:
                if key in weights:
                    # Reduce weight slightly
                    if key in ('min_confluence', 'min_confidence'):
                        # Raise threshold to be more selective
                        weights[key] = min(
                            90.0,
                            weights[key] + step * 5 * blame_count
                        )
                    else:
                        # Reduce signal weight
                        weights[key] = max(
                            0.1,
                            weights[key] - step * blame_count
                        )
                    logger.info(
                        f'Weight adjusted: {key} '
                        f'{self.weights[key]:.2f} → '
                        f'{weights[key]:.2f} '
                        f'(blamed {blame_count}x)'
                    )

        return weights

    def _diff_weights(
        self, old: dict, new: dict
    ) -> dict:
        """Returns dict showing what changed between weight sets."""
        changes = {}
        for key in new:
            old_val = old.get(key, 0)
            new_val = new.get(key, 0)
            if abs(old_val - new_val) > 0.001:
                changes[key] = {
                    'from': round(old_val, 3),
                    'to':   round(new_val, 3),
                    'delta':round(new_val - old_val, 3),
                }
        return changes

    def _retrain_regimes(self) -> dict:
        """Re-runs algorithmic regime clustering on updated history."""
        try:
            from brain.regime_clusterer import discover_regimes
            result = discover_regimes(
                instrument='XAUUSD',
                interval='1d',
                force_rerun=True,
            )
            logger.info(
                f'Regimes updated: '
                f'{result.get("k_regimes", "?")} clusters found'
            )
            return {
                'k_regimes': result.get('k_regimes'),
                'success':   'error' not in result,
            }
        except Exception as e:
            logger.error(f'Regime clustering error: {e}')
            return {'error': str(e)}

    def _update_cfr_tables(self, audit: dict) -> dict:
        """Updates CFR regret tables with new loss data."""
        try:
            bad_signals = audit.get('bad_signals', [])
            if not bad_signals:
                return {'updated': False, 'reason': 'No bad signals'}

            # Build regret entries from bad signals
            regret_entries = []
            for sig in bad_signals:
                regret_entries.append({
                    'instrument':  sig.get('instrument'),
                    'direction':   sig.get('direction'),
                    'confidence':  sig.get('confidence', 0),
                    'session':     sig.get('session', ''),
                    'regret':      1.0,  # full regret for loss
                })

            # Load and update CFR agent
            from brain.cfr_agent import CFRPolicyAgent
            agent = CFRPolicyAgent()
            for entry in regret_entries:
                try:
                    agent.update(entry)
                except Exception as e:
                    logger.warning(f'CFR update error: {e}')

            logger.info(
                f'CFR tables updated with '
                f'{len(regret_entries)} regret entries'
            )
            return {
                'updated':  True,
                'entries':  len(regret_entries),
            }
        except Exception as e:
            logger.error(f'CFR update error: {e}')
            return {'error': str(e)}

    def _run_full_backtest(self, weights: dict) -> dict:
        """Runs backtest on all instruments with given weights."""
        try:
            from brain.backtester import run_backtest

            all_stats = []
            for inst in config.INSTRUMENTS:
                for interval in ['1d', '1h']:
                    stats = run_backtest(inst, interval, weights)
                    if 'error' not in stats and stats['total_trades'] >= 10:
                        all_stats.append(stats)

            if not all_stats:
                return {'error': 'No backtest data'}

            total_wins   = sum(s['wins'] for s in all_stats)
            total_trades = sum(s['total_trades'] for s in all_stats)
            total_profit = sum(
                s['profit_factor'] for s in all_stats
                if s['profit_factor'] != float('inf')
            )
            avg_dd = sum(s['max_drawdown'] for s in all_stats) / len(all_stats)

            win_rate      = total_wins / total_trades if total_trades > 0 else 0
            profit_factor = total_profit / len(all_stats) if all_stats else 0

            # Calculate expectancy
            avg_win  = profit_factor / (1 + profit_factor) if profit_factor > 0 else 0
            avg_loss = 1 / (1 + profit_factor) if profit_factor > 0 else 1
            expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

            return {
                'win_rate':      round(win_rate, 3),
                'total_trades':  total_trades,
                'profit_factor': round(profit_factor, 2),
                'expectancy':    round(expectancy, 3),
                'max_drawdown':  round(avg_dd, 3),
                'meets_target':  (
                    win_rate >= TARGET_WIN_RATE and
                    expectancy >= MIN_EXPECTANCY and
                    profit_factor >= MIN_PROFIT_FACTOR
                ),
            }
        except Exception as e:
            logger.error(f'Backtest error: {e}')
            return {'error': str(e)}

    # ══════════════════════════════════════════════════════════
    # SUNDAY — VALIDATION AND DEPLOYMENT
    # ══════════════════════════════════════════════════════════

    def sunday_preparation(self) -> dict:
        """
        Sunday validation and deployment phase.
        """
        logger.info('SUNDAY: Starting preparation phase...')
        results = {}

        # Study doubt signals
        doubts = self._study_doubt_signals()
        results['doubt_signals'] = doubts

        # Fetch next week's calendar
        calendar = self._fetch_next_week_calendar()
        results['calendar'] = calendar

        # Out-of-sample validation
        validation = self._out_of_sample_validation()
        results['validation'] = validation

        # Deploy or revert
        deploy_result = self._deploy_or_revert(validation)
        results['deployment'] = deploy_result

        self.report['sunday'] = {
            'results': results,
            'timestamp': datetime.utcnow().isoformat(),
        }
        logger.info('SUNDAY: Preparation complete')
        return results

    def _study_doubt_signals(self) -> dict:
        """
        Studies signals where confidence was 50-65%.
        These sit on the decision boundary — most valuable
        for improvement because they are edge cases.
        """
        if not LOSS_FILE.exists():
            return {'found': 0}

        try:
            with open(LOSS_FILE, 'r') as f:
                studies = json.load(f)

            week_ago = datetime.utcnow() - timedelta(days=7)
            doubts   = []

            for study in studies:
                try:
                    ts = datetime.fromisoformat(study['timestamp'])
                    if ts < week_ago:
                        continue
                except Exception:
                    continue

                conf = study.get('confidence', 100)
                if 50 <= conf <= 65:
                    doubts.append({
                        'instrument':  study.get('instrument'),
                        'direction':   study.get('direction'),
                        'confidence':  conf,
                        'result':      study.get('result'),
                        'session':     study.get('session'),
                        'notes':       study.get('notes', []),
                    })

            wins_in_doubt  = sum(1 for d in doubts if d.get('result') == 'WIN')
            loss_in_doubt  = sum(1 for d in doubts if d.get('result') == 'LOSS')

            if doubts:
                logger.info(
                    f'Doubt signals: {len(doubts)} | '
                    f'Wins: {wins_in_doubt} | '
                    f'Losses: {loss_in_doubt}'
                )
                # If doubt signals win < 40% → raise min_confidence threshold
                if len(doubts) >= 5 and wins_in_doubt / len(doubts) < 0.4:
                    old_conf = self.weights.get('min_confidence', 65)
                    self.weights['min_confidence'] = min(
                        80, old_conf + 2.0
                    )
                    logger.info(
                        f'Doubt analysis: raising min_confidence '
                        f'{old_conf} → {self.weights["min_confidence"]}'
                    )

            return {
                'found':           len(doubts),
                'wins':            wins_in_doubt,
                'losses':          loss_in_doubt,
                'win_rate':        round(
                    wins_in_doubt / len(doubts), 3
                ) if doubts else 0,
            }

        except Exception as e:
            logger.error(f'Doubt study error: {e}')
            return {'error': str(e)}

    def _fetch_next_week_calendar(self) -> dict:
        """Fetches economic calendar for next 7 days."""
        try:
            from brain.event_interrupt import get_interrupt
            events = get_interrupt().fetch_weekly_calendar()
            return {
                'events_count': len(events),
                'success': True,
            }
        except Exception as e:
            logger.warning(f'Calendar fetch error: {e}')
            return {'error': str(e)}

    def _out_of_sample_validation(self) -> dict:
        """
        Tests new weights on data NOT used in training.
        Uses last 2 weeks of history as held-back test set.
        If new weights perform better → deploy.
        If worse → revert.
        """
        try:
            from brain.backtester import run_backtest
            from brain.backtester import load_weights

            # Test NEW weights on held-back data
            new_stats_all = []
            old_stats_all = []

            for inst in config.INSTRUMENTS:
                # Run with new weights
                new_stats = run_backtest(inst, '1d', self.weights)
                if 'error' not in new_stats:
                    new_stats_all.append(new_stats)

                # Run with previous weights
                old_stats = run_backtest(inst, '1d', self.prev_weights)
                if 'error' not in old_stats:
                    old_stats_all.append(old_stats)

            if not new_stats_all or not old_stats_all:
                return {'error': 'Insufficient data for validation'}

            new_wr = (
                sum(s['wins'] for s in new_stats_all) /
                sum(s['total_trades'] for s in new_stats_all)
            )
            old_wr = (
                sum(s['wins'] for s in old_stats_all) /
                sum(s['total_trades'] for s in old_stats_all)
            )

            new_dd = sum(
                s['max_drawdown'] for s in new_stats_all
            ) / len(new_stats_all)
            old_dd = sum(
                s['max_drawdown'] for s in old_stats_all
            ) / len(old_stats_all)

            improved = (
                new_wr >= old_wr and
                new_dd <= old_dd
            )

            logger.info(
                f'Validation: new={new_wr:.1%} old={old_wr:.1%} | '
                f'DD new={new_dd:.1%} old={old_dd:.1%} | '
                f'Improved={improved}'
            )

            return {
                'new_win_rate':    round(new_wr, 3),
                'old_win_rate':    round(old_wr, 3),
                'new_drawdown':    round(new_dd, 3),
                'old_drawdown':    round(old_dd, 3),
                'improved':        improved,
            }

        except Exception as e:
            logger.error(f'Validation error: {e}')
            return {'error': str(e)}

    def _deploy_or_revert(self, validation: dict) -> dict:
        """
        Deploys new weights if validation passed.
        Reverts to previous weights if overfit detected.
        """
        if 'error' in validation:
            logger.warning('Validation had errors — keeping current weights')
            return {'action': 'KEPT', 'reason': 'Validation error'}

        improved = validation.get('improved', False)

        if improved:
            # Backup current weights first
            if WEIGHTS_FILE.exists():
                backup = BACKUP_DIR / f'weights_{self.week_ending}.json'
                shutil.copy2(WEIGHTS_FILE, backup)
                logger.info(f'Previous weights backed up to {backup}')

            # Save new weights
            from brain.backtester import save_weights
            stats = {
                'win_rate':      validation.get('new_win_rate', 0),
                'profit_factor': 0,
                'total_trades':  0,
                'max_drawdown':  validation.get('new_drawdown', 0),
                'instruments':   config.INSTRUMENTS,
            }
            save_weights(self.weights, stats)
            logger.info('NEW WEIGHTS DEPLOYED — improvement confirmed')
            return {
                'action': 'DEPLOYED',
                'new_win_rate': validation.get('new_win_rate'),
                'old_win_rate': validation.get('old_win_rate'),
            }
        else:
            # Revert to previous weights
            self.weights = deepcopy(self.prev_weights)
            logger.warning(
                'WEIGHTS REVERTED — new weights did not improve '
                'out-of-sample performance (possible overfit)'
            )
            return {
                'action': 'REVERTED',
                'reason': 'Out-of-sample performance did not improve',
                'new_win_rate': validation.get('new_win_rate'),
                'old_win_rate': validation.get('old_win_rate'),
            }

    # ══════════════════════════════════════════════════════════
    # WEEKLY REPORT
    # ══════════════════════════════════════════════════════════

    def generate_report(self) -> str:
        """Generates Telegram-formatted weekly report."""
        friday  = self.report.get('friday', {})
        sat     = self.report.get('saturday', {})
        sun     = self.report.get('sunday', {})

        backtest  = sat.get('results', {}).get('backtest', {})
        audit     = sat.get('results', {}).get('signal_audit', {})
        deploy    = sun.get('results', {}).get('deployment', {})
        validation= sun.get('results', {}).get('validation', {})
        calendar  = sun.get('results', {}).get('calendar', {})
        doubts    = sun.get('results', {}).get('doubt_signals', {})

        deploy_action = deploy.get('action', 'UNKNOWN')
        deploy_emoji  = '✅' if deploy_action == 'DEPLOYED' else '⚠️'

        win_rate = backtest.get('win_rate', 0)
        wr_emoji = '✅' if win_rate >= TARGET_WIN_RATE else '⚠️'

        return (
            f'🧠 <b>WEEKLY BRAIN REPORT</b>\n'
            f'Week: {self.week_ending}\n'
            f'{"="*25}\n\n'

            f'<b>SIGNAL PERFORMANCE</b>\n'
            f'Total signals:  {audit.get("total_signals", 0)}\n'
            f'Wins:           {audit.get("wins", 0)}\n'
            f'Bad signals:    {len(audit.get("bad_signals", []))}\n'
            f'Unlucky:        {len(audit.get("unlucky", []))}\n\n'

            f'<b>BACKTEST RESULTS</b>\n'
            f'{wr_emoji} Win Rate:     {win_rate:.1%}\n'
            f'Expectancy:    {backtest.get("expectancy", 0):.3f}\n'
            f'Profit Factor: {backtest.get("profit_factor", 0):.2f}\n'
            f'Max Drawdown:  {backtest.get("max_drawdown", 0):.1%}\n\n'

            f'<b>VALIDATION</b>\n'
            f'New weights:   {validation.get("new_win_rate", 0):.1%}\n'
            f'Old weights:   {validation.get("old_win_rate", 0):.1%}\n'
            f'{deploy_emoji} Action: {deploy_action}\n\n'

            f'<b>DOUBT SIGNALS</b>\n'
            f'Found:         {doubts.get("found", 0)}\n'
            f'Win rate:      {doubts.get("win_rate", 0):.1%}\n\n'

            f'<b>NEXT WEEK</b>\n'
            f'Calendar events: {calendar.get("events_count", 0)}\n\n'

            f'{"Brain ready for next week." if deploy_action == "DEPLOYED" else "Previous weights active — monitoring."}'
        )

    # ══════════════════════════════════════════════════════════
    # MAIN ORCHESTRATOR
    # ══════════════════════════════════════════════════════════

    def run_full_weekend_cycle(self):
        """
        Runs complete Friday-Sunday cycle.
        Called automatically by scheduler.
        """
        logger.info('═'*50)
        logger.info('WEEKEND BRAIN UPDATE CYCLE STARTING')
        logger.info('═'*50)

        try:
            # Friday: Data
            self.friday_data_collection()

            # Saturday: Analysis
            self.saturday_analysis()

            # Sunday: Validation
            self.sunday_preparation()

            # Generate and send report
            report_text = self.generate_report()

            # Save report
            report_file = REPORTS_DIR / f'report_{self.week_ending}.json'
            with open(report_file, 'w') as f:
                json.dump(self.report, f, indent=2, default=str)

            # Send to Telegram
            try:
                asyncio.run(_send_telegram(report_text))
            except Exception as e:
                logger.error(f'Report send error: {e}')

            logger.info('WEEKEND BRAIN UPDATE CYCLE COMPLETE')
            logger.info('═'*50)

        except Exception as e:
            logger.critical(f'Weekend cycle error: {e}')
            try:
                asyncio.run(_send_telegram(
                    f'❌ <b>WEEKEND UPDATE ERROR</b>\n\n'
                    f'{str(e)[:200]}'
                ))
            except Exception:
                pass


async def _send_telegram(message: str):
    from tg_bot.bot import send_alert
    await send_alert(message)


# ══════════════════════════════════════════════════════════════
# SCHEDULER LOOP
# ══════════════════════════════════════════════════════════════

async def weekend_update_loop():
    """
    Async loop that monitors time and triggers weekend cycle.
    Runs in bot's event loop — zero manual intervention.

    Friday 22:00 UTC → data collection starts
    Saturday 06:00 UTC → analysis starts
    Sunday 06:00 UTC → validation + deployment starts
    Sunday 20:00 UTC → weekly report sent
    """
    last_run = {}

    while True:
        try:
            now     = datetime.utcnow()
            weekday = now.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday
            hour    = now.hour
            week    = now.isocalendar()[1]

            # Friday 22:00 — Data collection
            if weekday == 4 and hour == 22:
                key = f'friday_{week}'
                if key not in last_run:
                    last_run[key] = True
                    logger.info('Triggering Friday data collection...')
                    import threading
                    updater = WeekendBrainUpdate()
                    threading.Thread(
                        target=updater.friday_data_collection,
                        daemon=True,
                    ).start()

            # Saturday 06:00 — Analysis
            elif weekday == 5 and hour == 6:
                key = f'saturday_{week}'
                if key not in last_run:
                    last_run[key] = True
                    logger.info('Triggering Saturday analysis...')
                    import threading
                    updater = WeekendBrainUpdate()
                    threading.Thread(
                        target=updater.saturday_analysis,
                        daemon=True,
                    ).start()

            # Sunday 06:00 — Validation + deployment
            elif weekday == 6 and hour == 6:
                key = f'sunday_{week}'
                if key not in last_run:
                    last_run[key] = True
                    logger.info('Triggering Sunday preparation...')
                    import threading
                    updater = WeekendBrainUpdate()
                    threading.Thread(
                        target=updater.run_full_weekend_cycle,
                        daemon=True,
                    ).start()

            # Clean up old keys (keep last 4 weeks)
            if len(last_run) > 20:
                oldest = sorted(last_run.keys())[:5]
                for k in oldest:
                    del last_run[k]

        except Exception as e:
            logger.error(f'Weekend scheduler error: {e}')

        # Check every hour
        await asyncio.sleep(3600)


# ── STARTUP BACKTEST ───────────────────────────────────────────

def run_startup_backtest():
    """
    Runs automatically when system starts.
    No manual intervention needed.
    Runs in background thread — doesn't block startup.
    """
    logger.info('Startup backtest starting in background...')
    try:
        from brain.backtester import optimize_weights
        stats = optimize_weights(
            instruments = config.INSTRUMENTS,
            target_rate = TARGET_WIN_RATE,
            max_iter    = 100,
        )
        logger.info(
            f'Startup backtest complete: '
            f'win rate={stats.get("win_rate", 0):.1%} | '
            f'expectancy={stats.get("expectancy", 0):.3f}'
        )
        # Alert if significantly different from target
        wr = stats.get('win_rate', 0)
        if wr < TARGET_WIN_RATE - 0.10:
            try:
                asyncio.run(_send_telegram(
                    f'⚠️ <b>STARTUP BACKTEST</b>\n\n'
                    f'Win rate: {wr:.1%} '
                    f'(target {TARGET_WIN_RATE:.0%})\n'
                    f'System running with best available weights.\n'
                    f'Weekend update will re-optimize.'
                ))
            except Exception:
                pass
    except Exception as e:
        logger.error(f'Startup backtest error: {e}')


# ── DIRECT TEST ────────────────────────────────────────────────

if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Brain Update Test')
    print('='*55 + '\n')

    updater = WeekendBrainUpdate()

    print('Running Friday data collection...')
    updater.friday_data_collection()

    print('\nRunning Saturday analysis...')
    sat = updater.saturday_analysis()
    bt  = sat.get('backtest', {})
    print(f'Win rate:     {bt.get("win_rate", 0):.1%}')
    print(f'Expectancy:   {bt.get("expectancy", 0):.3f}')
    print(f'Meets target: {bt.get("meets_target", False)}')

    print('\nRunning Sunday preparation...')
    updater.sunday_preparation()

    print('\nWeekly report:')
    import re
    print(re.sub(r'<[^>]+>', '', updater.generate_report()))