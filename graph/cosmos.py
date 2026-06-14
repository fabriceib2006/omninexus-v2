# ════════════════════════════════════════════════════════════════
# OMNINEXUS — graph/cosmos.py
# Azure Cosmos DB Gremlin Graph Connector
# The World Dependency Graph — "The Cause Web"
# Every signal from the ingestion layer writes here
# Multi-hop causal chains calculated in real-time
# ════════════════════════════════════════════════════════════════

import logging
import json
from datetime import datetime
from typing import Optional, Any
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.graph.cosmos')

# ── GREMLIN CONNECTION ─────────────────────────────────────────
# Cosmos DB Gremlin endpoint format:
# wss://YOUR-ACCOUNT.gremlin.cosmos.azure.com:443/
def get_gremlin_client():
    """
    Creates and returns a Gremlin client connected
    to Azure Cosmos DB.
    """
    try:
        from gremlin_python.driver import client, serializer

        # Extract account name from endpoint URL
        # Format: https://NAME.documents.azure.com:443/
        endpoint = config.COSMOS_ENDPOINT
        account  = (
            endpoint
            .replace('https://', '')
            .replace('.documents.azure.com:443/', '')
            .replace('http://', '')
            .split('.')[0]
        )

        gremlin_endpoint = (
            f'wss://{account}'
            f'.gremlin.cosmos.azure.com:443/'
        )

        gremlin_client = client.Client(
            url         = gremlin_endpoint,
            traversal_source = 'g',
            username    = (
                f'/dbs/OmniNexusGraph'
                f'/colls/WorldGraph'
            ),
            password    = config.COSMOS_KEY,
            message_serializer = serializer.GraphSONSerializersV2d0()
        )

        logger.info('Gremlin client connected successfully')
        return gremlin_client

    except ImportError:
        logger.error(
            'gremlinpython not installed. '
            'Run: pip install gremlinpython'
        )
        return None
    except Exception as e:
        logger.error(f'Gremlin connection error: {e}')
        return None


def execute_gremlin(
    gremlin_client,
    query: str,
    bindings: dict = None
) -> Optional[list]:
    """
    Executes a single Gremlin query and returns results.
    Handles connection errors gracefully.
    """
    try:
        if bindings:
            callback = gremlin_client.submitAsync(
                query,
                bindings=bindings
            )
        else:
            callback = gremlin_client.submitAsync(query)

        results = []
        for result in callback.result():
            results.extend(result)

        return results

    except Exception as e:
        logger.error(f'Gremlin query error: {e}')
        logger.error(f'Query was: {query[:100]}')
        return None


# ── VERTEX WRITERS ─────────────────────────────────────────────
# Vertices = nodes in the graph (signals, instruments, events)
# Edges    = relationships between nodes (causal links)

def upsert_signal_vertex(
    gremlin_client,
    signal_id: str,
    signal_type: str,
    instrument: str,
    value: float,
    metadata: dict = None
) -> bool:
    """
    Creates or updates a signal vertex in the graph.
    Each signal is a node — e.g. REAL_YIELD, FRICTION_SCORE
    Uses upsert pattern: update if exists, create if not.

    signal_id:   unique identifier e.g. 'real_yield_xauusd'
    signal_type: e.g. 'REAL_YIELD', 'FRICTION', 'DARK_POOL'
    instrument:  e.g. 'XAUUSD', 'GBPUSD', 'GBPJPY'
    value:       current numeric value of the signal
    metadata:    additional properties as dict
    """
    try:
        timestamp = datetime.utcnow().isoformat()
        meta_str  = json.dumps(metadata or {})

        query = (
            "g.V().has('signal_id', signal_id)"
            ".fold()"
            ".coalesce("
            "  unfold(),"
            "  addV('signal')"
            "  .property('signal_id',   signal_id)"
            "  .property('partitionKey', signal_id)"
            ")"
            ".property('signal_type', signal_type)"
            ".property('instrument',  instrument)"
            ".property('value',       value)"
            ".property('timestamp',   timestamp)"
            ".property('metadata',    meta_str)"
        )

        bindings = {
            'signal_id':   signal_id,
            'signal_type': signal_type,
            'instrument':  instrument,
            'value':       value,
            'timestamp':   timestamp,
            'meta_str':    meta_str,
        }

        result = execute_gremlin(
            gremlin_client,
            query,
            bindings
        )

        if result is not None:
            logger.info(
                f'Vertex upserted: {signal_id} = {value}'
            )
            return True

        return False

    except Exception as e:
        logger.error(f'Vertex upsert error: {e}')
        return False


def upsert_edge(
    gremlin_client,
    from_id: str,
    to_id: str,
    edge_label: str,
    weight: float,
    description: str = ''
) -> bool:
    """
    Creates or updates a directed edge between two vertices.
    Edges represent causal relationships with weights.

    Example:
        from_id:    'real_yield_xauusd'
        to_id:      'breakout_signal_xauusd'
        edge_label: 'CAUSES'
        weight:     0.85  (strength of causal link)

    Higher weight = stronger causal relationship.
    The CFR engine uses these weights to calculate
    ripple effects across the dependency graph.
    """
    try:
        timestamp = datetime.utcnow().isoformat()

        query = (
            "g.V().has('signal_id', from_id).as('a')"
            ".V().has('signal_id', to_id).as('b')"
            ".coalesce("
            "  select('a').outE(edge_label)"
            "             .where(inV().as('b')),"
            "  addE(edge_label).from('a').to('b')"
            ")"
            ".property('weight',      weight)"
            ".property('description', description)"
            ".property('updated',     timestamp)"
        )

        bindings = {
            'from_id':     from_id,
            'to_id':       to_id,
            'edge_label':  edge_label,
            'weight':      weight,
            'description': description,
            'timestamp':   timestamp,
        }

        result = execute_gremlin(
            gremlin_client,
            query,
            bindings
        )

        if result is not None:
            logger.info(
                f'Edge upserted: {from_id} '
                f'--[{edge_label}:{weight}]--> {to_id}'
            )
            return True

        return False

    except Exception as e:
        logger.error(f'Edge upsert error: {e}')
        return False


# ── GRAPH READERS ──────────────────────────────────────────────

def get_signal_value(
    gremlin_client,
    signal_id: str
) -> Optional[float]:
    """
    Reads the current value of a signal vertex.
    Returns None if signal not found.
    """
    try:
        query = (
            "g.V().has('signal_id', signal_id)"
            ".values('value')"
        )
        result = execute_gremlin(
            gremlin_client,
            query,
            {'signal_id': signal_id}
        )

        if result:
            return float(result[0])
        return None

    except Exception as e:
        logger.error(f'Signal read error: {e}')
        return None


def get_all_signals(gremlin_client) -> list:
    """
    Returns all signal vertices currently in the graph.
    Used by /status and /signals Telegram commands.
    """
    try:
        query = (
            "g.V().hasLabel('signal')"
            ".project('id','type','instrument','value','time')"
            ".by('signal_id')"
            ".by('signal_type')"
            ".by('instrument')"
            ".by('value')"
            ".by('timestamp')"
        )

        result = execute_gremlin(gremlin_client, query)
        return result or []

    except Exception as e:
        logger.error(f'Get all signals error: {e}')
        return []


def get_instrument_signals(
    gremlin_client,
    instrument: str
) -> list:
    """
    Returns all signals related to a specific instrument.
    e.g. get_instrument_signals(client, 'XAUUSD')
    returns Real Yield, Friction, Dark Pool for Gold.
    """
    try:
        query = (
            "g.V().hasLabel('signal')"
            ".has('instrument', instrument)"
            ".project('id','type','value','time')"
            ".by('signal_id')"
            ".by('signal_type')"
            ".by('value')"
            ".by('timestamp')"
        )

        result = execute_gremlin(
            gremlin_client,
            query,
            {'instrument': instrument}
        )
        return result or []

    except Exception as e:
        logger.error(
            f'Instrument signals error for {instrument}: {e}'
        )
        return []


def get_causal_chain(
    gremlin_client,
    signal_id: str,
    max_hops: int = 3
) -> list:
    """
    Traverses the causal chain from a signal vertex.
    Returns all downstream signals within max_hops.

    This is the core multi-hop ripple effect query.
    Example: Real Yield → Gold Bias → Breakout Signal
    """
    try:
        query = (
            f"g.V().has('signal_id', signal_id)"
            f".repeat(out().simplePath())"
            f".times({max_hops})"
            f".path()"
            f".by('signal_id')"
        )

        result = execute_gremlin(
            gremlin_client,
            query,
            {'signal_id': signal_id}
        )
        return result or []

    except Exception as e:
        logger.error(f'Causal chain error: {e}')
        return []


def count_graph_nodes(gremlin_client) -> int:
    """Returns total number of vertices in the graph."""
    try:
        result = execute_gremlin(
            gremlin_client,
            "g.V().count()"
        )
        if result:
            return int(result[0])
        return 0
    except Exception as e:
        logger.error(f'Node count error: {e}')
        return 0


# ── GRAPH INITIALIZER ──────────────────────────────────────────
def initialize_graph(gremlin_client) -> bool:
    """
    Sets up the initial graph structure.
    Creates all instrument vertices and
    known causal relationship edges.
    Called once on first deployment.
    """
    logger.info('Initializing World Dependency Graph...')

    # ── Instrument vertices ────────────────────────────────────
    instruments = [
        ('instrument_xauusd', 'INSTRUMENT', 'XAUUSD', 0.0),
        ('instrument_gbpusd', 'INSTRUMENT', 'GBPUSD', 0.0),
        ('instrument_gbpjpy', 'INSTRUMENT', 'GBPJPY', 0.0),
    ]

    for sig_id, sig_type, instr, val in instruments:
        upsert_signal_vertex(
            gremlin_client,
            sig_id, sig_type, instr, val
        )

    # ── Core signal vertices ───────────────────────────────────
    signals = [
        # Gold signals
        ('real_yield',       'REAL_YIELD',     'XAUUSD', 0.0),
        ('friction_score',   'FRICTION',       'XAUUSD', 0.0),
        ('gold_dark_pool',   'DARK_POOL',      'XAUUSD', 0.0),
        ('gold_behavioral',  'BEHAVIORAL',     'XAUUSD', 0.0),
        ('gold_satellite',   'SATELLITE',      'XAUUSD', 0.0),
        ('gold_bias',        'BIAS_SCORE',     'XAUUSD', 0.0),
        # GBP signals
        ('boe_boj_spread',   'YIELD_SPREAD',   'GBPJPY', 0.0),
        ('gbp_dark_pool',    'DARK_POOL',      'GBPUSD', 0.0),
        ('gbp_behavioral',   'BEHAVIORAL',     'GBPUSD', 0.0),
        ('session_detector', 'SESSION',        'GBPJPY', 0.0),
        ('gbp_bias',         'BIAS_SCORE',     'GBPUSD', 0.0),
        # Macro
        ('dxy_correlation',  'MACRO',          'XAUUSD', 0.0),
        ('regime_score',     'REGIME',         'ALL',    0.0),
        ('cfr_regret',       'CFR',            'ALL',    0.0),
    ]

    for sig_id, sig_type, instr, val in signals:
        upsert_signal_vertex(
            gremlin_client,
            sig_id, sig_type, instr, val
        )

    # ── Causal edges ───────────────────────────────────────────
    # These encode the known causal relationships
    # Weight = strength of causal link (0.0 to 1.0)
    edges = [
        # Gold causal chain
        ('real_yield',      'gold_bias',         'DRIVES',    0.90,
         'Real yield is primary driver of gold price'),
        ('friction_score',  'gold_bias',         'DRIVES',    0.75,
         'Geopolitical friction drives safe-haven demand'),
        ('gold_dark_pool',  'gold_bias',         'LEADS',     0.70,
         'Dark pool accumulation leads spot price 12-72h'),
        ('gold_behavioral', 'gold_bias',         'SIGNALS',   0.55,
         'Behavioral exhaust signals retail sentiment shift'),
        ('gold_satellite',  'friction_score',    'CONFIRMS',  0.60,
         'Satellite activity confirms geopolitical events'),
        ('gold_bias',       'instrument_xauusd', 'PREDICTS',  0.80,
         'Aggregated gold bias predicts XAUUSD direction'),

        # GBP causal chain
        ('boe_boj_spread',  'gbp_bias',          'DRIVES',    0.85,
         'BoE/BoJ spread is primary GBPJPY driver'),
        ('gbp_dark_pool',   'gbp_bias',          'LEADS',     0.70,
         'GBP dark pool positioning leads spot 12-72h'),
        ('gbp_behavioral',  'gbp_bias',          'SIGNALS',   0.50,
         'GBP behavioral exhaust signals retail shift'),
        ('session_detector','gbp_bias',          'TRIGGERS',  0.65,
         'Session transition triggers liquidity events'),
        ('gbp_bias',        'instrument_gbpusd', 'PREDICTS',  0.75,
         'GBP bias predicts GBPUSD direction'),
        ('gbp_bias',        'instrument_gbpjpy', 'PREDICTS',  0.80,
         'GBP bias combined with spread predicts GBPJPY'),

        # Cross-instrument links
        ('friction_score',  'gbp_bias',          'INFLUENCES',0.45,
         'High friction affects all risk assets'),
        ('real_yield',      'dxy_correlation',   'DRIVES',    0.80,
         'Real yield drives DXY strength'),
        ('dxy_correlation', 'gold_bias',         'INVERSELY', 0.75,
         'DXY strength inversely affects gold'),
        ('regime_score',    'cfr_regret',        'GATES',     0.95,
         'Regime state gates CFR decision confidence'),
    ]

    for from_id, to_id, label, weight, desc in edges:
        upsert_edge(
            gremlin_client,
            from_id, to_id,
            label, weight, desc
        )

    node_count = count_graph_nodes(gremlin_client)
    logger.info(
        f'Graph initialized. '
        f'Total nodes: {node_count}'
    )
    return True


# ── SIGNAL UPDATER ─────────────────────────────────────────────
def update_signal(
    gremlin_client,
    signal_id: str,
    value: float,
    metadata: dict = None
) -> bool:
    """
    Quick update of a signal's current value.
    Called by each ingestion module after calculating
    its signal. This is the main write operation
    used throughout the system.
    """
    try:
        timestamp = datetime.utcnow().isoformat()
        meta_str  = json.dumps(metadata or {})

        query = (
            "g.V().has('signal_id', signal_id)"
            ".property('value',     value)"
            ".property('timestamp', timestamp)"
            ".property('metadata',  meta_str)"
        )

        result = execute_gremlin(
            gremlin_client,
            query,
            {
                'signal_id': signal_id,
                'value':     value,
                'timestamp': timestamp,
                'meta_str':  meta_str,
            }
        )

        if result is not None:
            logger.info(
                f'Signal updated: {signal_id} = {value}'
            )
            return True

        return False

    except Exception as e:
        logger.error(f'Signal update error: {e}')
        return False


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Cosmos DB Graph Connector Test')
    print('='*55 + '\n')

    print('Connecting to Azure Cosmos DB...')
    gc = get_gremlin_client()

    if gc is None:
        print('❌ Connection failed.')
        print('Check COSMOS_ENDPOINT and COSMOS_KEY in .env')
        sys.exit(1)

    print('✅ Connected to Cosmos DB\n')

    print('Initializing World Dependency Graph...')
    success = initialize_graph(gc)

    if success:
        print('✅ Graph initialized\n')
        node_count = count_graph_nodes(gc)
        print(f'Total nodes in graph: {node_count}')

        print('\nReading all signals...')
        signals = get_all_signals(gc)
        print(f'Signals found: {len(signals)}')
        for s in signals[:5]:
            print(f'  {s}')
        if len(signals) > 5:
            print(f'  ... and {len(signals)-5} more')

        print('\nTesting signal update...')
        updated = update_signal(
            gc,
            'real_yield',
            2.180,
            {'method': 'TIPS_DIRECT', 'bias': 'STRONG BEARISH'}
        )
        print(
            f'Signal update: '
            f'{"✅ Success" if updated else "❌ Failed"}'
        )

        print('\nTesting causal chain query...')
        chain = get_causal_chain(gc, 'real_yield', max_hops=2)
        print(f'Causal chain hops found: {len(chain)}')

    else:
        print('❌ Graph initialization failed')

    gc.close()
    print('\nConnection closed.')