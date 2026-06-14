# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/cfr_agent.py
# CFR Policy Agent
# Two agents: SLOW (long timeframe) and FAST (session-transition)
# Selected by Hi-DARTS Meta-Agent based on regime state
# ════════════════════════════════════════════════════════════════

import logging
import json
import os
import numpy as np
from datetime import datetime
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.cfr_agent')

CFR_STATE_FILE = 'logs/cfr_state.json'


class CFRPolicyAgent:
    """
    Counterfactual Regret Minimization policy agent.

    Two operating modes:
      SLOW: Conservative, long-timeframe analysis
            Used in stable regimes
            Higher CFR threshold required
            Larger minimum hold time

      FAST: Aggressive, session-transition focused
            Used in anomalous/volatile regimes
            Lower CFR threshold
            Shorter hold time, tighter stops
    """

    def __init__(self, mode: str = 'slow'):
        """
        mode: 'slow' or 'fast'
        """
        self.mode     = mode.lower()
        self.strategy = {}
        self.history  = []

        # Mode-specific parameters
        if self.mode == 'slow':
            self.cfr_threshold     = config.CFR_REGRET_THRESHOLD
            self.kelly_multiplier  = 1.0
            self.min_confluence    = 60.0
            self.min_bias_distance = 15.0
            self.description       = 'Conservative long-timeframe'
        else:
            self.cfr_threshold     = config.CFR_REGRET_THRESHOLD * 0.85
            self.kelly_multiplier  = 0.6
            self.min_confluence    = 50.0
            self.min_bias_distance = 10.0
            self.description       = 'Aggressive session-transition'

        logger.info(
            f'CFR Agent initialized: mode={self.mode} | '
            f'threshold={self.cfr_threshold:.3f} | '
            f'kelly_mult={self.kelly_multiplier}'
        )

    def evaluate(self, state: dict) -> dict:
        """
        Evaluates current market state and returns
        CFR policy decision.

        state keys expected:
          bias_score, confluence_pct, regime_score,
          friction_score, real_yield, spread_value,
          dark_pool_signal, behavioral_score,
          stop_hunt_active, instrument
        """
        bias_score     = state.get('bias_score', 50.0)
        confluence_pct = state.get('confluence_pct', 0.0)
        regime_score   = state.get('regime_score', 50.0)
        friction_score = state.get('friction_score', 35.0)
        real_yield     = state.get('real_yield', 1.5)
        spread_value   = state.get('spread_value', 0.0)
        instrument     = state.get('instrument', 'XAUUSD')

        # ── Regret Calculation ─────────────────────────────────
        regret_components = {}
        total_boost   = 0.0
        total_penalty = 0.0

        # 1. Bias conviction
        conviction = abs(bias_score - 50) / 50.0
        if conviction < (self.min_bias_distance / 50.0):
            total_penalty += 0.20
            regret_components['bias'] = 'NEUTRAL_PENALTY'
        else:
            total_boost += conviction * 0.30
            regret_components['bias'] = f'CONVICTION_{conviction:.2f}'

        # 2. Confluence
        if confluence_pct >= self.min_confluence:
            boost = (confluence_pct / 100.0) * 0.25
            total_boost += boost
            regret_components['confluence'] = 'PASS'
        else:
            total_penalty += 0.15
            regret_components['confluence'] = 'LOW_PENALTY'

        # 3. Regime state
        if regime_score >= 70:
            total_boost += 0.15
            regret_components['regime'] = 'STABLE_BOOST'
        elif regime_score < 30:
            if self.mode == 'slow':
                total_penalty += 0.25
                regret_components['regime'] = 'ANOMALY_BLOCK'
            else:
                # Fast agent is designed for anomalous regimes
                total_boost += 0.05
                regret_components['regime'] = 'ANOMALY_ACCEPTED'
        else:
            regret_components['regime'] = 'NEUTRAL'

        # 4. Friction
        if friction_score > 85:
            total_penalty += 0.15
            regret_components['friction'] = 'EXTREME_PENALTY'
        elif friction_score > config.FRICTION_THRESHOLD:
            if self.mode == 'slow':
                total_penalty += 0.08
                regret_components['friction'] = 'HIGH_PENALTY'
            else:
                # Fast agent: friction = opportunity
                total_boost += 0.05
                regret_components['friction'] = 'HIGH_ACCEPTED'
        else:
            regret_components['friction'] = 'NORMAL'

        # 5. Real yield extremes
        if abs(real_yield) > 3.5:
            total_penalty += 0.10
            regret_components['real_yield'] = 'EXTREME'
        else:
            regret_components['real_yield'] = 'NORMAL'

        # 6. Mode-specific bonuses
        if self.mode == 'fast':
            if state.get('stop_hunt_active'):
                total_boost += 0.12
                regret_components['stop_hunt'] = 'ACTIVE_BOOST'
            if state.get('dark_pool_signal'):
                total_boost += 0.08
                regret_components['dark_pool'] = 'SIGNAL_BOOST'

        # ── Final Score ────────────────────────────────────────
        base_score   = 0.40
        action_score = base_score + total_boost - total_penalty
        action_score = max(0.0, min(1.0, action_score))

        # Position allowed check
        position_allowed = action_score >= self.cfr_threshold

        # Direction
        if bias_score > 55:
            direction = 'LONG'
        elif bias_score < 45:
            direction = 'SHORT'
        else:
            direction = 'NEUTRAL'
            position_allowed = False

        # Kelly multiplier (mode-specific)
        kelly_adjustment = self.kelly_multiplier * action_score

        result = {
            'mode':              self.mode,
            'action_score':      round(action_score, 3),
            'position_allowed':  position_allowed,
            'direction':         direction,
            'kelly_adjustment':  round(kelly_adjustment, 3),
            'cfr_threshold':     self.cfr_threshold,
            'regret_components': regret_components,
            'total_boost':       round(total_boost, 3),
            'total_penalty':     round(total_penalty, 3),
            'instrument':        instrument,
            'timestamp':         datetime.utcnow().isoformat(),
        }

        # Store in history
        self.history.append({
            'timestamp':    result['timestamp'],
            'action_score': result['action_score'],
            'allowed':      result['position_allowed'],
            'direction':    result['direction'],
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]

        # Save strategy state
        self.strategy['last_result'] = result

        logger.info(
            f'CFR [{self.mode.upper()}]: '
            f'score={action_score:.3f} | '
            f'allowed={position_allowed} | '
            f'direction={direction} | '
            f'kelly_adj={kelly_adjustment:.3f}'
        )

        return result

    def update(self, reward: float, trade_outcome: dict = None):
        """
        Updates strategy based on trade outcome.
        Positive reward = trade was profitable.
        Negative reward = trade was a loss.
        """
        self.strategy['last_reward'] = reward

        if trade_outcome:
            self.strategy['last_trade'] = trade_outcome

        # Adapt thresholds based on performance
        if reward < -0.5:
            # Loss: slightly increase threshold (more selective)
            self.cfr_threshold = min(
                0.95,
                self.cfr_threshold + 0.01
            )
            logger.info(
                f'CFR threshold raised to '
                f'{self.cfr_threshold:.3f} after loss'
            )
        elif reward > 0.5:
            # Win: slightly relax threshold
            self.cfr_threshold = max(
                config.CFR_REGRET_THRESHOLD * 0.8,
                self.cfr_threshold - 0.005
            )

    def get_recent_performance(self) -> dict:
        """Returns win rate from recent history."""
        if not self.history:
            return {'trades': 0, 'win_rate': 0.0}

        recent = self.history[-20:]
        allowed = [h for h in recent if h['allowed']]

        return {
            'trades':      len(allowed),
            'mode':        self.mode,
            'threshold':   self.cfr_threshold,
        }

    def format_telegram(self, result: dict) -> str:
        """Formats CFR result for Telegram."""
        allowed_str = (
            '✅ POSITION ALLOWED'
            if result['position_allowed']
            else '🚫 POSITION BLOCKED'
        )

        components = result.get('regret_components', {})
        comp_str = ' · '.join(
            f'{k}:{v}' for k, v in
            list(components.items())[:4]
        )

        return (
            f'🧠 <b>CFR POLICY [{result["mode"].upper()}]</b>\n\n'
            f'{allowed_str}\n'
            f'Action Score:  <b>{result["action_score"]:.3f}</b>\n'
            f'Threshold:     {result["cfr_threshold"]:.3f}\n'
            f'Direction:     <b>{result["direction"]}</b>\n'
            f'Kelly Adj:     {result["kelly_adjustment"]:.3f}\n\n'
            f'Boost:   +{result["total_boost"]:.3f}\n'
            f'Penalty: -{result["total_penalty"]:.3f}\n\n'
            f'<code>{comp_str}</code>'
        )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — CFR Policy Agent Test')
    print('='*55 + '\n')

    test_state = {
        'bias_score':      78.0,
        'confluence_pct':  68.0,
        'regime_score':    75.0,
        'friction_score':  42.0,
        'real_yield':      2.18,
        'spread_value':    3.5,
        'stop_hunt_active': True,
        'dark_pool_signal': False,
        'instrument':      'XAUUSD',
    }

    for mode in ['slow', 'fast']:
        print(f'--- {mode.upper()} AGENT ---')
        agent  = CFRPolicyAgent(mode=mode)
        result = agent.evaluate(test_state)
        print(f'  Action Score:     {result["action_score"]:.3f}')
        print(f'  Allowed:          {result["position_allowed"]}')
        print(f'  Direction:        {result["direction"]}')
        print(f'  Kelly Adjustment: {result["kelly_adjustment"]:.3f}')
        print(f'  Boost:            +{result["total_boost"]:.3f}')
        print(f'  Penalty:          -{result["total_penalty"]:.3f}')
        print()