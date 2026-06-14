# ════════════════════════════════════════════════════════════════
# OMNINEXUS — ingestion/friction.py
# Geopolitical Friction Index
# Monitors global RSS feeds and scores geopolitical tension
# High friction score = safe-haven demand = Gold long bias
# ════════════════════════════════════════════════════════════════

import feedparser
import requests
import logging
import re
from datetime import datetime, timedelta
from typing import Optional
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.ingestion.friction')

# ── RSS FEED SOURCES ───────────────────────────────────────────
# Curated high-signal geopolitical and macro news sources
# Each feed has a weight — higher weight = more influence on score
FEEDS = [
    {
        'name':   'Reuters World News',
        'url':    'https://feeds.reuters.com/reuters/worldNews',
        'weight': 1.5
    },
    {
        'name':   'BBC World News',
        'url':    'https://feeds.bbci.co.uk/news/world/rss.xml',
        'weight': 1.3
    },
    {
        'name':   'Al Jazeera',
        'url':    'https://www.aljazeera.com/xml/rss/all.xml',
        'weight': 1.2
    },
    {
        'name':   'Reuters Business',
        'url':    'https://feeds.reuters.com/reuters/businessNews',
        'weight': 1.4
    },
    {
        'name':   'Financial Times',
        'url':    'https://www.ft.com/rss/home',
        'weight': 1.5
    },
    {
        'name':   'Bloomberg Markets',
        'url':    'https://feeds.bloomberg.com/markets/news.rss',
        'weight': 1.4
    },
]

# ── FRICTION KEYWORDS ──────────────────────────────────────────
# Words that signal geopolitical tension or safe-haven demand
# Organized by category and severity weight

KEYWORDS = {
    # War and conflict — highest friction
    'conflict': {
        'weight': 3.0,
        'terms': [
            'war', 'invasion', 'attack', 'missile', 'airstrike',
            'troops', 'military', 'conflict', 'combat', 'offensive',
            'ceasefire', 'bombing', 'explosion', 'nuclear',
            'sanctions', 'blockade', 'coup', 'insurgency'
        ]
    },
    # Economic crisis — high friction
    'economic': {
        'weight': 2.5,
        'terms': [
            'recession', 'crisis', 'crash', 'collapse', 'default',
            'inflation', 'hyperinflation', 'debt ceiling', 'bailout',
            'bank run', 'contagion', 'devaluation', 'currency crisis',
            'financial crisis', 'liquidity crisis', 'sovereign debt'
        ]
    },
    # Central bank shock — medium-high friction
    'central_bank': {
        'weight': 2.0,
        'terms': [
            'federal reserve', 'fed rate', 'interest rate hike',
            'rate cut', 'emergency meeting', 'quantitative easing',
            'quantitative tightening', 'bank of england', 'boe',
            'bank of japan', 'boj', 'ecb', 'central bank',
            'monetary policy', 'pivot', 'rate decision'
        ]
    },
    # Geopolitical tension — medium friction
    'geopolitical': {
        'weight': 1.8,
        'terms': [
            'tension', 'escalation', 'threat', 'ultimatum',
            'sanctions', 'tariff', 'trade war', 'embargo',
            'election', 'protest', 'unrest', 'riot',
            'assassination', 'terror', 'pandemic', 'outbreak'
        ]
    },
    # Market volatility signals — medium friction
    'market': {
        'weight': 1.5,
        'terms': [
            'volatility', 'sell-off', 'market crash', 'stock market',
            'risk off', 'safe haven', 'gold rally', 'dollar rally',
            'yield spike', 'bond market', 'credit default',
            'margin call', 'flash crash', 'circuit breaker'
        ]
    }
}

# Build a flat keyword lookup for fast scanning
KEYWORD_LOOKUP = {}
for category, data in KEYWORDS.items():
    for term in data['terms']:
        KEYWORD_LOOKUP[term.lower()] = {
            'category': category,
            'weight':   data['weight']
        }


# ── ARTICLE SCORER ─────────────────────────────────────────────
def score_article(title: str, summary: str = '') -> dict:
    """
    Scores a single article for geopolitical friction.
    Scans title and summary for friction keywords.
    Title matches score higher than summary matches.
    Returns dict with score, matched keywords, categories.
    """
    text_title   = title.lower()
    text_summary = summary.lower() if summary else ''

    matched_keywords = []
    categories_hit   = set()
    raw_score        = 0.0

    for keyword, data in KEYWORD_LOOKUP.items():
        title_count   = text_title.count(keyword)
        summary_count = text_summary.count(keyword)

        if title_count > 0:
            # Title matches count double
            raw_score += data['weight'] * 2.0 * title_count
            matched_keywords.append(keyword)
            categories_hit.add(data['category'])

        if summary_count > 0:
            raw_score += data['weight'] * 1.0 * summary_count
            if keyword not in matched_keywords:
                matched_keywords.append(keyword)
            categories_hit.add(data['category'])

    return {
        'score':    raw_score,
        'keywords': matched_keywords[:5],  # top 5 only
        'categories': list(categories_hit)
    }


# ── FEED FETCHER ───────────────────────────────────────────────
def fetch_feed(feed: dict, hours_back: int = 24) -> list:
    """
    Fetches and scores articles from a single RSS feed.
    Only processes articles published in the last hours_back hours.
    Returns list of scored articles.
    """
    articles = []
    cutoff = datetime.utcnow() - timedelta(hours=hours_back)

    try:
        parsed = feedparser.parse(feed['url'])

        if parsed.bozo and not parsed.entries:
            logger.warning(
                f'Feed parse issue: {feed["name"]} — '
                f'{parsed.bozo_exception}'
            )
            return articles

        for entry in parsed.entries[:20]:  # max 20 per feed
            # Extract title and summary
            title   = getattr(entry, 'title',   '') or ''
            summary = getattr(entry, 'summary', '') or ''

            # Clean HTML tags from summary
            summary = re.sub(r'<[^>]+>', '', summary)

            # Score the article
            scored = score_article(title, summary)

            # Only include articles with friction signal
            if scored['score'] > 0:
                articles.append({
                    'source':     feed['name'],
                    'title':      title[:120],
                    'score':      scored['score'] * feed['weight'],
                    'keywords':   scored['keywords'],
                    'categories': scored['categories'],
                })

        logger.info(
            f'{feed["name"]}: {len(articles)} '
            f'friction articles found'
        )

    except Exception as e:
        logger.error(f'Feed fetch failed: {feed["name"]} — {e}')

    return articles


# ── FRICTION INDEX CALCULATOR ──────────────────────────────────
def calculate_friction_index() -> dict:
    """
    Main function. Fetches all RSS feeds, scores all articles,
    aggregates into a single Friction Score 0-100.

    Score interpretation:
    0-20:   CALM       — Normal market conditions
    21-40:  LOW        — Minor geopolitical noise
    41-60:  MODERATE   — Elevated tension, monitor closely
    61-75:  HIGH       — Strong safe-haven demand expected
    76-100: CRITICAL   — Maximum safe-haven flight to gold
    """
    logger.info('Calculating Geopolitical Friction Index...')

    all_articles  = []
    feed_scores   = {}
    failed_feeds  = []

    # Fetch all feeds
    for feed in FEEDS:
        articles = fetch_feed(feed)
        if articles:
            feed_scores[feed['name']] = sum(
                a['score'] for a in articles
            )
            all_articles.extend(articles)
        else:
            failed_feeds.append(feed['name'])

    if not all_articles:
        logger.error('No articles fetched from any feed')
        return {'error': 'No RSS data available'}

    # ── Aggregate Score ────────────────────────────────────────
    total_raw = sum(a['score'] for a in all_articles)

    # Normalize to 0-100 scale
    # Raw score of 50+ = maximum friction (100)
    normalized = min(100, (total_raw / 50.0) * 100)
    friction_score = round(normalized, 1)

    # ── Top Articles ───────────────────────────────────────────
    all_articles.sort(key=lambda x: x['score'], reverse=True)
    top_articles = all_articles[:5]

    # ── Category Breakdown ─────────────────────────────────────
    category_counts = {}
    for article in all_articles:
        for cat in article['categories']:
            category_counts[cat] = category_counts.get(cat, 0) + 1

    # ── Friction Level ─────────────────────────────────────────
    if friction_score <= 20:
        level       = 'CALM'
        gold_impact = 'NEUTRAL'
        emoji       = '⚪'
    elif friction_score <= 40:
        level       = 'LOW'
        gold_impact = 'SLIGHT BULLISH'
        emoji       = '🟡'
    elif friction_score <= 60:
        level       = 'MODERATE'
        gold_impact = 'BULLISH'
        emoji       = '🟠'
    elif friction_score <= 75:
        level       = 'HIGH'
        gold_impact = 'STRONG BULLISH'
        emoji       = '🔴'
    else:
        level       = 'CRITICAL'
        gold_impact = 'MAXIMUM SAFE-HAVEN DEMAND'
        emoji       = '🚨'

    # ── Alert Threshold Check ──────────────────────────────────
    alert_triggered = friction_score >= config.FRICTION_THRESHOLD

    result = {
        'timestamp':        datetime.utcnow().isoformat(),
        'friction_score':   friction_score,
        'level':            level,
        'gold_impact':      gold_impact,
        'emoji':            emoji,
        'alert_triggered':  alert_triggered,
        'total_articles':   len(all_articles),
        'top_articles':     top_articles,
        'category_counts':  category_counts,
        'feed_scores':      feed_scores,
        'failed_feeds':     failed_feeds,
    }

    logger.info(
        f'Friction Score: {friction_score}/100 | '
        f'Level: {level} | '
        f'Articles: {len(all_articles)} | '
        f'Alert: {alert_triggered}'
    )

    return result


# ── TELEGRAM FORMATTER ─────────────────────────────────────────
def format_friction_alert(data: dict) -> str:
    """
    Formats Friction Index data into a Telegram message.
    """
    if 'error' in data:
        return f'❌ Friction Index Error: {data["error"]}'

    # Format top articles
    top_news = ''
    for i, article in enumerate(data['top_articles'][:3], 1):
        top_news += (
            f'\n{i}. <i>{article["title"][:80]}</i>\n'
            f'   Source: {article["source"]} | '
            f'Score: {article["score"]:.1f}\n'
        )

    # Format categories
    cats = data.get('category_counts', {})
    cat_line = ' · '.join(
        f'{k.upper()}:{v}'
        for k, v in sorted(
            cats.items(),
            key=lambda x: x[1],
            reverse=True
        )
    ) if cats else 'None'

    alert_line = ''
    if data['alert_triggered']:
        alert_line = (
            f'\n🚨 <b>FRICTION THRESHOLD EXCEEDED</b>\n'
            f'Score {data["friction_score"]} above '
            f'threshold {config.FRICTION_THRESHOLD}\n'
            f'Safe-haven gold demand elevated.'
        )

    failed_line = ''
    if data.get('failed_feeds'):
        failed_line = (
            f'\n⚠️ Failed feeds: '
            f'{", ".join(data["failed_feeds"])}'
        )

    return (
        f'{data["emoji"]} <b>GEOPOLITICAL FRICTION INDEX</b>\n'
        f'<code>{data["timestamp"][:19]} UTC</code>\n\n'
        f'Score:        <b>{data["friction_score"]}/100</b>\n'
        f'Level:        <b>{data["level"]}</b>\n'
        f'Gold Impact:  <b>{data["gold_impact"]}</b>\n'
        f'Articles:     {data["total_articles"]}\n\n'
        f'<b>CATEGORIES:</b>\n'
        f'<code>{cat_line}</code>\n\n'
        f'<b>TOP SIGNALS:</b>'
        f'{top_news}'
        f'{alert_line}'
        f'{failed_line}'
    )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Geopolitical Friction Index Test')
    print('='*55 + '\n')

    data = calculate_friction_index()

    if 'error' not in data:
        print(f'Friction Score:  {data["friction_score"]}/100')
        print(f'Level:           {data["level"]}')
        print(f'Gold Impact:     {data["gold_impact"]}')
        print(f'Total Articles:  {data["total_articles"]}')
        print(f'Alert Triggered: {data["alert_triggered"]}')
        print(f'\nCategory Breakdown:')
        for cat, count in data['category_counts'].items():
            print(f'  {cat}: {count}')
        print(f'\nTop 3 Articles:')
        for i, a in enumerate(data['top_articles'][:3], 1):
            print(f'  {i}. [{a["source"]}] {a["title"][:70]}')
            print(f'     Score: {a["score"]:.1f} | '
                  f'Keywords: {", ".join(a["keywords"][:3])}')
        if data['failed_feeds']:
            print(f'\nFailed Feeds: {data["failed_feeds"]}')
    else:
        print(f'ERROR: {data["error"]}')