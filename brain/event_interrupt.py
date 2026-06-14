# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/event_interrupt.py
# News Event Interrupt System
# Two functions:
# 1. Detects when friction score spikes (news event hit)
#    → Forces immediate indicator recalculation
# 2. Reads economic calendar for upcoming week
#    → Flags high-impact events (FOMC, NFP, BoE, BoJ)
#    → Raises signal confidence threshold in 4hr window
#    → Adds volatility warning to affected pairs
# ════════════════════════════════════════════════════════════════

import json
import logging
import os
import requests
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.event_interrupt')

EVENTS_FILE = Path(
    os.path.dirname(os.path.abspath(__file__))
) / 'upcoming_events.json'

INTERRUPT_FILE = Path(
    os.path.dirname(os.path.abspath(__file__))
) / 'interrupt_state.json'

# Events that affect each instrument
INSTRUMENT_EVENTS = {
    'XAUUSD': [
        'FOMC', 'Federal Reserve', 'Fed Rate', 'CPI', 'NFP',
        'Non-Farm', 'inflation', 'GDP', 'dollar', 'DXY',
        'treasury', 'yield', 'recession', 'safe haven',
    ],
    'GBPUSD': [
        'Bank of England', 'BoE', 'MPC', 'UK CPI', 'UK GDP',
        'UK inflation', 'FOMC', 'Fed Rate', 'NFP', 'PMI UK',
    ],
    'GBPJPY': [
        'Bank of Japan', 'BoJ', 'Bank of England', 'BoE',
        'Japan CPI', 'MPC', 'BOJ rate', 'yield curve control',
    ],
}

# Friction spike detection
_last_friction_score = 0.0
FRICTION_SPIKE_THRESHOLD = 15.0  # points jump triggers interrupt


class EventInterrupt:
    """
    Monitors news events and economic calendar.
    Interrupts the normal 5-minute indicator cycle when
    significant events are detected.
    """

    def __init__(self):
        self.interrupt_active = False
        self.affected_pairs   = []
        self.event_description= ''
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if not INTERRUPT_FILE.exists():
            return {
                'last_friction':    0.0,
                'interrupt_count':  0,
                'last_interrupt':   None,
                'upcoming_events':  [],
            }
        try:
            with open(INTERRUPT_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {'last_friction': 0.0, 'interrupt_count': 0}

    def _save_state(self):
        with open(INTERRUPT_FILE, 'w') as f:
            json.dump(self.state, f, indent=2, default=str)

    # ── FRICTION SPIKE DETECTOR ────────────────────────────────

    def check_friction_spike(
        self,
        current_friction: float,
        instruments: list = None,
    ) -> dict:
        """
        Compares current friction score to last known value.
        If spike >= threshold: triggers interrupt for affected pairs.
        Returns interrupt decision dict.
        """
        global _last_friction_score
        if instruments is None:
            instruments = config.INSTRUMENTS

        last = self.state.get('last_friction', 0.0)
        spike = current_friction - last

        result = {
            'interrupt':    False,
            'spike':        round(spike, 1),
            'current':      current_friction,
            'previous':     last,
            'pairs':        [],
            'reason':       None,
        }

        if spike >= FRICTION_SPIKE_THRESHOLD:
            result['interrupt'] = True
            result['pairs']     = instruments  # recalculate all
            result['reason']    = (
                f'Friction spike +{spike:.0f} points '
                f'({last:.0f} → {current_friction:.0f})'
            )
            self.state['interrupt_count'] += 1
            self.state['last_interrupt']   = (
                datetime.utcnow().isoformat()
            )
            logger.warning(
                f'FRICTION SPIKE INTERRUPT: {result["reason"]}'
            )
            self._send_interrupt_alert(result)

        self.state['last_friction'] = current_friction
        self._save_state()
        return result

    # ── ECONOMIC CALENDAR ──────────────────────────────────────

    def fetch_weekly_calendar(self) -> list:
        """
        Fetches high-impact economic events for next 7 days
        from Finnhub economic calendar endpoint.
        Saves to upcoming_events.json.
        """
        try:
            now   = datetime.utcnow()
            end   = now + timedelta(days=7)
            r = requests.get(
                'https://finnhub.io/api/v1/calendar/economic',
                params={
                    'from':  now.strftime('%Y-%m-%d'),
                    'to':    end.strftime('%Y-%m-%d'),
                    'token': config.FINNHUB_API_KEY,
                },
                timeout=15
            )
            data   = r.json()
            events = data.get('economicCalendar', [])

            # Filter high-impact only
            high_impact = []
            for event in events:
                impact = event.get('impact', '').lower()
                if impact in ['high', '3']:
                    high_impact.append({
                        'datetime':  event.get('time', ''),
                        'event':     event.get('event', ''),
                        'country':   event.get('country', ''),
                        'impact':    'HIGH',
                        'actual':    event.get('actual'),
                        'forecast':  event.get('estimate'),
                        'previous':  event.get('prev'),
                    })

            # Save to file
            with open(EVENTS_FILE, 'w') as f:
                json.dump({
                    'fetched_at': now.isoformat(),
                    'events':     high_impact,
                }, f, indent=2, default=str)

            logger.info(
                f'Calendar: {len(high_impact)} high-impact '
                f'events next 7 days'
            )
            return high_impact

        except Exception as e:
            logger.error(f'Calendar fetch error: {e}')
            return []

    def get_upcoming_events(self) -> list:
        """Returns saved upcoming events."""
        if not EVENTS_FILE.exists():
            return []
        try:
            with open(EVENTS_FILE, 'r') as f:
                data = json.load(f)
            return data.get('events', [])
        except Exception:
            return []

    def get_event_context(self, instrument: str) -> dict:
        """
        Returns event context for a specific instrument right now.
        Checks if any high-impact event is within 4 hours
        (pre-event window) or within 1 hour past (post-event).

        Returns dict with:
        - pre_event: bool (event coming within 4hrs)
        - post_event: bool (event was within last 1hr)
        - confidence_boost: extra % to add to min confidence
        - volatility_warning: bool
        - event_name: str
        """
        events  = self.get_upcoming_events()
        now     = datetime.utcnow()
        keywords= INSTRUMENT_EVENTS.get(instrument, [])

        result = {
            'pre_event':          False,
            'post_event':         False,
            'confidence_boost':   0,
            'volatility_warning': False,
            'event_name':         None,
        }

        for event in events:
            event_name = event.get('event', '').lower()
            country    = event.get('country', '').upper()

            # Check if this event affects our instrument
            is_relevant = any(
                kw.lower() in event_name
                for kw in keywords
            )
            if not is_relevant:
                continue

            try:
                event_dt = datetime.fromisoformat(
                    event['datetime'].replace('Z', '+00:00')
                ).replace(tzinfo=None)
            except Exception:
                continue

            delta = (event_dt - now).total_seconds() / 3600

            if 0 < delta <= 4:
                # Pre-event window: raise threshold
                result['pre_event']          = True
                result['volatility_warning'] = True
                result['event_name']         = event.get('event')
                result['confidence_boost']   = max(
                    result['confidence_boost'],
                    int((4 - delta) * 5)  # up to +20% as event approaches
                )
                logger.info(
                    f'Pre-event warning: {instrument} | '
                    f'{event.get("event")} in {delta:.1f}hrs'
                )

            elif -1 <= delta <= 0:
                # Post-event: high volatility, extra caution
                result['post_event']         = True
                result['volatility_warning'] = True
                result['event_name']         = event.get('event')
                result['confidence_boost']   = 15
                logger.info(
                    f'Post-event caution: {instrument} | '
                    f'{event.get("event")} just occurred'
                )

        return result

    def _send_interrupt_alert(self, interrupt: dict):
        """Sends Telegram alert on friction spike."""
        try:
            import asyncio
            from tg_bot.bot import send_alert
            asyncio.run(send_alert(
                f'⚡ <b>NEWS INTERRUPT TRIGGERED</b>\n\n'
                f'Friction spike: +{interrupt["spike"]:.0f} points\n'
                f'Current: {interrupt["current"]:.0f}/100\n\n'
                f'Forcing indicator recalculation\n'
                f'for all pairs...'
            ))
        except Exception:
            pass

    async def weekly_calendar_refresh_loop(self):
        """
        Runs every Sunday to fetch next week's calendar.
        Attach to bot event loop.
        """
        import asyncio
        while True:
            now = datetime.utcnow()
            # Run on Sunday between 06:00-07:00 UTC
            if now.weekday() == 6 and 6 <= now.hour < 7:
                logger.info('Sunday calendar refresh starting...')
                events = self.fetch_weekly_calendar()
                if events:
                    from tg_bot.bot import send_alert
                    lines = [
                        '📅 <b>NEXT WEEK HIGH-IMPACT EVENTS</b>\n'
                    ]
                    for e in events[:10]:
                        dt  = e['datetime'][:16] if e.get('datetime') else '?'
                        lines.append(
                            f'• {dt} — {e["event"]} '
                            f'({e.get("country","?")})'
                        )
                    await send_alert('\n'.join(lines))
            # Check every hour
            await asyncio.sleep(3600)


# ── SINGLETON ──────────────────────────────────────────────────
_interrupt = None

def get_interrupt() -> EventInterrupt:
    global _interrupt
    if _interrupt is None:
        _interrupt = EventInterrupt()
    return _interrupt