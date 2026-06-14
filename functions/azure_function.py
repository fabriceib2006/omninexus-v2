# ════════════════════════════════════════════════════════════════
# OMNINEXUS — functions/azure_function.py
# Azure Function Handler
# Entry point for all serverless Azure Function triggers
# Each function maps to one system component
# Triggered by Azure Event Grid events
# ════════════════════════════════════════════════════════════════

import logging
import json
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.functions.azure_function')


class AzureFunctionHandler:
    """
    Routes Azure Function triggers to the correct
    system component. Each trigger type maps to
    a specific OmniNexus module.

    Trigger types:
      signal_gold      → Gold signal aggregation
      signal_gbp       → GBP signal aggregation
      regime_check     → Autoencoder regime detection
      dark_pool_scan   → FINRA dark pool scraper
      friction_update  → Geopolitical friction index
      yield_update     → FRED real yield
      behavioral_scan  → Google Trends + GitHub
      satellite_scan   → NASA + ESA satellite
      trade_execute    → Full execution pipeline
      loss_analysis    → Post-loss diagnostic
    """

    def __init__(self):
        self.status       = 'initialized'
        self.run_count    = 0
        self.last_run     = None
        self.error_count  = 0

    def run(
        self,
        trigger_payload: dict
    ) -> dict:
        """
        Main function entry point.
        Routes payload to correct handler.
        """
        trigger_type = trigger_payload.get(
            'trigger_type', 'unknown'
        )
        self.run_count += 1
        self.last_run   = datetime.utcnow().isoformat()

        logger.info(
            f'Azure Function triggered: {trigger_type} | '
            f'Run #{self.run_count}'
        )

        handlers = {
            'signal_gold':     self._handle_gold_signal,
            'signal_gbp':      self._handle_gbp_signal,
            'regime_check':    self._handle_regime_check,
            'dark_pool_scan':  self._handle_dark_pool,
            'friction_update': self._handle_friction,
            'yield_update':    self._handle_yield,
            'behavioral_scan': self._handle_behavioral,
            'satellite_scan':  self._handle_satellite,
            'trade_execute':   self._handle_trade_execute,
            'loss_analysis':   self._handle_loss_analysis,
            'status':          self._handle_status,
        }

        handler = handlers.get(trigger_type)
        if not handler:
            return {
                'status':  'error',
                'message': f'Unknown trigger: {trigger_type}',
                'trigger': trigger_type,
            }

        try:
            result = handler(trigger_payload)
            result['trigger_type'] = trigger_type
            result['run_number']   = self.run_count
            result['timestamp']    = self.last_run
            self.status = 'healthy'
            return result

        except Exception as e:
            self.error_count += 1
            self.status       = 'error'
            logger.error(
                f'Function error [{trigger_type}]: {e}'
            )
            return {
                'status':      'error',
                'trigger_type': trigger_type,
                'error':        str(e),
                'run_number':   self.run_count,
            }

    def _handle_gold_signal(self, payload: dict) -> dict:
        """Runs Gold Signal Aggregation."""
        from signals.gold import GoldSignalAggregator
        agg    = GoldSignalAggregator()
        result = agg.aggregate(fast_mode=payload.get(
            'fast_mode', False
        ))
        return {
            'status':     'success',
            'bias_score': result['bias_score'],
            'bias_label': result['bias_label'],
            'signals':    result['signals_used'],
        }

    def _handle_gbp_signal(self, payload: dict) -> dict:
        """Runs GBP Signal Aggregation."""
        from signals.gbp import GBPSignalAggregator
        agg    = GBPSignalAggregator()
        result = agg.aggregate(fast_mode=payload.get(
            'fast_mode', False
        ))
        return {
            'status':     'success',
            'bias_score': result['bias_score'],
            'bias_label': result['bias_label'],
            'session':    result['session']['session'],
        }

    def _handle_regime_check(self, payload: dict) -> dict:
        """Runs Autoencoder Regime Check."""
        from brain.autoencoder import AutoencoderRegimeDetector
        detector = AutoencoderRegimeDetector()

        obs = detector.build_observation(
            real_yield     = payload.get('real_yield', 1.5),
            friction_score = payload.get('friction_score', 35.0),
            gold_bias      = payload.get('gold_bias', 50.0),
            gbp_bias       = payload.get('gbp_bias', 50.0),
            boe_boj_spread = payload.get('boe_boj_spread', 2.5),
            session_score  = payload.get('session_score', 50.0),
        )
        result = detector.detect_anomaly(obs)
        return {
            'status':    'success',
            'regime':    result['regime'],
            'anomaly':   result['is_anomaly'],
            'error':     result['reconstruction_error'],
        }

    def _handle_dark_pool(self, payload: dict) -> dict:
        """Runs Dark Pool Scanner."""
        from ingestion.darkpool import scan_dark_pools
        result = scan_dark_pools()
        return {
            'status':    'success',
            'anomalies': result['anomalies_found'],
            'gold':      result['gold_signal'],
            'gbp':       result['gbp_signal'],
        }

    def _handle_friction(self, payload: dict) -> dict:
        """Runs Friction Index Update."""
        from ingestion.friction import calculate_friction_index
        result = calculate_friction_index()
        if 'error' in result:
            return {'status': 'error', 'error': result['error']}
        return {
            'status': 'success',
            'score':  result['friction_score'],
            'level':  result['level'],
            'alert':  result['alert_triggered'],
        }

    def _handle_yield(self, payload: dict) -> dict:
        """Runs Real Yield Update."""
        from ingestion.fred_yield import calculate_real_yield
        result = calculate_real_yield()
        if 'error' in result:
            return {'status': 'error', 'error': result['error']}
        return {
            'status':    'success',
            'real_yield': result['real_yield'],
            'gold_bias':  result['gold_bias'],
            'breakout':   result['breakout_signal'],
        }

    def _handle_behavioral(self, payload: dict) -> dict:
        """Runs Behavioral Exhaust Scan."""
        from ingestion.behavioral import (
            calculate_behavioral_exhaust
        )
        result = calculate_behavioral_exhaust()
        return {
            'status':      'success',
            'gold_score':  result.get(
                'gold_behavioral_score', 0
            ),
            'gbp_score':   result.get(
                'gbp_behavioral_score', 0
            ),
        }

    def _handle_satellite(self, payload: dict) -> dict:
        """Runs Satellite Zone Scan."""
        from ingestion.satellite import scan_satellite_zones
        result = scan_satellite_zones()
        return {
            'status':    'success',
            'zones':     result['zones_scanned'],
            'anomalies': result['anomalies_found'],
        }

    def _handle_trade_execute(self, payload: dict) -> dict:
        """Routes trade execution request."""
        instrument  = payload.get('instrument', 'XAUUSD')
        direction   = payload.get('direction', 'LONG')
        bias_score  = payload.get('bias_score', 50.0)

        logger.info(
            f'Trade execution requested: '
            f'{instrument} {direction} | '
            f'Bias={bias_score}'
        )

        return {
            'status':     'queued',
            'instrument': instrument,
            'direction':  direction,
            'bias_score': bias_score,
            'message':    (
                'Trade execution queued. '
                'Full execution via DerivativeExecution.'
            ),
        }

    def _handle_loss_analysis(self, payload: dict) -> dict:
        """Triggers post-loss diagnostic pipeline."""
        trade_id  = payload.get('trade_id', 'unknown')
        pnl       = payload.get('pnl', 0.0)
        instrument = payload.get('instrument', 'XAUUSD')

        logger.warning(
            f'Loss analysis triggered: '
            f'{instrument} | PnL={pnl} | ID={trade_id}'
        )

        return {
            'status':     'analysis_triggered',
            'trade_id':   trade_id,
            'instrument': instrument,
            'pnl':        pnl,
            'message':    'Loss diagnostic queued',
        }

    def _handle_status(self, payload: dict) -> dict:
        """Returns system health status."""
        return {
            'status':      'success',
            'health':      self.status,
            'run_count':   self.run_count,
            'error_count': self.error_count,
            'last_run':    self.last_run,
            'instruments': config.INSTRUMENTS,
            'challenge':   config.CHALLENGE_ACTIVE,
        }


# ── AZURE FUNCTION ENTRY POINTS ────────────────────────────────
# These are called directly by Azure Functions runtime

def main_signal_gold(event: dict) -> str:
    """Azure Function: Gold Signal trigger."""
    handler = AzureFunctionHandler()
    result  = handler.run({
        'trigger_type': 'signal_gold',
        **event
    })
    return json.dumps(result)


def main_signal_gbp(event: dict) -> str:
    """Azure Function: GBP Signal trigger."""
    handler = AzureFunctionHandler()
    result  = handler.run({
        'trigger_type': 'signal_gbp',
        **event
    })
    return json.dumps(result)


def main_regime_check(event: dict) -> str:
    """Azure Function: Regime Check trigger."""
    handler = AzureFunctionHandler()
    result  = handler.run({
        'trigger_type': 'regime_check',
        **event
    })
    return json.dumps(result)


def main_yield_update(event: dict) -> str:
    """Azure Function: Yield Update trigger."""
    handler = AzureFunctionHandler()
    result  = handler.run({
        'trigger_type': 'yield_update',
        **event
    })
    return json.dumps(result)


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Azure Function Handler Test')
    print('='*55 + '\n')

    handler = AzureFunctionHandler()

    # Test status
    print('--- Status Check ---')
    result = handler.run({'trigger_type': 'status'})
    print(f'  Health:      {result["health"]}')
    print(f'  Instruments: {result["instruments"]}')
    print(f'  Run #:       {result["run_number"]}')

    # Test yield update
    print('\n--- Yield Update ---')
    result2 = handler.run({'trigger_type': 'yield_update'})
    print(f'  Status:     {result2["status"]}')
    if result2['status'] == 'success':
        print(f'  Real Yield: {result2.get("real_yield")}')
        print(f'  Gold Bias:  {result2.get("gold_bias")}')

    # Test regime check
    print('\n--- Regime Check ---')
    result3 = handler.run({
        'trigger_type':  'regime_check',
        'real_yield':    2.18,
        'friction_score': 42.0,
        'gold_bias':     25.0,
    })
    print(f'  Status:  {result3["status"]}')
    if result3['status'] == 'success':
        print(f'  Regime:  {result3.get("regime")}')
        print(f'  Anomaly: {result3.get("anomaly")}')
        print(f'  Error:   {result3.get("error")}')

    # Test dark pool
    print('\n--- Dark Pool Scan ---')
    result4 = handler.run({'trigger_type': 'dark_pool_scan'})
    print(f'  Status:    {result4["status"]}')
    if result4['status'] == 'success':
        print(f'  Anomalies: {result4.get("anomalies")}')
        print(f'  Gold:      {result4.get("gold")}')