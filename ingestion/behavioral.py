# ════════════════════════════════════════════════════════════════
# OMNINEXUS — ingestion/behavioral.py
# Behavioral Exhaust Fusion Engine
# Tracks what people DO digitally without knowing they're
# being observed — not what they say
#
# Sources:
#   1. Google Trends — delta acceleration on key terms
#   2. GitHub API   — fintech commit sentiment + velocity
# ════════════════════════════════════════════════════════════════

import requests
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.ingestion.behavioral')

# ── GOOGLE TRENDS KEYWORDS ─────────────────────────────────────
# We track the ACCELERATION of search interest
# not the raw trend level
# Grouped by instrument relevance

GOLD_KEYWORDS = [
    'gold price',
    'buy gold',
    'inflation hedge',
    'gold ETF',
    'safe haven',
    'store of value',
    'gold rally',
]

GBP_KEYWORDS = [
    'pound dollar',
    'bank of england',
    'uk inflation',
    'sterling',
    'gbp usd',
    'uk interest rate',
    'BoE rate decision',
]

MACRO_KEYWORDS = [
    'recession',
    'hyperinflation',
    'bank collapse',
    'currency crisis',
    'dollar collapse',
    'financial crisis',
]

# ── GITHUB FINTECH REPOS ───────────────────────────────────────
# Repositories we monitor for commit velocity and sentiment
# These are central bank and fintech open source projects
GITHUB_REPOS = [
    {'owner': 'federal-reserve',    'repo': 'FedNow'},
    {'owner': 'bank-of-england',    'repo': 'open-source-ai'},
    {'owner': 'openmf',             'repo': 'fineract'},
    {'owner': 'hyperledger',        'repo': 'firefly'},
    {'owner': 'stripe',             'repo': 'stripe-python'},
    {'owner': 'plaid',              'repo': 'plaid-python'},
]

# Commit message words that signal urgency or panic
URGENT_WORDS = [
    'urgent', 'critical', 'emergency', 'hotfix', 'fix',
    'bug', 'crash', 'broken', 'failed', 'error', 'security',
    'vulnerability', 'patch', 'rollback', 'revert', 'incident'
]

POSITIVE_WORDS = [
    'launch', 'release', 'feature', 'improvement', 'optimize',
    'enhance', 'upgrade', 'stable', 'production', 'deploy'
]


# ── GOOGLE TRENDS FETCHER ──────────────────────────────────────
def fetch_trends_acceleration(keywords: list) -> dict:
    """
    Fetches Google Trends interest for a list of keywords.
    Calculates the ACCELERATION (rate of change) not just level.
    Uses pytrends library for Google Trends API access.

    Returns dict with current interest, previous interest,
    delta, and acceleration score for each keyword.
    """
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(
            hl='en-US',
            tz=0,
            timeout=(10, 25),
            retries=2,
            backoff_factor=0.5
        )

        results = {}

        # Process keywords in batches of 5 (Google limit)
        batch_size = 5
        for i in range(0, len(keywords), batch_size):
            batch = keywords[i:i + batch_size]

            try:
                # Build payload for last 90 days
                pytrends.build_payload(
                    batch,
                    cat=0,
                    timeframe='today 3-m',
                    geo='',
                    gprop=''
                )

                # Get interest over time
                df = pytrends.interest_over_time()

                if df.empty:
                    logger.warning(
                        f'No trends data for batch: {batch}'
                    )
                    continue

                for keyword in batch:
                    if keyword not in df.columns:
                        continue

                    values = df[keyword].tolist()

                    if len(values) < 4:
                        continue

                    # Current = average of last 2 weeks
                    current = sum(values[-2:]) / 2

                    # Previous = average of weeks 3-4 ago
                    previous = sum(values[-4:-2]) / 2

                    # Delta = absolute change
                    delta = current - previous

                    # Acceleration = rate of change %
                    if previous > 0:
                        acceleration = (delta / previous) * 100
                    else:
                        acceleration = 0.0

                    results[keyword] = {
                        'current':      round(current, 1),
                        'previous':     round(previous, 1),
                        'delta':        round(delta, 1),
                        'acceleration': round(acceleration, 1),
                        'peak_recent':  max(values[-4:]),
                    }

                # Respect Google rate limits
                time.sleep(2)

            except Exception as e:
                logger.warning(
                    f'Trends batch failed {batch}: {e}'
                )
                continue

        return results

    except ImportError:
        logger.error('pytrends not installed')
        return {}
    except Exception as e:
        logger.error(f'Google Trends error: {e}')
        return {}


# ── GITHUB COMMIT FETCHER ──────────────────────────────────────
def fetch_github_commits(
    owner: str,
    repo: str,
    days_back: int = 7
) -> Optional[dict]:
    """
    Fetches recent commits from a GitHub repository.
    Analyzes commit message sentiment and velocity.
    Returns commit velocity and urgency score.
    """
    headers = {
        'Authorization': f'token {config.GITHUB_API_TOKEN}',
        'Accept':        'application/vnd.github.v3+json',
        'User-Agent':    'OmniNexus-Research'
    }

    since = (
        datetime.utcnow() - timedelta(days=days_back)
    ).strftime('%Y-%m-%dT%H:%M:%SZ')

    url = (
        f'https://api.github.com/repos/'
        f'{owner}/{repo}/commits'
    )

    params = {
        'since': since,
        'per_page': 30
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=10
        )

        if response.status_code == 404:
            # Repo doesn't exist or is private — skip silently
            return None

        if response.status_code == 403:
            logger.warning(
                f'GitHub rate limit hit for {owner}/{repo}'
            )
            return None

        if response.status_code != 200:
            logger.warning(
                f'GitHub {owner}/{repo}: '
                f'Status {response.status_code}'
            )
            return None

        commits = response.json()

        if not commits or not isinstance(commits, list):
            return None

        # Analyze commit messages
        urgent_count   = 0
        positive_count = 0
        total_commits  = len(commits)
        commit_hours   = []

        for commit in commits:
            msg = (
                commit.get('commit', {})
                      .get('message', '')
                      .lower()
            )

            # Check for urgent words
            for word in URGENT_WORDS:
                if word in msg:
                    urgent_count += 1
                    break

            # Check for positive words
            for word in POSITIVE_WORDS:
                if word in msg:
                    positive_count += 1
                    break

            # Track commit timing
            date_str = (
                commit.get('commit', {})
                      .get('author', {})
                      .get('date', '')
            )
            if date_str:
                try:
                    dt = datetime.strptime(
                        date_str,
                        '%Y-%m-%dT%H:%M:%SZ'
                    )
                    commit_hours.append(dt.hour)
                except Exception:
                    pass

        # Urgency score 0-100
        urgency_score = min(
            100,
            (urgent_count / max(total_commits, 1)) * 100 * 2
        )

        # Detect off-hours commits (potential emergency)
        off_hours = sum(
            1 for h in commit_hours
            if h < 6 or h > 22
        )
        off_hours_pct = (
            off_hours / max(len(commit_hours), 1)
        ) * 100

        return {
            'repo':           f'{owner}/{repo}',
            'total_commits':  total_commits,
            'urgent_count':   urgent_count,
            'positive_count': positive_count,
            'urgency_score':  round(urgency_score, 1),
            'off_hours_pct':  round(off_hours_pct, 1),
            'commits_per_day': round(total_commits / days_back, 1)
        }

    except requests.exceptions.Timeout:
        logger.error(f'GitHub timeout: {owner}/{repo}')
        return None
    except Exception as e:
        logger.error(f'GitHub error {owner}/{repo}: {e}')
        return None


# ── BEHAVIORAL EXHAUST AGGREGATOR ─────────────────────────────
def calculate_behavioral_exhaust() -> dict:
    """
    Main function. Combines Google Trends acceleration
    and GitHub commit sentiment into a single
    Behavioral Exhaust Score for each instrument.

    High score = unusual digital behavior = early signal
    """
    logger.info('Calculating Behavioral Exhaust signals...')

    # ── Google Trends ──────────────────────────────────────────
    logger.info('Fetching Google Trends data...')
    gold_trends  = fetch_trends_acceleration(GOLD_KEYWORDS)
    gbp_trends   = fetch_trends_acceleration(GBP_KEYWORDS)
    macro_trends = fetch_trends_acceleration(MACRO_KEYWORDS)

    # Calculate aggregate acceleration scores
    def avg_acceleration(trends: dict) -> float:
        if not trends:
            return 0.0
        accs = [
            v['acceleration']
            for v in trends.values()
            if 'acceleration' in v
        ]
        return round(sum(accs) / len(accs), 1) if accs else 0.0

    gold_trend_score  = avg_acceleration(gold_trends)
    gbp_trend_score   = avg_acceleration(gbp_trends)
    macro_trend_score = avg_acceleration(macro_trends)

    # Find top accelerating keyword per group
    def top_keyword(trends: dict) -> Optional[str]:
        if not trends:
            return None
        return max(
            trends.items(),
            key=lambda x: x[1].get('acceleration', 0)
        )[0]

    # ── GitHub Analysis ────────────────────────────────────────
    logger.info('Fetching GitHub commit data...')
    github_results  = []
    total_urgency   = 0.0
    repos_analyzed  = 0

    for repo_info in GITHUB_REPOS:
        result = fetch_github_commits(
            repo_info['owner'],
            repo_info['repo']
        )
        if result:
            github_results.append(result)
            total_urgency  += result['urgency_score']
            repos_analyzed += 1
        time.sleep(0.5)  # Respect GitHub rate limits

    avg_github_urgency = (
        total_urgency / repos_analyzed
        if repos_analyzed > 0 else 0.0
    )

    # ── Composite Behavioral Score ─────────────────────────────
    # Weight: Trends 60%, GitHub 40%
    gold_behavioral_score = min(100, (
        (abs(gold_trend_score) * 0.4) +
        (abs(macro_trend_score) * 0.2) +
        (avg_github_urgency * 0.4)
    ))

    gbp_behavioral_score = min(100, (
        (abs(gbp_trend_score) * 0.6) +
        (avg_github_urgency * 0.4)
    ))

    # ── Signal Interpretation ──────────────────────────────────
    def interpret_score(score: float) -> str:
        if score < 15:
            return 'DORMANT'
        elif score < 30:
            return 'LOW ACTIVITY'
        elif score < 50:
            return 'MODERATE'
        elif score < 70:
            return 'ELEVATED'
        else:
            return 'HIGH ALERT'

    result = {
        'timestamp':             datetime.utcnow().isoformat(),

        # Google Trends
        'gold_trend_score':      gold_trend_score,
        'gbp_trend_score':       gbp_trend_score,
        'macro_trend_score':     macro_trend_score,
        'gold_top_keyword':      top_keyword(gold_trends),
        'gbp_top_keyword':       top_keyword(gbp_trends),
        'macro_top_keyword':     top_keyword(macro_trends),

        # GitHub
        'github_repos_analyzed': repos_analyzed,
        'github_urgency_score':  round(avg_github_urgency, 1),
        'github_results':        github_results,

        # Composite
        'gold_behavioral_score': round(gold_behavioral_score, 1),
        'gbp_behavioral_score':  round(gbp_behavioral_score, 1),
        'gold_signal':           interpret_score(
                                     gold_behavioral_score
                                 ),
        'gbp_signal':            interpret_score(
                                     gbp_behavioral_score
                                 ),

        # Alert flags
        'gold_alert':            gold_behavioral_score >= 50,
        'gbp_alert':             gbp_behavioral_score >= 50,
    }

    logger.info(
        f'Behavioral Exhaust: '
        f'Gold={gold_behavioral_score:.1f} '
        f'({result["gold_signal"]}) | '
        f'GBP={gbp_behavioral_score:.1f} '
        f'({result["gbp_signal"]})'
    )

    return result


# ── TELEGRAM FORMATTER ─────────────────────────────────────────
def format_behavioral_alert(data: dict) -> str:
    """
    Formats behavioral exhaust data for Telegram.
    """
    if 'error' in data:
        return f'❌ Behavioral Error: {data["error"]}'

    gold_kw = data.get('gold_top_keyword', 'N/A') or 'N/A'
    gbp_kw  = data.get('gbp_top_keyword', 'N/A') or 'N/A'
    mac_kw  = data.get('macro_top_keyword', 'N/A') or 'N/A'

    alert_line = ''
    if data['gold_alert']:
        alert_line += (
            f'\n⚡ <b>GOLD BEHAVIORAL ALERT</b>\n'
            f'Unusual digital activity detected\n'
            f'Top signal: "{gold_kw}"\n'
        )
    if data['gbp_alert']:
        alert_line += (
            f'\n⚡ <b>GBP BEHAVIORAL ALERT</b>\n'
            f'Unusual digital activity detected\n'
            f'Top signal: "{gbp_kw}"\n'
        )

    return (
        f'🧠 <b>BEHAVIORAL EXHAUST REPORT</b>\n'
        f'<code>{data["timestamp"][:19]} UTC</code>\n\n'
        f'<b>GOOGLE TRENDS ACCELERATION:</b>\n'
        f'Gold terms:   <b>{data["gold_trend_score"]:+.1f}%</b>'
        f' → "{gold_kw}"\n'
        f'GBP terms:    <b>{data["gbp_trend_score"]:+.1f}%</b>'
        f' → "{gbp_kw}"\n'
        f'Macro terms:  '
        f'<b>{data["macro_trend_score"]:+.1f}%</b>'
        f' → "{mac_kw}"\n\n'
        f'<b>GITHUB URGENCY:</b>\n'
        f'Repos analyzed: {data["github_repos_analyzed"]}\n'
        f'Urgency score:  '
        f'<b>{data["github_urgency_score"]}/100</b>\n\n'
        f'<b>COMPOSITE SIGNALS:</b>\n'
        f'Gold Behavioral: '
        f'<b>{data["gold_behavioral_score"]}/100</b> '
        f'— {data["gold_signal"]}\n'
        f'GBP Behavioral:  '
        f'<b>{data["gbp_behavioral_score"]}/100</b> '
        f'— {data["gbp_signal"]}\n'
        f'{alert_line}'
    )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Behavioral Exhaust Engine Test')
    print('='*55 + '\n')
    print('Note: Google Trends takes 30-60 seconds...\n')

    data = calculate_behavioral_exhaust()

    print(f'Google Trends Acceleration:')
    print(f'  Gold terms:   {data["gold_trend_score"]:+.1f}%'
          f' (top: {data["gold_top_keyword"]})')
    print(f'  GBP terms:    {data["gbp_trend_score"]:+.1f}%'
          f' (top: {data["gbp_top_keyword"]})')
    print(f'  Macro terms:  {data["macro_trend_score"]:+.1f}%'
          f' (top: {data["macro_top_keyword"]})')
    print(f'\nGitHub Analysis:')
    print(f'  Repos analyzed: {data["github_repos_analyzed"]}')
    print(f'  Urgency score:  {data["github_urgency_score"]}/100')
    if data['github_results']:
        for r in data['github_results']:
            print(
                f'  {r["repo"]}: '
                f'{r["total_commits"]} commits | '
                f'urgency={r["urgency_score"]:.0f}'
            )
    print(f'\nComposite Behavioral Scores:')
    print(f'  Gold: {data["gold_behavioral_score"]}/100'
          f' — {data["gold_signal"]}')
    print(f'  GBP:  {data["gbp_behavioral_score"]}/100'
          f' — {data["gbp_signal"]}')
    print(f'\nAlerts:')
    print(f'  Gold Alert: {data["gold_alert"]}')
    print(f'  GBP Alert:  {data["gbp_alert"]}')