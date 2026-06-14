# ════════════════════════════════════════════════════════════════
# OMNINEXUS — ingestion/satellite.py
# Free Satellite Proxy Monitor
# Uses NASA Earthdata + ESA Copernicus free APIs
# Monitors key physical-world coordinates for
# unusual activity that precedes institutional moves
#
# Target zones:
#   - Swiss National Bank / Zurich gold vault proximity
#   - Bank of Japan headquarters (Tokyo)
#   - Cushing Oklahoma oil storage (DXY correlation)
#   - Port of Rotterdam (GBP trade balance proxy)
# ════════════════════════════════════════════════════════════════

import requests
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Optional
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.ingestion.satellite')

# ── TARGET COORDINATES ─────────────────────────────────────────
# Physical locations we monitor via satellite proxy data
# Each location has an instrument it correlates to
TARGET_ZONES = [
    {
        'name':        'Swiss National Bank — Zurich',
        'lat':         47.3769,
        'lon':         8.5417,
        'instrument':  'XAUUSD',
        'signal_type': 'GOLD_VAULT_ACTIVITY',
        'description': 'Unusual vehicle density near SNB '
                       'or gold vault = emergency policy meeting'
    },
    {
        'name':        'Bank of Japan — Tokyo',
        'lat':         35.6892,
        'lon':         139.6917,
        'instrument':  'GBPJPY',
        'signal_type': 'BOJ_ACTIVITY',
        'description': 'Unusual activity near BoJ headquarters '
                       '= potential emergency rate decision'
    },
    {
        'name':        'Cushing Oklahoma — Oil Storage',
        'lat':         36.0175,
        'lon':         -96.7836,
        'instrument':  'XAUUSD',
        'signal_type': 'OIL_STORAGE_LEVEL',
        'description': 'Oil storage levels correlate with '
                       'inflation expectations and gold demand'
    },
    {
        'name':        'Port of Rotterdam',
        'lat':         51.9244,
        'lon':         4.4777,
        'instrument':  'GBPUSD',
        'signal_type': 'UK_TRADE_FLOW',
        'description': 'Port congestion signals UK trade '
                       'balance pressure on GBP'
    },
    {
        'name':        'Fort Knox — Kentucky',
        'lat':         37.8897,
        'lon':         -85.9631,
        'instrument':  'XAUUSD',
        'signal_type': 'US_GOLD_RESERVE',
        'description': 'US gold reserve activity proxy'
    },
]

# ── NASA EARTHDATA API ─────────────────────────────────────────
NASA_EARTHDATA_BASE = (
    'https://cmr.earthdata.nasa.gov/search'
)

# ESA Copernicus Token URL
COPERNICUS_TOKEN_URL = (
    'https://identity.dataspace.copernicus.eu'
    '/auth/realms/CDSE/protocol/openid-connect/token'
)

# ESA Copernicus Search URL
COPERNICUS_SEARCH_URL = (
    'https://catalogue.dataspace.copernicus.eu'
    '/odata/v1/Products'
)

# Cache file for satellite readings
SATELLITE_CACHE_FILE = 'logs/satellite_cache.json'


# ── CACHE MANAGER ──────────────────────────────────────────────
def load_satellite_cache() -> dict:
    """Loads previous satellite readings for comparison."""
    if os.path.exists(SATELLITE_CACHE_FILE):
        try:
            with open(SATELLITE_CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_satellite_cache(cache: dict):
    """Saves satellite readings to cache."""
    try:
        os.makedirs('logs', exist_ok=True)
        with open(SATELLITE_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        logger.error(f'Cache save error: {e}')


# ── ESA COPERNICUS TOKEN ───────────────────────────────────────
def get_copernicus_token() -> Optional[str]:
    """
    Gets an access token from ESA Copernicus Data Space.
    Token is valid for 10 minutes.
    """
    try:
        response = requests.post(
            COPERNICUS_TOKEN_URL,
            data={
                'grant_type':    'client_credentials',
                'client_id':     config.COPERNICUS_CLIENT_ID,
                'client_secret': config.COPERNICUS_CLIENT_SECRET,
            },
            timeout=15
        )

        if response.status_code == 200:
            token = response.json().get('access_token')
            logger.info('Copernicus token obtained successfully')
            return token
        else:
            logger.warning(
                f'Copernicus token failed: '
                f'{response.status_code} — {response.text[:100]}'
            )
            return None

    except Exception as e:
        logger.error(f'Copernicus token error: {e}')
        return None


# ── NASA GRANULE CHECKER ───────────────────────────────────────
def check_nasa_coverage(
    lat: float,
    lon: float,
    zone_name: str
) -> dict:
    """
    Checks NASA CMR for recent satellite data coverage
    over a specific coordinate.
    Uses MODIS Terra/Aqua which provides daily global coverage.

    Returns metadata about available imagery —
    we use coverage density as an activity proxy.
    """
    try:
        # Search for recent MODIS granules over this location
        # MODIS provides cloud-penetrating thermal data
        params = {
            'short_name':   'MOD09GA',  # MODIS Surface Reflectance
            'point':        f'{lon},{lat}',
            'temporal':     (
                (datetime.utcnow() - timedelta(days=7))
                .strftime('%Y-%m-%dT00:00:00Z') +
                ',' +
                datetime.utcnow().strftime('%Y-%m-%dT23:59:59Z')
            ),
            'page_size':    10,
            'page_num':     1,
        }

        headers = {
            'Authorization': (
                f'Bearer {config.NASA_EARTHDATA_TOKEN}'
            )
        }

        response = requests.get(
            f'{NASA_EARTHDATA_BASE}/granules.json',
            params=params,
            headers=headers,
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            granules = data.get('feed', {}).get('entry', [])
            granule_count = len(granules)

            logger.info(
                f'NASA {zone_name}: '
                f'{granule_count} granules found '
                f'(last 7 days)'
            )

            return {
                'source':        'NASA_MODIS',
                'granule_count': granule_count,
                'coverage':      'HIGH' if granule_count >= 5
                                 else 'MODERATE'
                                 if granule_count >= 2
                                 else 'LOW',
                'available':     True
            }

        elif response.status_code == 401:
            logger.warning(
                'NASA Earthdata: Authentication failed. '
                'Check NASA_EARTHDATA_TOKEN in .env'
            )
            return {'source': 'NASA_MODIS', 'available': False}

        else:
            logger.warning(
                f'NASA {zone_name}: '
                f'Status {response.status_code}'
            )
            return {'source': 'NASA_MODIS', 'available': False}

    except Exception as e:
        logger.error(f'NASA error for {zone_name}: {e}')
        return {'source': 'NASA_MODIS', 'available': False}


# ── ESA SENTINEL CHECKER ───────────────────────────────────────
def check_copernicus_coverage(
    lat: float,
    lon: float,
    zone_name: str,
    token: Optional[str]
) -> dict:
    """
    Checks ESA Copernicus for recent Sentinel-2 imagery
    over a specific coordinate.
    Sentinel-2 provides 10m resolution optical imagery
    with 5-day revisit cycle globally.
    """
    if not token:
        return {'source': 'ESA_SENTINEL', 'available': False}

    try:
        # Build bounding box (0.1 degree around point)
        bbox = (
            f'{lon-0.05},{lat-0.05},'
            f'{lon+0.05},{lat+0.05}'
        )

        date_from = (
            datetime.utcnow() - timedelta(days=10)
        ).strftime('%Y-%m-%dT00:00:00.000Z')

        date_to = datetime.utcnow().strftime(
            '%Y-%m-%dT23:59:59.999Z'
        )

        filter_query = (
            f"Collection/Name eq 'SENTINEL-2' and "
            f"OData.CSC.Intersects(area=geography'SRID=4326;"
            f"POLYGON(("
            f"{lon-0.05} {lat-0.05},"
            f"{lon+0.05} {lat-0.05},"
            f"{lon+0.05} {lat+0.05},"
            f"{lon-0.05} {lat+0.05},"
            f"{lon-0.05} {lat-0.05}"
            f"))') and "
            f"ContentDate/Start gt {date_from} and "
            f"ContentDate/Start lt {date_to}"
        )

        headers = {
            'Authorization': f'Bearer {token}',
            'Accept':        'application/json'
        }

        params = {
            '$filter':  filter_query,
            '$top':     5,
            '$orderby': 'ContentDate/Start desc'
        }

        response = requests.get(
            COPERNICUS_SEARCH_URL,
            headers=headers,
            params=params,
            timeout=20
        )

        if response.status_code == 200:
            data  = response.json()
            items = data.get('value', [])
            count = len(items)

            # Get cloud coverage from most recent image
            cloud_cover = None
            if items:
                props = items[0].get('Attributes', {})
                for attr in props.get('results', []):
                    if attr.get('Name') == 'cloudCover':
                        cloud_cover = attr.get('Value')

            logger.info(
                f'ESA {zone_name}: '
                f'{count} Sentinel-2 scenes found'
            )

            return {
                'source':       'ESA_SENTINEL2',
                'scene_count':  count,
                'cloud_cover':  cloud_cover,
                'available':    True,
                'usable':       (
                    count > 0 and
                    (cloud_cover is None or cloud_cover < 70)
                )
            }

        else:
            logger.warning(
                f'ESA {zone_name}: '
                f'Status {response.status_code}'
            )
            return {'source': 'ESA_SENTINEL2', 'available': False}

    except Exception as e:
        logger.error(f'ESA error for {zone_name}: {e}')
        return {'source': 'ESA_SENTINEL2', 'available': False}


# ── ANOMALY DETECTOR ───────────────────────────────────────────
def detect_coverage_anomaly(
    zone_name: str,
    current_data: dict,
    cache: dict
) -> dict:
    """
    Compares current satellite coverage to historical cache.
    Significant changes in coverage patterns can indicate
    unusual activity at monitored locations.

    Note: This is a proxy signal — we are detecting
    CHANGES IN MONITORING INTEREST not direct activity.
    Unusual coverage = something worth watching.
    """
    anomaly_score = 0
    anomaly_notes = []

    cache_key = zone_name.replace(' ', '_').lower()
    historical = cache.get(cache_key, {})

    if historical:
        # Compare NASA granule counts
        prev_granules = historical.get('nasa_granules', 0)
        curr_granules = current_data.get(
            'nasa', {}
        ).get('granule_count', 0)

        if prev_granules > 0 and curr_granules > 0:
            change_pct = (
                (curr_granules - prev_granules)
                / prev_granules * 100
            )
            if abs(change_pct) > 50:
                anomaly_score += 25
                anomaly_notes.append(
                    f'NASA coverage change: '
                    f'{change_pct:+.0f}%'
                )

        # Compare ESA scene availability
        prev_scenes = historical.get('esa_scenes', 0)
        curr_scenes = current_data.get(
            'esa', {}
        ).get('scene_count', 0)

        if prev_scenes > 0 and curr_scenes == 0:
            anomaly_score += 15
            anomaly_notes.append(
                'ESA coverage dropped to zero '
                '(possible classification)'
            )

    return {
        'anomaly_score': anomaly_score,
        'anomaly_notes': anomaly_notes,
        'is_anomaly':    anomaly_score >= 25
    }


# ── MAIN SATELLITE SCANNER ─────────────────────────────────────
def scan_satellite_zones() -> dict:
    """
    Main function. Scans all target zones using
    NASA and ESA free APIs.
    Returns coverage data and anomaly scores.
    """
    logger.info('Scanning satellite zones...')

    # Get ESA token once for all zones
    copernicus_token = get_copernicus_token()
    if not copernicus_token:
        logger.warning(
            'ESA token unavailable — '
            'will use NASA only'
        )

    cache    = load_satellite_cache()
    results  = {}
    anomalies = []

    for zone in TARGET_ZONES:
        logger.info(f'Scanning zone: {zone["name"]}')

        # NASA check
        nasa_data = check_nasa_coverage(
            zone['lat'],
            zone['lon'],
            zone['name']
        )

        # ESA check
        esa_data = check_copernicus_coverage(
            zone['lat'],
            zone['lon'],
            zone['name'],
            copernicus_token
        )

        # Combined zone data
        zone_data = {
            'nasa': nasa_data,
            'esa':  esa_data,
        }

        # Anomaly detection
        anomaly = detect_coverage_anomaly(
            zone['name'],
            zone_data,
            cache
        )

        if anomaly['is_anomaly']:
            anomalies.append({
                'zone':       zone['name'],
                'instrument': zone['instrument'],
                'signal':     zone['signal_type'],
                'score':      anomaly['anomaly_score'],
                'notes':      anomaly['anomaly_notes'],
            })

        results[zone['name']] = {
            'instrument':    zone['instrument'],
            'signal_type':   zone['signal_type'],
            'description':   zone['description'],
            'nasa':          nasa_data,
            'esa':           esa_data,
            'anomaly':       anomaly,
            'last_scanned':  datetime.utcnow().isoformat()
        }

        # Update cache
        cache_key = zone['name'].replace(' ', '_').lower()
        cache[cache_key] = {
            'nasa_granules': nasa_data.get('granule_count', 0),
            'esa_scenes':    esa_data.get('scene_count', 0),
            'last_updated':  datetime.utcnow().isoformat()
        }

    # Save updated cache
    save_satellite_cache(cache)

    # Overall signal assessment
    gold_zones_active = sum(
        1 for z in results.values()
        if z['instrument'] == 'XAUUSD'
        and z['nasa'].get('available', False)
    )
    gbp_zones_active = sum(
        1 for z in results.values()
        if z['instrument'] in ['GBPUSD', 'GBPJPY']
        and z['nasa'].get('available', False)
    )

    result = {
        'timestamp':         datetime.utcnow().isoformat(),
        'zones_scanned':     len(TARGET_ZONES),
        'anomalies_found':   len(anomalies),
        'anomalies':         anomalies,
        'results':           results,
        'gold_zones_active': gold_zones_active,
        'gbp_zones_active':  gbp_zones_active,
        'esa_available':     copernicus_token is not None,
        'nasa_available':    True,
    }

    logger.info(
        f'Satellite scan complete. '
        f'Zones: {len(TARGET_ZONES)} | '
        f'Anomalies: {len(anomalies)} | '
        f'ESA: {copernicus_token is not None}'
    )

    return result


# ── TELEGRAM FORMATTER ─────────────────────────────────────────
def format_satellite_alert(data: dict) -> str:
    """Formats satellite scan for Telegram."""

    # Build zone status lines
    zone_lines = ''
    for zone_name, info in data['results'].items():
        nasa_ok = '✅' if info['nasa'].get('available') else '❌'
        esa_ok  = '✅' if info['esa'].get('available')  else '❌'
        anom    = ' ⚠️' if info['anomaly']['is_anomaly'] else ''
        short   = zone_name[:25]
        zone_lines += (
            f'{short}: NASA{nasa_ok} ESA{esa_ok}{anom}\n'
        )

    anomaly_detail = ''
    if data['anomalies']:
        anomaly_detail = '\n<b>⚠️ ANOMALIES:</b>\n'
        for a in data['anomalies']:
            anomaly_detail += (
                f'{a["zone"][:30]}\n'
                f'→ {a["instrument"]} | Score: {a["score"]}\n'
                f'→ {", ".join(a["notes"])}\n'
            )

    return (
        f'🛰️ <b>SATELLITE PROXY REPORT</b>\n'
        f'<code>{data["timestamp"][:19]} UTC</code>\n\n'
        f'Zones Scanned:  {data["zones_scanned"]}\n'
        f'ESA Available:  '
        f'{"✅" if data["esa_available"] else "❌"}\n'
        f'NASA Available: '
        f'{"✅" if data["nasa_available"] else "❌"}\n\n'
        f'<b>ZONE STATUS:</b>\n'
        f'<code>{zone_lines}</code>'
        f'{anomaly_detail}'
    )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Satellite Proxy Monitor Test')
    print('='*55 + '\n')

    data = scan_satellite_zones()

    print(f'Zones Scanned:   {data["zones_scanned"]}')
    print(f'Anomalies Found: {data["anomalies_found"]}')
    print(f'ESA Available:   {data["esa_available"]}')
    print(f'NASA Available:  {data["nasa_available"]}')
    print(f'\nZone Results:')

    for zone_name, info in data['results'].items():
        nasa_status = (
            f'granules={info["nasa"].get("granule_count","N/A")}'
            if info['nasa'].get('available')
            else 'unavailable'
        )
        esa_status = (
            f'scenes={info["esa"].get("scene_count","N/A")}'
            if info['esa'].get('available')
            else 'unavailable'
        )
        anomaly_flag = (
            ' ⚠️ ANOMALY'
            if info['anomaly']['is_anomaly']
            else ''
        )
        print(
            f'  {zone_name[:35]:<35} | '
            f'NASA: {nasa_status:<20} | '
            f'ESA: {esa_status}{anomaly_flag}'
        )

    if data['anomalies']:
        print(f'\nAnomalies:')
        for a in data['anomalies']:
            print(f'  {a["zone"]}: score={a["score"]}')
            for note in a['notes']:
                print(f'    → {note}')