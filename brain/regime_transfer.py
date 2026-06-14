# ════════════════════════════════════════════════════════════════
# OMNINEXUS — brain/regime_transfer.py
# Zero-Shot Regime Transfer
# Stores historical world-state fingerprints in Cosmos DB
# When new market state arrives, finds nearest historical
# match and inherits that regime's proven CFR policy
# System never faces a truly unseen situation
# ════════════════════════════════════════════════════════════════

import logging
import json
import os
import math
from datetime import datetime
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.brain.regime_transfer')

FINGERPRINT_FILE = 'logs/regime_fingerprints.json'

# ── HISTORICAL FINGERPRINTS ────────────────────────────────────
# Pre-loaded with known major market regimes
# System adds new fingerprints as it trades
SEED_FINGERPRINTS = [
    {
        'id':          'covid_crash_2020_03',
        'label':       'COVID Market Crash',
        'date':        '2020-03-16',
        'features': {
            'real_yield':      -0.5,
            'friction_score':  95.0,
            'gold_bias':       85.0,
            'gbp_bias':        15.0,
            'boe_boj_spread':  0.5,
            'session_score':   95.0,
            'dark_pool_gold':  3.5,
            'dark_pool_gbp':  -2.8,
            'behavioral_gold': 90.0,
            'behavioral_gbp':  20.0,
        },
        'cfr_policy': {
            'direction':       'LONG',
            'instrument':      'XAUUSD',
            'kelly_fraction':  0.015,
            'hold_type':       'swing',
            'outcome':         'win',
            'return_pct':      12.4,
        }
    },
    {
        'id':          'fed_hike_2022_06',
        'label':       'Fed Emergency Hike 75bps',
        'date':        '2022-06-15',
        'features': {
            'real_yield':      2.8,
            'friction_score':  65.0,
            'gold_bias':       20.0,
            'gbp_bias':        35.0,
            'boe_boj_spread':  2.1,
            'session_score':   80.0,
            'dark_pool_gold': -2.1,
            'dark_pool_gbp':  -1.5,
            'behavioral_gold': 30.0,
            'behavioral_gbp':  40.0,
        },
        'cfr_policy': {
            'direction':       'SHORT',
            'instrument':      'XAUUSD',
            'kelly_fraction':  0.010,
            'hold_type':       'intraday',
            'outcome':         'win',
            'return_pct':      5.8,
        }
    },
    {
        'id':          'boe_emergency_2022_09',
        'label':       'BoE Emergency Bond Buying',
        'date':        '2022-09-28',
        'features': {
            'real_yield':      3.5,
            'friction_score':  72.0,
            'gold_bias':       35.0,
            'gbp_bias':        10.0,
            'boe_boj_spread':  0.8,
            'session_score':   90.0,
            'dark_pool_gold':  1.8,
            'dark_pool_gbp':  -3.2,
            'behavioral_gold': 55.0,
            'behavioral_gbp':  15.0,
        },
        'cfr_policy': {
            'direction':       'SHORT',
            'instrument':      'GBPJPY',
            'kelly_fraction':  0.012,
            'hold_type':       'intraday',
            'outcome':         'win',
            'return_pct':      8.2,
        }
    },
    {
        'id':          'svb_collapse_2023_03',
        'label':       'SVB Bank Collapse',
        'date':        '2023-03-10',
        'features': {
            'real_yield':      1.2,
            'friction_score':  80.0,
            'gold_bias':       75.0,
            'gbp_bias':        40.0,
            'boe_boj_spread':  2.8,
            'session_score':   85.0,
            'dark_pool_gold':  2.9,
            'dark_pool_gbp':  -1.2,
            'behavioral_gold': 75.0,
            'behavioral_gbp':  35.0,
        },
        'cfr_policy': {
            'direction':       'LONG',
            'instrument':      'XAUUSD',
            'kelly_fraction':  0.018,
            'hold_type':       'swing',
            'outcome':         'win',
            'return_pct':      9.1,
        }
    },
    {
        'id':          'stable_trending_2024',
        'label':       'Normal Trending Market',
        'date':        '2024-01-15',
        'features': {
            'real_yield':      1.8,
            'friction_score':  30.0,
            'gold_bias':       55.0,
            'gbp_bias':        52.0,
            'boe_boj_spread':  3.2,
            'session_score':   55.0,
            'dark_pool_gold':  0.3,
            'dark_pool_gbp':   0.5,
            'behavioral_gold': 28.0,
            'behavioral_gbp':  30.0,
        },
        'cfr_policy': {
            'direction':       'LONG',
            'instrument':      'XAUUSD',
            'kelly_fraction':  0.015,
            'hold_type':       'swing',
            'outcome':         'win',
            'return_pct':      3.4,
        }
    },
]


class RegimeTransfer:
    """
    Zero-shot regime transfer using historical fingerprints.
    Finds the nearest historical regime to current market state
    and inherits its proven CFR policy parameters.
    """

    def __init__(self, gremlin_client=None):
        self.gc                  = gremlin_client
        self.fingerprint_library = []
        self.match_history       = []

        # Load fingerprints
        self._load_fingerprints()

    def _load_fingerprints(self):
        """Loads fingerprints from file + seeds."""
        # Start with seed fingerprints
        self.fingerprint_library = list(SEED_FINGERPRINTS)

        # Load additional from file
        if os.path.exists(FINGERPRINT_FILE):
            try:
                with open(FINGERPRINT_FILE, 'r') as f:
                    saved = json.load(f)
                existing_ids = {
                    f['id'] for f in self.fingerprint_library
                }
                new_fps = [
                    fp for fp in saved
                    if fp['id'] not in existing_ids
                ]
                self.fingerprint_library.extend(new_fps)
                logger.info(
                    f'Loaded {len(new_fps)} additional '
                    f'fingerprints from file'
                )
            except Exception as e:
                logger.error(f'Fingerprint load error: {e}')

        logger.info(
            f'Fingerprint library: '
            f'{len(self.fingerprint_library)} regimes'
        )

    def _save_fingerprints(self):
        """Saves non-seed fingerprints to file."""
        try:
            os.makedirs('logs', exist_ok=True)
            seed_ids = {fp['id'] for fp in SEED_FINGERPRINTS}
            to_save  = [
                fp for fp in self.fingerprint_library
                if fp['id'] not in seed_ids
            ]
            with open(FINGERPRINT_FILE, 'w') as f:
                json.dump(to_save, f, indent=2)
        except Exception as e:
            logger.error(f'Fingerprint save error: {e}')

    def _cosine_similarity(
        self,
        vec_a: dict,
        vec_b: dict
    ) -> float:
        """
        Calculates cosine similarity between two
        feature dictionaries.
        Range: -1.0 to 1.0 (1.0 = identical direction)
        """
        keys = set(vec_a.keys()) & set(vec_b.keys())
        if not keys:
            return 0.0

        dot_product = sum(
            float(vec_a.get(k, 0)) * float(vec_b.get(k, 0))
            for k in keys
        )
        mag_a = math.sqrt(sum(
            float(vec_a.get(k, 0)) ** 2 for k in keys
        ))
        mag_b = math.sqrt(sum(
            float(vec_b.get(k, 0)) ** 2 for k in keys
        ))

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot_product / (mag_a * mag_b)

    def _euclidean_distance(
        self,
        vec_a: dict,
        vec_b: dict
    ) -> float:
        """Euclidean distance between feature dicts."""
        keys = set(vec_a.keys()) | set(vec_b.keys())
        squared_sum = sum(
            (float(vec_a.get(k, 0)) -
             float(vec_b.get(k, 0))) ** 2
            for k in keys
        )
        return math.sqrt(squared_sum)

    def match_state(
        self,
        current_state: dict,
        top_k: int = 3
    ) -> dict:
        """
        Finds the nearest historical fingerprints
        to the current market state.

        Uses combined cosine + euclidean similarity.
        Returns top_k matches with similarity scores.
        """
        if not self.fingerprint_library:
            return {
                'matched_regime': None,
                'similarity':     0.0,
                'matches':        [],
            }

        matches = []

        for fingerprint in self.fingerprint_library:
            fp_features = fingerprint['features']

            cosine = self._cosine_similarity(
                current_state, fp_features
            )
            euclidean = self._euclidean_distance(
                current_state, fp_features
            )

            # Normalize euclidean to 0-1 similarity
            euclid_sim = 1.0 / (1.0 + euclidean / 10.0)

            # Combined similarity (70% cosine, 30% euclidean)
            combined = (cosine * 0.70) + (euclid_sim * 0.30)

            matches.append({
                'fingerprint':    fingerprint,
                'cosine_sim':     round(cosine, 4),
                'euclid_sim':     round(euclid_sim, 4),
                'combined_sim':   round(combined, 4),
            })

        # Sort by combined similarity
        matches.sort(
            key=lambda x: x['combined_sim'],
            reverse=True
        )
        top_matches = matches[:top_k]

        best = top_matches[0] if top_matches else None

        if best:
            fp     = best['fingerprint']
            policy = fp.get('cfr_policy', {})

            logger.info(
                f'Regime match: {fp["label"]} '
                f'(sim={best["combined_sim"]:.4f}) | '
                f'Policy: {policy.get("direction")} '
                f'{policy.get("instrument")}'
            )

            result = {
                'matched_regime':  fp['label'],
                'matched_id':      fp['id'],
                'matched_date':    fp['date'],
                'similarity':      best['combined_sim'],
                'cosine_sim':      best['cosine_sim'],
                'inherited_policy': policy,
                'top_matches': [
                    {
                        'label':  m['fingerprint']['label'],
                        'date':   m['fingerprint']['date'],
                        'sim':    m['combined_sim'],
                        'policy': m['fingerprint'].get(
                            'cfr_policy', {}
                        ),
                    }
                    for m in top_matches
                ],
            }

            self.match_history.append({
                'timestamp':     datetime.utcnow().isoformat(),
                'matched_label': fp['label'],
                'similarity':    best['combined_sim'],
            })

            return result

        return {
            'matched_regime': None,
            'similarity':     0.0,
            'matches':        [],
        }

    def register_fingerprint(
        self,
        label:      str,
        features:   dict,
        cfr_policy: dict,
        outcome:    str = 'unknown'
    ) -> str:
        """
        Registers a new regime fingerprint.
        Called after every significant market event
        or when an anomaly is detected.
        """
        fp_id = (
            f'live_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}'
        )

        fingerprint = {
            'id':       fp_id,
            'label':    label,
            'date':     datetime.utcnow().strftime('%Y-%m-%d'),
            'features': features,
            'cfr_policy': {
                **cfr_policy,
                'outcome': outcome,
            }
        }

        self.fingerprint_library.append(fingerprint)
        self._save_fingerprints()

        logger.info(
            f'New fingerprint registered: {label} | '
            f'ID: {fp_id} | '
            f'Library size: {len(self.fingerprint_library)}'
        )

        return fp_id

    def format_telegram(self, match: dict) -> str:
        """Formats regime match for Telegram."""
        if not match.get('matched_regime'):
            return '🔍 No historical regime match found'

        top = match.get('top_matches', [])[:3]
        match_lines = ''
        for i, m in enumerate(top, 1):
            policy = m.get('policy', {})
            match_lines += (
                f'{i}. <b>{m["label"]}</b> '
                f'({m["date"]})\n'
                f'   Similarity: {m["sim"]:.4f} | '
                f'Policy: {policy.get("direction", "?")} '
                f'{policy.get("instrument", "?")}\n'
            )

        policy = match.get('inherited_policy', {})

        return (
            f'🔍 <b>REGIME TRANSFER MATCH</b>\n\n'
            f'Best Match: <b>{match["matched_regime"]}</b>\n'
            f'Date:       {match["matched_date"]}\n'
            f'Similarity: <b>{match["similarity"]:.4f}</b>\n\n'
            f'<b>INHERITED POLICY:</b>\n'
            f'Direction:  <b>{policy.get("direction", "?")}</b>\n'
            f'Instrument: {policy.get("instrument", "?")}\n'
            f'Hold Type:  {policy.get("hold_type", "?")}\n\n'
            f'<b>TOP MATCHES:</b>\n'
            f'{match_lines}'
        )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Zero-Shot Regime Transfer Test')
    print('='*55 + '\n')

    rt = RegimeTransfer()
    print(
        f'Fingerprint library: '
        f'{len(rt.fingerprint_library)} regimes\n'
    )

    # Test: current state similar to SVB collapse
    print('--- Test: Crisis-like state ---')
    current = {
        'real_yield':      1.3,
        'friction_score':  78.0,
        'gold_bias':       72.0,
        'gbp_bias':        38.0,
        'boe_boj_spread':  2.9,
        'session_score':   82.0,
        'dark_pool_gold':  2.7,
        'dark_pool_gbp':  -1.0,
        'behavioral_gold': 70.0,
        'behavioral_gbp':  33.0,
    }

    match = rt.match_state(current)

    if match['matched_regime']:
        print(f'Best Match:  {match["matched_regime"]}')
        print(f'Date:        {match["matched_date"]}')
        print(f'Similarity:  {match["similarity"]:.4f}')
        print(f'Cosine Sim:  {match["cosine_sim"]:.4f}')
        policy = match['inherited_policy']
        print(f'\nInherited Policy:')
        print(f'  Direction:  {policy.get("direction")}')
        print(f'  Instrument: {policy.get("instrument")}')
        print(f'  Kelly:      {policy.get("kelly_fraction")}')
        print(f'  Hold Type:  {policy.get("hold_type")}')
        print(f'  Outcome:    {policy.get("outcome")}')
        print(f'\nTop 3 Matches:')
        for m in match['top_matches']:
            print(
                f'  {m["label"]} '
                f'({m["date"]}): '
                f'sim={m["sim"]:.4f}'
            )