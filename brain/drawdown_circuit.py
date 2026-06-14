# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/drawdown_circuit.py
# Daily Drawdown Circuit Breaker
# Pauses all signal generation if daily loss exceeds limit
# Resets automatically at midnight UTC
# Also tracks total drawdown from peak equity
# ════════════════════════════════════════════════════════════════

import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.drawdown_circuit')

CIRCUIT_FILE = Path(
    os.path.dirname(os.path.abspath(__file__))
) / 'circuit_state.json'


class DrawdownCircuit:
    """
    Monitors daily and total drawdown.
    Trips the circuit breaker when limits are exceeded.

    Daily limit:   2% (config.MAX_DAILY_LOSS_PCT)
    Total limit:   5% (config.MAX_TOTAL_DRAWDOWN_PCT)

    When tripped:
    - All signal generation returns HOLD
    - Telegram alert sent
    - Resets daily at 00:00 UTC
    - Total reset only when equity recovers above trip point
    """

    def __init__(self):
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if not CIRCUIT_FILE.exists():
            return self._fresh_state()
        try:
            with open(CIRCUIT_FILE, 'r') as f:
                data = json.load(f)
            # Reset daily counter if new day
            if data.get('date') != str(date.today()):
                data['daily_loss']     = 0.0
                data['daily_tripped']  = False
                data['date']           = str(date.today())
                data['trades_today']   = 0
            return data
        except Exception:
            return self._fresh_state()

    def _fresh_state(self) -> dict:
        return {
            'date':           str(date.today()),
            'daily_loss':     0.0,
            'daily_tripped':  False,
            'total_tripped':  False,
            'peak_balance':   0.0,
            'current_balance':0.0,
            'total_drawdown': 0.0,
            'trades_today':   0,
            'trip_reason':    None,
            'tripped_at':     None,
        }

    def _save_state(self):
        with open(CIRCUIT_FILE, 'w') as f:
            json.dump(self.state, f, indent=2)

    def update_balance(self, current_balance: float):
        """Call after each trade result to update tracking."""
        if self.state['peak_balance'] == 0:
            self.state['peak_balance'] = current_balance

        self.state['current_balance'] = current_balance

        # Update peak
        if current_balance > self.state['peak_balance']:
            self.state['peak_balance'] = current_balance

        # Calculate total drawdown from peak
        peak = self.state['peak_balance']
        if peak > 0:
            dd = (peak - current_balance) / peak
            self.state['total_drawdown'] = round(dd, 4)

        self._save_state()

    def record_loss(self, loss_amount: float, balance: float):
        """Records a trade loss and checks circuit breakers."""
        self.state['daily_loss']   += abs(loss_amount)
        self.state['trades_today'] += 1
        self.update_balance(balance - abs(loss_amount))

        # Check daily limit
        daily_pct = (
            self.state['daily_loss'] / balance
            if balance > 0 else 0
        )
        if daily_pct >= config.MAX_DAILY_LOSS_PCT:
            self._trip_daily(daily_pct, balance)

        # Check total drawdown limit
        if self.state['total_drawdown'] >= config.MAX_TOTAL_DRAWDOWN_PCT:
            self._trip_total()

        self._save_state()

    def record_win(self, win_amount: float, balance: float):
        """Records a trade win."""
        self.state['trades_today'] += 1
        self.update_balance(balance + abs(win_amount))
        self._save_state()

    def _trip_daily(self, pct: float, balance: float):
        """Trips the daily circuit breaker."""
        if not self.state['daily_tripped']:
            self.state['daily_tripped'] = True
            self.state['trip_reason']   = (
                f'Daily loss {pct:.1%} exceeded '
                f'{config.MAX_DAILY_LOSS_PCT:.0%} limit'
            )
            self.state['tripped_at'] = datetime.utcnow().isoformat()
            logger.critical(
                f'CIRCUIT BREAKER TRIPPED: {self.state["trip_reason"]}'
            )
            self._send_alert(
                f'🔴 <b>CIRCUIT BREAKER — DAILY LIMIT</b>\n\n'
                f'Daily loss: <b>{pct:.1%}</b> of balance\n'
                f'Limit: {config.MAX_DAILY_LOSS_PCT:.0%}\n'
                f'All signals paused until midnight UTC.\n'
                f'Balance: ${balance:,.2f}'
            )

    def _trip_total(self):
        """Trips the total drawdown circuit breaker."""
        if not self.state['total_tripped']:
            self.state['total_tripped'] = True
            self.state['trip_reason']   = (
                f'Total drawdown {self.state["total_drawdown"]:.1%} '
                f'exceeded {config.MAX_TOTAL_DRAWDOWN_PCT:.0%} limit'
            )
            self.state['tripped_at'] = datetime.utcnow().isoformat()
            logger.critical(
                f'TOTAL DRAWDOWN CIRCUIT TRIPPED: '
                f'{self.state["total_drawdown"]:.1%}'
            )
            self._send_alert(
                f'🚨 <b>CIRCUIT BREAKER — TOTAL DRAWDOWN</b>\n\n'
                f'Total drawdown: '
                f'<b>{self.state["total_drawdown"]:.1%}</b>\n'
                f'Limit: {config.MAX_TOTAL_DRAWDOWN_PCT:.0%}\n'
                f'All signals paused until equity recovers.\n'
                f'Manual review required.'
            )

    def _send_alert(self, message: str):
        """Sends Telegram alert."""
        try:
            import asyncio
            from tg_bot.bot import send_alert
            asyncio.run(send_alert(message))
        except Exception as e:
            logger.error(f'Circuit alert error: {e}')

    def is_tripped(self) -> bool:
        """Returns True if any circuit breaker is active."""
        return (
            self.state['daily_tripped'] or
            self.state['total_tripped']
        )

    def get_status(self) -> dict:
        """Returns current circuit breaker status."""
        return {
            'is_tripped':      self.is_tripped(),
            'daily_tripped':   self.state['daily_tripped'],
            'total_tripped':   self.state['total_tripped'],
            'daily_loss':      self.state['daily_loss'],
            'total_drawdown':  self.state['total_drawdown'],
            'trades_today':    self.state['trades_today'],
            'trip_reason':     self.state.get('trip_reason'),
            'peak_balance':    self.state['peak_balance'],
            'current_balance': self.state['current_balance'],
        }


# ── SINGLETON ──────────────────────────────────────────────────
_circuit = None

def get_circuit() -> DrawdownCircuit:
    global _circuit
    if _circuit is None:
        _circuit = DrawdownCircuit()
    return _circuit


def is_trading_allowed() -> tuple:
    """
    Quick check. Returns (allowed: bool, reason: str).
    Call this at the start of every signal calculation.
    """
    circuit = get_circuit()
    if circuit.is_tripped():
        return False, circuit.state.get('trip_reason', 'Circuit tripped')
    return True, 'OK'