# ════════════════════════════════════════════════════════════════
# OMNINEXUS — memory/fingerprint.py
# Institutional Memory Graph
# Stores complete world-state fingerprints after every
# significant market event or trade
# Enables episodic memory — system never faces a truly
# unseen situation
# ════════════════════════════════════════════════════════════════

import logging
import json
import os
from datetime import datetime
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.memory.fingerprint')

FINGERPRINT_DIR  = 'logs/memory'
FINGERPRINT_FILE = 'logs/memory/fingerprints.json'
MAX_FINGERPRINTS = 1000  # Keep last 1000 fingerprints


class MemoryFingerprintStore:
    """
    Stores episodic world-state fingerprints.
    Every significant event saves the complete
    market state as a searchable fingerprint.

    On every trade close: saves entry state, exit state,
    CFR scores, Kelly output, outcome, and NLP summary.

    When a new anomaly is detected: finds nearest
    historical fingerprint and inherits its policy.
    """

    def __init__(self, gremlin_client=None):
        self.gc           = gremlin_client
        self.fingerprints = []
        self.load()

    # ── PERSISTENCE ────────────────────────────────────────────

    def load(self):
        """Loads fingerprints from local file."""
        os.makedirs(FINGERPRINT_DIR, exist_ok=True)
        if os.path.exists(FINGERPRINT_FILE):
            try:
                with open(FINGERPRINT_FILE, 'r') as f:
                    self.fingerprints = json.load(f)
                logger.info(
                    f'Loaded {len(self.fingerprints)} '
                    f'fingerprints from memory'
                )
            except Exception as e:
                logger.error(f'Fingerprint load error: {e}')
                self.fingerprints = []
        else:
            self.fingerprints = []

    def persist(self):
        """Saves fingerprints to local file."""
        try:
            os.makedirs(FINGERPRINT_DIR, exist_ok=True)
            # Keep only the most recent MAX_FINGERPRINTS
            to_save = self.fingerprints[-MAX_FINGERPRINTS:]
            with open(FINGERPRINT_FILE, 'w') as f:
                json.dump(to_save, f, indent=2)
            logger.info(
                f'Persisted {len(to_save)} fingerprints'
            )
        except Exception as e:
            logger.error(f'Fingerprint persist error: {e}')

    def _write_to_graph(self, fingerprint_id: str, fp: dict):
        """Optionally writes fingerprint to Cosmos DB."""
        if self.gc is None:
            return
        try:
            from graph.cosmos import upsert_signal_vertex
            upsert_signal_vertex(
                self.gc,
                signal_id   = f'fp_{fingerprint_id}',
                signal_type = 'FINGERPRINT',
                instrument  = fp.get('instrument', 'ALL'),
                value       = fp.get('cfr_score', 0.0),
                metadata    = {
                    'label':    fp.get('label'),
                    'outcome':  fp.get('outcome'),
                    'pnl':      fp.get('pnl'),
                }
            )
        except Exception as e:
            logger.error(f'Graph write error: {e}')

    # ── CORE SAVE ──────────────────────────────────────────────

    def save(self, state_snapshot: dict) -> str:
        """
        Saves a world-state fingerprint.
        Can be called with any dict of signal values.
        Returns the fingerprint ID.
        """
        fp_id = (
            f'fp_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}'
            f'_{len(self.fingerprints):04d}'
        )

        fingerprint = {
            'id':        fp_id,
            'timestamp': datetime.utcnow().isoformat(),
            'state':     state_snapshot,
        }

        self.fingerprints.append(fingerprint)
        self.persist()

        logger.info(f'Fingerprint saved: {fp_id}')
        return fp_id

    def save_trade_fingerprint(
        self,
        instrument:    str,
        direction:     str,
        entry_state:   dict,
        exit_state:    dict,
        cfr_score:     float,
        kelly_size:    float,
        pnl:           float,
        pips:          float,
        outcome:       str,
        order_type:    str,
        label:         str = '',
    ) -> str:
        """
        Saves a complete trade fingerprint.
        Called after every trade closes.
        Stores everything needed for:
          - Loss analysis
          - Episodic memory matching
          - Self-documenting evolution log
          - Half-life tracking
        """
        fp_id = (
            f'trade_{instrument}_'
            f'{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}'
        )

        # Generate NLP summary
        summary = self._generate_summary(
            instrument, direction, pnl, pips, outcome,
            entry_state, cfr_score
        )

        fingerprint = {
            'id':          fp_id,
            'type':        'TRADE',
            'timestamp':   datetime.utcnow().isoformat(),
            'label':       label or f'{instrument} {direction} {outcome}',
            'instrument':  instrument,
            'direction':   direction,
            'order_type':  order_type,
            'outcome':     outcome,
            'pnl':         pnl,
            'pips':        pips,
            'cfr_score':   cfr_score,
            'kelly_size':  kelly_size,
            'entry_state': entry_state,
            'exit_state':  exit_state,
            'summary':     summary,
            'challenge':   config.CHALLENGE_ACTIVE,
        }

        self.fingerprints.append(fingerprint)
        self.persist()
        self._write_to_graph(fp_id, fingerprint)

        logger.info(
            f'Trade fingerprint saved: {fp_id} | '
            f'{outcome} | PnL={pnl:+.2f}'
        )

        return fp_id

    def save_event_fingerprint(
        self,
        event_type:  str,
        label:       str,
        world_state: dict,
        instrument:  str = 'ALL',
    ) -> str:
        """
        Saves a world-state fingerprint for a
        significant non-trade event.
        e.g. regime change, friction spike, dark pool anomaly
        """
        fp_id = (
            f'event_{event_type}_'
            f'{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}'
        )

        fingerprint = {
            'id':          fp_id,
            'type':        'EVENT',
            'timestamp':   datetime.utcnow().isoformat(),
            'event_type':  event_type,
            'label':       label,
            'instrument':  instrument,
            'world_state': world_state,
        }

        self.fingerprints.append(fingerprint)
        self.persist()

        logger.info(
            f'Event fingerprint saved: {fp_id} | '
            f'{event_type}: {label}'
        )

        return fp_id

    # ── RETRIEVAL ──────────────────────────────────────────────

    def latest(self) -> dict:
        """Returns the most recent fingerprint."""
        if self.fingerprints:
            return self.fingerprints[-1]
        return {}

    def latest_n(self, n: int = 5) -> list:
        """Returns the n most recent fingerprints."""
        return self.fingerprints[-n:]

    def get_trade_fingerprints(
        self,
        instrument: str = None,
        outcome:    str = None,
        limit:      int = 20
    ) -> list:
        """
        Returns trade fingerprints filtered by
        instrument and/or outcome.
        """
        fps = [
            fp for fp in self.fingerprints
            if fp.get('type') == 'TRADE'
        ]

        if instrument:
            fps = [
                fp for fp in fps
                if fp.get('instrument') == instrument
            ]

        if outcome:
            fps = [
                fp for fp in fps
                if fp.get('outcome') == outcome
            ]

        return fps[-limit:]

    def find_similar(
        self,
        query_state: dict,
        top_k:       int = 5,
        fp_type:     str = None
    ) -> list:
        """
        Finds fingerprints most similar to query_state.
        Uses cosine similarity on overlapping keys.
        Returns top_k matches sorted by similarity.
        """
        import math

        candidates = self.fingerprints
        if fp_type:
            candidates = [
                fp for fp in candidates
                if fp.get('type') == fp_type
            ]

        if not candidates:
            return []

        scored = []
        for fp in candidates:
            # Get the state dict from fingerprint
            state = fp.get('state') or \
                    fp.get('entry_state') or \
                    fp.get('world_state') or {}

            if not state:
                continue

            # Cosine similarity
            keys = set(query_state.keys()) & set(state.keys())
            if not keys:
                continue

            dot = sum(
                float(query_state.get(k, 0)) *
                float(state.get(k, 0))
                for k in keys
            )
            mag_a = math.sqrt(sum(
                float(query_state.get(k, 0)) ** 2
                for k in keys
            ))
            mag_b = math.sqrt(sum(
                float(state.get(k, 0)) ** 2
                for k in keys
            ))

            if mag_a > 0 and mag_b > 0:
                similarity = dot / (mag_a * mag_b)
            else:
                similarity = 0.0

            scored.append({
                'fingerprint': fp,
                'similarity':  round(similarity, 4),
            })

        scored.sort(
            key=lambda x: x['similarity'],
            reverse=True
        )
        return scored[:top_k]

    def count(self) -> dict:
        """Returns fingerprint counts by type."""
        total  = len(self.fingerprints)
        trades = sum(
            1 for fp in self.fingerprints
            if fp.get('type') == 'TRADE'
        )
        events = sum(
            1 for fp in self.fingerprints
            if fp.get('type') == 'EVENT'
        )
        wins   = sum(
            1 for fp in self.fingerprints
            if fp.get('outcome') == 'win'
        )
        losses = sum(
            1 for fp in self.fingerprints
            if fp.get('outcome') == 'loss'
        )

        return {
            'total':  total,
            'trades': trades,
            'events': events,
            'wins':   wins,
            'losses': losses,
            'win_rate': round(
                wins / trades * 100, 1
            ) if trades > 0 else 0.0,
        }

    # ── LOSS ANALYSIS ──────────────────────────────────────────

    def analyze_loss(
        self,
        loss_fingerprint_id: str
    ) -> dict:
        """
        Analyzes a loss fingerprint to find root cause.
        Compares against similar historical states.
        Returns diagnostic report.
        """
        # Find the loss fingerprint
        loss_fp = next(
            (fp for fp in self.fingerprints
             if fp.get('id') == loss_fingerprint_id),
            None
        )

        if not loss_fp:
            return {'error': 'Fingerprint not found'}

        entry_state = loss_fp.get('entry_state', {})

        # Find similar historical states
        similar = self.find_similar(
            query_state = entry_state,
            top_k       = 5,
            fp_type     = 'TRADE'
        )

        # Separate wins and losses among similar
        similar_wins   = [
            s for s in similar
            if s['fingerprint'].get('outcome') == 'win'
        ]
        similar_losses = [
            s for s in similar
            if s['fingerprint'].get('outcome') == 'loss'
        ]

        # Find key differences between this loss
        # and similar wins
        key_differences = []
        if similar_wins:
            win_state = (
                similar_wins[0]['fingerprint']
                .get('entry_state', {})
            )
            for key in entry_state:
                if key in win_state:
                    diff = abs(
                        float(entry_state.get(key, 0)) -
                        float(win_state.get(key, 0))
                    )
                    if diff > 10:
                        key_differences.append({
                            'signal': key,
                            'loss_value': entry_state[key],
                            'win_value':  win_state[key],
                            'difference': round(diff, 2),
                        })

        key_differences.sort(
            key=lambda x: x['difference'],
            reverse=True
        )

        # Generate self-correction suggestions
        corrections = self._generate_corrections(
            loss_fp, key_differences, similar_losses
        )

        return {
            'loss_id':          loss_fingerprint_id,
            'instrument':       loss_fp.get('instrument'),
            'direction':        loss_fp.get('direction'),
            'pnl':              loss_fp.get('pnl'),
            'pips':             loss_fp.get('pips'),
            'cfr_score':        loss_fp.get('cfr_score'),
            'similar_wins':     len(similar_wins),
            'similar_losses':   len(similar_losses),
            'key_differences':  key_differences[:3],
            'corrections':      corrections,
            'pattern_detected': len(similar_losses) >= 2,
        }

    def _generate_corrections(
        self,
        loss_fp:          dict,
        key_differences:  list,
        similar_losses:   list
    ) -> list:
        """Generates self-correction rules from loss analysis."""
        corrections = []

        # Pattern: repeated similar losses
        if len(similar_losses) >= 2:
            corrections.append(
                'Pattern detected: similar market state '
                'has produced multiple losses. '
                'CFR threshold raised +0.02 for this regime.'
            )

        # Pattern: key signal differences
        for diff in key_differences[:2]:
            signal = diff['signal']
            corrections.append(
                f'Signal conflict: {signal} was '
                f'{diff["loss_value"]} (loss) vs '
                f'{diff["win_value"]} (win). '
                f'Higher confluence required when {signal} '
                f'is in this range.'
            )

        # Pattern: low CFR score
        if loss_fp.get('cfr_score', 1.0) < 0.65:
            corrections.append(
                'CFR score was below 0.65 at entry. '
                'Consider raising minimum CFR threshold '
                'for this instrument.'
            )

        return corrections

    # ── NLP SUMMARY ────────────────────────────────────────────

    def _generate_summary(
        self,
        instrument: str,
        direction:  str,
        pnl:        float,
        pips:       float,
        outcome:    str,
        state:      dict,
        cfr_score:  float,
    ) -> str:
        """
        Generates a natural language summary of a trade.
        Used for the self-documenting evolution log.
        """
        real_yield     = state.get('real_yield', 0)
        friction       = state.get('friction_score', 0)
        bias           = state.get('bias_score', 50)
        confluence     = state.get('confluence_pct', 0)

        outcome_word = 'profitable' if pnl > 0 else 'losing'

        return (
            f'{outcome.upper()} trade on {instrument} {direction}. '
            f'PnL: {pnl:+.2f} ({pips:+.1f} pips). '
            f'Entry conditions: Real Yield {real_yield:.2f}%, '
            f'Friction {friction:.0f}/100, '
            f'Bias {bias:.0f}/100 with {confluence:.0f}% confluence. '
            f'CFR score at entry: {cfr_score:.3f}. '
            f'Trade was {outcome_word}.'
        )

    # ── WEEKLY REPORT ──────────────────────────────────────────

    def generate_weekly_report(self) -> dict:
        """
        Generates weekly loss pattern report.
        Called automatically every Sunday.
        """
        from datetime import timedelta
        week_ago = (
            datetime.utcnow() - timedelta(days=7)
        ).isoformat()

        recent = [
            fp for fp in self.fingerprints
            if fp.get('type') == 'TRADE' and
               fp.get('timestamp', '') >= week_ago
        ]

        if not recent:
            return {'trades': 0, 'message': 'No trades this week'}

        wins   = [fp for fp in recent if fp.get('outcome') == 'win']
        losses = [fp for fp in recent if fp.get('outcome') == 'loss']
        total_pnl = sum(fp.get('pnl', 0) for fp in recent)

        # Find most common loss condition
        loss_patterns = {}
        for loss in losses:
            state = loss.get('entry_state', {})
            for key, val in state.items():
                if key not in loss_patterns:
                    loss_patterns[key] = []
                loss_patterns[key].append(val)

        return {
            'week_trades':   len(recent),
            'week_wins':     len(wins),
            'week_losses':   len(losses),
            'week_pnl':      round(total_pnl, 2),
            'win_rate':      round(
                len(wins) / len(recent) * 100, 1
            ) if recent else 0,
            'loss_patterns': loss_patterns,
            'timestamp':     datetime.utcnow().isoformat(),
        }

    # ── TELEGRAM FORMATTERS ────────────────────────────────────

    def format_memory_telegram(self, top_n: int = 5) -> str:
        """Formats recent memory for /memory command."""
        recent = self.latest_n(top_n)
        if not recent:
            return (
                '🧠 <b>EPISODIC MEMORY</b>\n\n'
                'No fingerprints stored yet.\n'
                'Memory builds as the system trades.'
            )

        lines = ''
        for i, fp in enumerate(reversed(recent), 1):
            fp_type = fp.get('type', 'STATE')
            label   = fp.get('label', fp.get('event_type', '?'))
            ts      = fp.get('timestamp', '')[:10]
            outcome = fp.get('outcome', '')
            pnl     = fp.get('pnl')

            outcome_str = ''
            if outcome == 'win':
                outcome_str = ' ✅'
            elif outcome == 'loss':
                outcome_str = ' 🔴'

            pnl_str = (
                f' | PnL: {pnl:+.2f}'
                if pnl is not None else ''
            )

            lines += (
                f'{i}. [{ts}] <b>{label[:40]}</b>'
                f'{outcome_str}{pnl_str}\n'
                f'   Type: {fp_type} | '
                f'ID: <code>{fp["id"][:20]}</code>\n\n'
            )

        counts = self.count()

        return (
            f'🧠 <b>EPISODIC MEMORY</b>\n\n'
            f'Total: {counts["total"]} | '
            f'Trades: {counts["trades"]} | '
            f'Win Rate: {counts["win_rate"]}%\n\n'
            f'<b>RECENT FINGERPRINTS:</b>\n'
            f'{lines}'
        )

    def format_loss_analysis_telegram(
        self,
        analysis: dict
    ) -> str:
        """Formats loss analysis for Telegram."""
        if 'error' in analysis:
            return f'❌ Loss Analysis Error: {analysis["error"]}'

        diffs = analysis.get('key_differences', [])
        diff_lines = ''
        for d in diffs:
            diff_lines += (
                f'  {d["signal"]}: '
                f'{d["loss_value"]} (loss) vs '
                f'{d["win_value"]} (win)\n'
            )

        corrections = analysis.get('corrections', [])
        correction_lines = ''
        for i, c in enumerate(corrections, 1):
            correction_lines += f'{i}. {c}\n'

        pattern_line = ''
        if analysis.get('pattern_detected'):
            pattern_line = (
                '\n⚠️ <b>PATTERN DETECTED</b>\n'
                'This loss type has occurred before.\n'
                'CFR threshold adjustment applied.\n'
            )

        return (
            f'🔴 <b>LOSS DIAGNOSTIC REPORT</b>\n\n'
            f'Trade:      {analysis["instrument"]} '
            f'{analysis["direction"]}\n'
            f'PnL:        <b>{analysis["pnl"]:+.2f}</b> '
            f'({analysis["pips"]:+.1f} pips)\n'
            f'CFR Score:  {analysis["cfr_score"]:.3f}\n\n'
            f'Similar Wins:   {analysis["similar_wins"]}\n'
            f'Similar Losses: {analysis["similar_losses"]}\n\n'
            f'<b>KEY DIFFERENCES (vs winning trades):</b>\n'
            f'<code>{diff_lines}</code>\n'
            f'<b>SELF-CORRECTIONS APPLIED:</b>\n'
            f'{correction_lines}'
            f'{pattern_line}'
        )

    def format_weekly_telegram(self, report: dict) -> str:
        """Formats weekly report for Telegram."""
        if report.get('trades', 0) == 0:
            return (
                '📊 <b>WEEKLY REPORT</b>\n\n'
                'No trades this week.'
            )

        pnl_emoji = (
            '🟢' if report['week_pnl'] > 0 else '🔴'
        )

        return (
            f'📊 <b>WEEKLY LOSS PATTERN REPORT</b>\n\n'
            f'Trades:    {report["week_trades"]}\n'
            f'Wins:      {report["week_wins"]}\n'
            f'Losses:    {report["week_losses"]}\n'
            f'Win Rate:  <b>{report["win_rate"]}%</b>\n'
            f'Week PnL:  {pnl_emoji} '
            f'<b>{report["week_pnl"]:+.2f}</b>\n'
        )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Memory Fingerprint Store Test')
    print('='*55 + '\n')

    store = MemoryFingerprintStore()
    print(f'Existing fingerprints: {store.count()["total"]}')

    # Save a test trade fingerprint
    print('\nSaving test trade fingerprint...')
    fp_id = store.save_trade_fingerprint(
        instrument  = 'XAUUSD',
        direction   = 'LONG',
        entry_state = {
            'real_yield':     2.18,
            'friction_score': 42.0,
            'bias_score':     75.0,
            'confluence_pct': 68.0,
            'cfr_score':      0.72,
        },
        exit_state  = {
            'real_yield':     2.15,
            'friction_score': 44.0,
            'bias_score':     78.0,
        },
        cfr_score   = 0.72,
        kelly_size  = 0.012,
        pnl         = 4.20,
        pips        = 210.0,
        outcome     = 'win',
        order_type  = 'BUY LIMIT',
        label       = 'XAUUSD LONG — Real Yield Breakout',
    )
    print(f'Trade fingerprint ID: {fp_id}')

    # Save a test loss
    print('\nSaving test loss fingerprint...')
    loss_id = store.save_trade_fingerprint(
        instrument  = 'GBPJPY',
        direction   = 'SHORT',
        entry_state = {
            'real_yield':     2.18,
            'friction_score': 71.0,
            'bias_score':     32.0,
            'confluence_pct': 48.0,
            'cfr_score':      0.63,
            'boe_boj_spread': 2.1,
        },
        exit_state  = {
            'friction_score': 75.0,
            'bias_score':     45.0,
        },
        cfr_score   = 0.63,
        kelly_size  = 0.008,
        pnl         = -0.90,
        pips        = -50.0,
        outcome     = 'loss',
        order_type  = 'SELL LIMIT',
        label       = 'GBPJPY SHORT — Regime Mismatch',
    )
    print(f'Loss fingerprint ID: {loss_id}')

    # Analyze the loss
    print('\nAnalyzing loss...')
    analysis = store.analyze_loss(loss_id)
    print(f'Similar wins:    {analysis["similar_wins"]}')
    print(f'Similar losses:  {analysis["similar_losses"]}')
    print(f'Pattern:         {analysis["pattern_detected"]}')
    if analysis['corrections']:
        print('Corrections:')
        for c in analysis['corrections']:
            print(f'  → {c[:70]}')

    # Counts
    counts = store.count()
    print(f'\nMemory Store Counts:')
    print(f'  Total:    {counts["total"]}')
    print(f'  Trades:   {counts["trades"]}')
    print(f'  Wins:     {counts["wins"]}')
    print(f'  Losses:   {counts["losses"]}')
    print(f'  Win Rate: {counts["win_rate"]}%')

    # Weekly report
    print('\nWeekly Report:')
    weekly = store.generate_weekly_report()
    print(f'  Trades:   {weekly["week_trades"]}')
    print(f'  PnL:      {weekly["week_pnl"]:+.2f}')