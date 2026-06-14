# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/meta_agent.py
# Hi-DARTS Meta-Agent
# Monitors regime signals and dynamically switches
# between SLOW and FAST CFR policy agents
# Always-on Azure Function — cheapest compute tier
# ════════════════════════════════════════════════════════════════

import logging
import json
import os
from datetime import datetime
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.meta_agent')

META_STATE_FILE = 'logs/meta_agent_state.json'


class MetaAgent:
    """
    Hi-DARTS Meta-Agent.
    Monitors 4 switching triggers and selects
    the appropriate CFR brain.

    Switching triggers:
      1. Autoencoder reconstruction error > threshold
      2. Friction Score spike above FRICTION_THRESHOLD
      3. Real Yield delta acceleration > 0.05%
      4. Session transition window active

    Any single trigger activates FAST agent.
    All triggers below threshold = SLOW agent.
    """

    def __init__(self):
        self.current_brain   = 'slow'
        self.previous_brain  = 'slow'
        self.switch_count    = 0
        self.switch_history  = []
        self.last_evaluation = None

        # Load saved state
        self._load_state()

        # Initialize both agents
        from brain.cfr_agent import CFRPolicyAgent
        self.slow_agent = CFRPolicyAgent(mode='slow')
        self.fast_agent = CFRPolicyAgent(mode='fast')

        logger.info(
            f'Meta-Agent initialized. '
            f'Current brain: {self.current_brain}'
        )

    def _load_state(self):
        """Loads saved meta-agent state."""
        if os.path.exists(META_STATE_FILE):
            try:
                with open(META_STATE_FILE, 'r') as f:
                    state = json.load(f)
                self.current_brain  = state.get(
                    'current_brain', 'slow'
                )
                self.switch_count   = state.get(
                    'switch_count', 0
                )
                self.switch_history = state.get(
                    'switch_history', []
                )[-20:]
                logger.info(
                    f'Meta-Agent state loaded: '
                    f'brain={self.current_brain}'
                )
            except Exception as e:
                logger.error(f'State load error: {e}')

    def _save_state(self):
        """Saves current meta-agent state."""
        try:
            os.makedirs('logs', exist_ok=True)
            state = {
                'current_brain':  self.current_brain,
                'switch_count':   self.switch_count,
                'switch_history': self.switch_history[-20:],
                'last_saved':     datetime.utcnow().isoformat(),
            }
            with open(META_STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f'State save error: {e}')

    def select_brain(
        self,
        anomaly_score:     float,
        friction_score:    float,
        real_yield_delta:  float,
        session_overlap:   bool = False,
        dark_pool_anomaly: bool = False,
    ) -> str:
        """
        Core switching logic.
        Evaluates all triggers and selects brain.

        Returns: 'slow' or 'fast'
        """
        triggers_fired    = []
        trigger_details   = {}

        # ── Trigger 1: Autoencoder anomaly ────────────────────
        ae_normalized = anomaly_score / max(
            config.AUTOENCODER_THRESHOLD, 0.001
        )
        if anomaly_score >= config.AUTOENCODER_THRESHOLD:
            triggers_fired.append('AUTOENCODER')
            trigger_details['autoencoder'] = (
                f'error={anomaly_score:.4f} >= '
                f'threshold={config.AUTOENCODER_THRESHOLD}'
            )
        elif ae_normalized >= 0.8:
            # Grey zone — pre-emptive fast activation
            triggers_fired.append('AUTOENCODER_GREY')
            trigger_details['autoencoder'] = (
                f'grey_zone={anomaly_score:.4f}'
            )

        # ── Trigger 2: Friction spike ──────────────────────────
        if friction_score >= config.FRICTION_THRESHOLD:
            triggers_fired.append('FRICTION')
            trigger_details['friction'] = (
                f'score={friction_score:.1f} >= '
                f'threshold={config.FRICTION_THRESHOLD}'
            )

        # ── Trigger 3: Real yield acceleration ────────────────
        if abs(real_yield_delta) >= 0.05:
            triggers_fired.append('YIELD_DELTA')
            trigger_details['yield_delta'] = (
                f'delta={real_yield_delta:+.4f}'
            )

        # ── Trigger 4: Session overlap ─────────────────────────
        if session_overlap:
            triggers_fired.append('SESSION_OVERLAP')
            trigger_details['session'] = 'overlap_active'

        # ── Trigger 5: Dark pool anomaly ───────────────────────
        if dark_pool_anomaly:
            triggers_fired.append('DARK_POOL')
            trigger_details['dark_pool'] = 'anomaly_detected'

        # ── Brain selection ────────────────────────────────────
        self.previous_brain = self.current_brain

        if triggers_fired:
            new_brain = 'fast'
        else:
            new_brain = 'slow'

        # ── Switch detection ───────────────────────────────────
        switched = new_brain != self.current_brain
        if switched:
            self.current_brain = new_brain
            self.switch_count += 1

            switch_record = {
                'timestamp':      datetime.utcnow().isoformat(),
                'from_brain':     self.previous_brain,
                'to_brain':       self.current_brain,
                'triggers':       triggers_fired,
                'switch_number':  self.switch_count,
            }
            self.switch_history.append(switch_record)

            logger.warning(
                f'BRAIN SWITCH: '
                f'{self.previous_brain.upper()} → '
                f'{self.current_brain.upper()} | '
                f'Triggers: {triggers_fired}'
            )

            self._save_state()
        else:
            self.current_brain = new_brain

        self.last_evaluation = {
            'timestamp':      datetime.utcnow().isoformat(),
            'brain':          self.current_brain,
            'triggers_fired': triggers_fired,
            'trigger_details': trigger_details,
            'switched':       switched,
            'anomaly_score':  anomaly_score,
            'friction_score': friction_score,
            'yield_delta':    real_yield_delta,
        }

        logger.info(
            f'Meta-Agent: brain={self.current_brain} | '
            f'triggers={triggers_fired} | '
            f'switched={switched}'
        )

        return self.current_brain

    def get_active_agent(self):
        """
        Returns the currently active CFR agent.
        """
        if self.current_brain == 'fast':
            return self.fast_agent
        return self.slow_agent

    def evaluate_and_decide(
        self,
        market_state: dict,
        regime_data:  dict,
    ) -> dict:
        """
        Full evaluation pipeline.
        1. Selects brain based on regime signals
        2. Runs active agent against market state
        3. Returns complete decision

        market_state: signal data from aggregators
        regime_data:  output from autoencoder
        """
        # Extract switching signals
        anomaly_score    = regime_data.get(
            'reconstruction_error', 0.0
        )
        friction_score   = market_state.get(
            'friction_score', 35.0
        )
        real_yield_delta = market_state.get(
            'real_yield_delta', 0.0
        )
        session_overlap  = market_state.get(
            'session_overlap', False
        )
        dark_pool_anomaly = market_state.get(
            'dark_pool_anomaly', False
        )

        # Select brain
        brain = self.select_brain(
            anomaly_score     = anomaly_score,
            friction_score    = friction_score,
            real_yield_delta  = real_yield_delta,
            session_overlap   = session_overlap,
            dark_pool_anomaly = dark_pool_anomaly,
        )

        # Run active agent
        agent  = self.get_active_agent()
        result = agent.evaluate(market_state)

        # Combine results
        decision = {
            'brain_selected':    brain,
            'switched':          self.last_evaluation.get(
                'switched', False
            ),
            'triggers_fired':    self.last_evaluation.get(
                'triggers_fired', []
            ),
            'cfr_result':        result,
            'position_allowed':  result['position_allowed'],
            'direction':         result['direction'],
            'action_score':      result['action_score'],
            'kelly_adjustment':  result['kelly_adjustment'],
            'timestamp':         datetime.utcnow().isoformat(),
        }

        return decision

    def get_status(self) -> dict:
        """Returns current meta-agent status."""
        return {
            'current_brain':     self.current_brain,
            'switch_count':      self.switch_count,
            'last_evaluation':   self.last_evaluation,
            'slow_threshold':    self.slow_agent.cfr_threshold,
            'fast_threshold':    self.fast_agent.cfr_threshold,
        }

    def format_telegram(self, decision: dict) -> str:
        """Formats meta-agent decision for Telegram."""
        brain_emoji = (
            '⚡' if decision['brain_selected'] == 'fast'
            else '🐢'
        )
        switch_line = ''
        if decision.get('switched'):
            switch_line = (
                f'\n🔄 <b>BRAIN SWITCHED</b>\n'
                f'Triggers: '
                f'{", ".join(decision["triggers_fired"])}\n'
            )

        cfr = decision.get('cfr_result', {})

        return (
            f'{brain_emoji} <b>META-AGENT DECISION</b>\n\n'
            f'Active Brain:  <b>'
            f'{decision["brain_selected"].upper()}</b>\n'
            f'Position:      '
            f'{"✅ ALLOWED" if decision["position_allowed"] else "🚫 BLOCKED"}\n'
            f'Direction:     <b>{decision["direction"]}</b>\n'
            f'CFR Score:     '
            f'<b>{decision["action_score"]:.3f}</b>\n'
            f'Kelly Adj:     {decision["kelly_adjustment"]:.3f}\n'
            f'{switch_line}'
        )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Hi-DARTS Meta-Agent Test')
    print('='*55 + '\n')

    agent = MetaAgent()

    # Test 1: Stable market
    print('--- Test 1: Stable Market ---')
    brain = agent.select_brain(
        anomaly_score    = 0.02,
        friction_score   = 35.0,
        real_yield_delta = 0.01,
        session_overlap  = False,
    )
    print(f'  Brain selected: {brain}')
    print(f'  Triggers: {agent.last_evaluation["triggers_fired"]}')

    # Test 2: Anomaly detected
    print('\n--- Test 2: Regime Anomaly ---')
    brain2 = agent.select_brain(
        anomaly_score    = 0.85,
        friction_score   = 78.0,
        real_yield_delta = 0.08,
        session_overlap  = True,
    )
    print(f'  Brain selected: {brain2}')
    print(f'  Switched: {agent.last_evaluation["switched"]}')
    print(f'  Triggers: {agent.last_evaluation["triggers_fired"]}')
    print(f'  Switch count: {agent.switch_count}')

    # Test 3: Full evaluation
    print('\n--- Test 3: Full Evaluation ---')
    market_state = {
        'bias_score':      75.0,
        'confluence_pct':  65.0,
        'regime_score':    30.0,
        'friction_score':  78.0,
        'real_yield':      2.18,
        'real_yield_delta': 0.08,
        'spread_value':    3.5,
        'stop_hunt_active': True,
        'session_overlap': True,
        'instrument':      'GBPJPY',
    }
    regime_data = {
        'reconstruction_error': 0.85,
        'is_anomaly':           True,
        'regime':               'ANOMALY_DETECTED',
    }
    decision = agent.evaluate_and_decide(
        market_state, regime_data
    )
    print(f'  Brain:    {decision["brain_selected"]}')
    print(f'  Allowed:  {decision["position_allowed"]}')
    print(f'  Direction:{decision["direction"]}')
    print(f'  CFR Score:{decision["action_score"]:.3f}')