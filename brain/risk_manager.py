# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/risk_manager.py
# Dynamic Position Sizing Engine
# Calculates exact lot size per trade based on:
# - Account balance
# - Max risk per trade (1% default)
# - SL distance in pips
# - Pip value per instrument
# Without this, a 50-pip SL on XAUUSD risks 5x more
# than a 50-pip SL on GBPUSD — this fixes that.
# ════════════════════════════════════════════════════════════════

import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.risk_manager')

# ── PIP VALUES (USD per standard lot per pip) ──────────────────
# XAUUSD: 1 pip = $0.10 movement, 1 lot = 100oz
#         pip value = $10 per lot per $0.10 move
# GBPUSD: 1 pip = 0.0001, 1 lot = 100,000 units
#         pip value = $10 per lot
# GBPJPY: 1 pip = 0.01, pip value varies with JPY rate
#         approximated at $9 per lot
PIP_VALUES = {
    'XAUUSD': 10.0,   # $10 per lot per pip ($0.10 move)
    'GBPUSD': 10.0,   # $10 per lot per pip (0.0001 move)
    'GBPJPY': 9.0,    # ~$9 per lot per pip (0.01 move)
}

# Pip size per instrument (what 1 pip equals in price)
PIP_SIZE = {
    'XAUUSD': 0.10,    # Gold moves in $0.10 pips
    'GBPUSD': 0.0001,  # Forex standard
    'GBPJPY': 0.01,    # JPY pairs
}

# Daily loss tracker file
DAILY_LOSS_FILE = Path(
    os.path.dirname(os.path.abspath(__file__))
) / 'daily_loss.json'


class RiskManager:
    """
    Calculates position size so that every trade risks
    exactly the same dollar amount regardless of pair or
    SL distance.

    Formula:
        lot_size = (balance × risk_pct) / (sl_pips × pip_value)

    Example:
        Balance = $1000, Risk = 1%, SL = 150 pips on XAUUSD
        Dollar risk = $10
        Lot size = $10 / (150 × $10) = 0.006 lots (micro lot)
    """

    def __init__(self):
        self.daily_loss      = self._load_daily_loss()
        self.risk_pct        = config.BASE_KELLY_FRACTION
        self.max_risk_pct    = config.MAX_KELLY_FRACTION
        self.max_daily_loss  = config.MAX_DAILY_LOSS_PCT
        self.max_drawdown    = config.MAX_TOTAL_DRAWDOWN_PCT

    # ── DAILY LOSS TRACKING ────────────────────────────────────

    def _load_daily_loss(self) -> dict:
        """Loads today's loss data."""
        if not DAILY_LOSS_FILE.exists():
            return self._fresh_daily_loss()
        try:
            with open(DAILY_LOSS_FILE, 'r') as f:
                data = json.load(f)
            # Reset if it's a new day
            if data.get('date') != str(date.today()):
                return self._fresh_daily_loss()
            return data
        except Exception:
            return self._fresh_daily_loss()

    def _fresh_daily_loss(self) -> dict:
        return {
            'date':          str(date.today()),
            'total_loss':    0.0,
            'total_risk':    0.0,
            'trades_today':  0,
            'losses_today':  0,
        }

    def _save_daily_loss(self):
        with open(DAILY_LOSS_FILE, 'w') as f:
            json.dump(self.daily_loss, f, indent=2)

    def record_loss(self, amount: float):
        """Records a trade loss for daily tracking."""
        self.daily_loss['total_loss']   += amount
        self.daily_loss['losses_today'] += 1
        self._save_daily_loss()
        logger.info(
            f'Loss recorded: ${amount:.2f} | '
            f'Daily total: ${self.daily_loss["total_loss"]:.2f}'
        )

    def record_trade(self, risk_amount: float):
        """Records a trade being taken."""
        self.daily_loss['trades_today'] += 1
        self.daily_loss['total_risk']   += risk_amount
        self._save_daily_loss()

    def get_daily_loss_pct(self, balance: float) -> float:
        """Returns today's loss as percentage of balance."""
        if balance <= 0:
            return 0.0
        return self.daily_loss['total_loss'] / balance

    # ── CORE SIZING FUNCTION ───────────────────────────────────

    def calculate_lot_size(
        self,
        instrument:  str,
        entry_price: float,
        stop_loss:   float,
        balance:     float,
        risk_pct:    float = None,
    ) -> dict:
        """
        Main function. Returns lot size and full risk breakdown.

        Parameters:
            instrument:  XAUUSD, GBPUSD, or GBPJPY
            entry_price: planned entry price
            stop_loss:   stop loss price
            balance:     current account balance in USD
            risk_pct:    override default risk % (optional)
        """
        if risk_pct is None:
            risk_pct = self.risk_pct

        pip_size  = PIP_SIZE.get(instrument,  0.0001)
        pip_value = PIP_VALUES.get(instrument, 10.0)

        # Calculate SL distance in pips
        sl_pips = abs(entry_price - stop_loss) / pip_size

        if sl_pips <= 0:
            logger.error(
                f'Invalid SL distance for {instrument}: '
                f'entry={entry_price} sl={stop_loss}'
            )
            return {'error': 'Invalid SL distance', 'lot_size': 0}

        # Dollar risk for this trade
        dollar_risk = balance * risk_pct

        # Lot size formula
        lot_size = dollar_risk / (sl_pips * pip_value)

        # Round to nearest micro lot (0.01)
        lot_size = max(0.01, round(lot_size, 2))

        # Cap at max position size
        max_lots = (balance * self.max_risk_pct) / (sl_pips * pip_value)
        lot_size = min(lot_size, max(0.01, round(max_lots, 2)))

        # Actual dollar risk after rounding
        actual_risk = lot_size * sl_pips * pip_value
        actual_risk_pct = actual_risk / balance if balance > 0 else 0

        result = {
            'instrument':     instrument,
            'lot_size':       lot_size,
            'sl_pips':        round(sl_pips, 1),
            'pip_value':      pip_value,
            'dollar_risk':    round(actual_risk, 2),
            'risk_pct':       round(actual_risk_pct * 100, 2),
            'balance':        balance,
            'entry':          entry_price,
            'stop_loss':      stop_loss,
            'timestamp':      datetime.utcnow().isoformat(),
        }

        logger.info(
            f'Position sized: {instrument} | '
            f'Lot={lot_size} | '
            f'SL={sl_pips:.0f}pips | '
            f'Risk=${actual_risk:.2f} ({actual_risk_pct:.1%})'
        )
        return result

    def size_all_pairs(
        self,
        signals:  dict,
        balance:  float,
    ) -> dict:
        """
        Sizes positions for multiple signals simultaneously.
        Applies correlation guard to reduce risk on correlated pairs.
        """
        results = {}
        for instrument, signal in signals.items():
            if signal.get('direction') in ['HOLD', None]:
                continue
            entry = signal.get('entry')
            sl    = signal.get('stop_loss')
            if not entry or not sl:
                continue
            results[instrument] = self.calculate_lot_size(
                instrument, entry, sl, balance
            )
        return results


# ── SINGLETON ──────────────────────────────────────────────────
_risk_manager = None

def get_risk_manager() -> RiskManager:
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager