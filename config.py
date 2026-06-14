# ════════════════════════════════════════════════════════════════
# OMNINEXUS — config.py
# Central configuration and secret management
# Loads all secrets from Azure Key Vault with .env fallback
# ════════════════════════════════════════════════════════════════

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# ── LOGGING SETUP ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('logs/omninexus.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('omninexus.config')

logging.getLogger('azure').setLevel(logging.ERROR)
logging.getLogger('azure.identity').setLevel(logging.ERROR)
logging.getLogger('azure.core').setLevel(logging.ERROR)


# ── AZURE KEY VAULT LOADER ─────────────────────────────────────
def get_secret(secret_name: str, required: bool = True) -> str:
    """
    Fetches a secret from Azure Key Vault.
    Falls back to .env file if Key Vault is unreachable.
    If required=False, returns None instead of raising error.
    """
    try:
        from azure.keyvault.secrets import SecretClient
        from azure.identity import DefaultAzureCredential
        vault_url = os.getenv('AZURE_KEY_VAULT_URL')
        if vault_url:
            credential = DefaultAzureCredential()
            client = SecretClient(
                vault_url=vault_url,
                credential=credential
            )
            secret = client.get_secret(secret_name)
            logger.info(f'Secret loaded from Key Vault: {secret_name}')
            return secret.value
    except Exception as e:
        logger.warning(
            f'Key Vault unavailable for {secret_name}. '
            f'Falling back to .env. Reason: {e}'
        )

    env_name = secret_name.replace('-', '_')
    value = os.getenv(env_name)
    if value:
        logger.info(f'Secret loaded from .env: {env_name}')
        return value

    if not required:
        logger.warning(f'Optional secret not found: {secret_name}')
        return None

    logger.error(f'Secret not found anywhere: {secret_name}')
    raise ValueError(
        f'Secret "{secret_name}" not found in '
        f'Key Vault or .env file.'
    )


# ── CONFIG CLASS ───────────────────────────────────────────────
class Config:

    def __init__(self):
        logger.info('Loading OmniNexus configuration...')

        # ── Telegram ───────────────────────────────────────────
        self.TELEGRAM_BOT_TOKEN       = get_secret('TELEGRAM-BOT-TOKEN')
        self.TELEGRAM_CHAT_ID         = get_secret('TELEGRAM-CHAT-ID')

        # ── Market Data APIs ───────────────────────────────────
        self.TWELVE_DATA_API_KEY      = get_secret('TWELVE-DATA-API-KEY')
        self.FINNHUB_API_KEY          = get_secret('FINNHUB-API-KEY')
        self.FRED_API_KEY             = get_secret('FRED-API-KEY')
        self.GITHUB_API_TOKEN         = get_secret('GITHUB-API-TOKEN')
        self.TWELVE_DATA_API_KEY      = get_secret('TWELVE-DATA-API-KEY')
        self.TWELVE_DATA_API_KEY_2    = get_secret('TWELVE-DATA-API-KEY-2', required=False)
        self.TWELVE_DATA_API_KEY_3    = get_secret('TWELVE-DATA-API-KEY-3', required=False)
        # ── Optional APIs ──────────────────────────────────────
        self.NASA_EARTHDATA_TOKEN     = get_secret(
            'NASA-EARTHDATA-TOKEN', required=False
        )
        self.COPERNICUS_CLIENT_ID     = get_secret(
            'COPERNICUS-CLIENT-ID', required=False
        )
        self.COPERNICUS_CLIENT_SECRET = get_secret(
            'COPERNICUS-CLIENT-SECRET', required=False
        )

        # ── Azure Infrastructure ───────────────────────────────
        self.COSMOS_ENDPOINT          = get_secret('COSMOS-ENDPOINT')
        self.COSMOS_KEY               = get_secret('COSMOS-KEY')
        self.COGNITIVE_ENDPOINT       = get_secret(
            'COGNITIVE-SERVICES-ENDPOINT', required=False
        )
        self.COGNITIVE_KEY            = get_secret(
            'COGNITIVE-SERVICES-KEY', required=False
        )

        # ── Instruments ────────────────────────────────────────
        self.INSTRUMENTS = ['XAUUSD', 'GBPUSD', 'GBPJPY']

        # Twelve Data symbol map
        self.TD_SYMBOLS = {
            'XAUUSD': 'XAU/USD',
            'GBPUSD': 'GBP/USD',
            'GBPJPY': 'GBP/JPY',
        }

        # Finnhub symbol map
        self.FH_SYMBOLS = {
            'XAUUSD': 'DERIV:frxXAUUSD',
            'GBPUSD': 'DERIV:frxGBPUSD',
            'GBPJPY': 'DERIV:frxGBPJPY',
        }

        # yfinance symbol map (for 10yr history)
        self.YF_SYMBOLS = {
            'XAUUSD': 'GC=F',
            'GBPUSD': 'GBPUSD=X',
            'GBPJPY': 'GBPJPY=X',
        }

        # ── API Rate Management ────────────────────────────────
        # Twelve Data: 800 req/day free plan
        # WebSocket for live prices = 0 REST credits
        # Budget breakdown (per day):
        #   WebSocket prices:        0   (free, streaming)
        #   Startup indicator fetch: 12  (4 per pair x 3 pairs)
        #   /signal one pair:         4  (RSI+MACD+BBands+ATR)
        #   /signal all pairs:       12  (4 x 3)
        #   Auto-refresh every 30min: 4  (one pair per cycle)
        #   Daily total estimate:   ~200-400 of 800 budget
        self.TD_PRICE_INTERVAL_SEC     = 600   # REST fallback every 10min
        self.TD_INDICATOR_INTERVAL_SEC = 1800  # Auto-refresh every 30min
        self.TD_DAILY_BUDGET           = 800   # Total daily REST budget
        self.TD_SIGNAL_COST            = 4     # Credits per pair signal
        self.TD_LOW_BUDGET_THRESHOLD   = 100   # Warn when below this
        # Auto-refresh rotates pairs — only 1 pair per cycle
        # XAUUSD at :00, GBPUSD at :30, GBPJPY at :00 next hour
        self.TD_AUTO_REFRESH_PAIRS     = ['XAUUSD', 'GBPUSD', 'GBPJPY']

        # ── Signal Parameters ──────────────────────────────────
        self.MIN_RISK_REWARD          = 2.0   # minimum 1:2 R:R
        self.MAX_DAILY_LOSS_PCT       = 0.02  # 2% max daily loss
        self.MAX_TOTAL_DRAWDOWN_PCT   = 0.05  # 5% max total drawdown
        self.BASE_KELLY_FRACTION      = 0.01  # 1% base position size
        self.MAX_KELLY_FRACTION       = 0.03  # 3% max position size
        self.CFR_REGRET_THRESHOLD     = 0.70  # min CFR confidence
        self.AUTOENCODER_THRESHOLD    = 0.70  # regime change trigger
        self.DARKPOOL_Z_THRESHOLD     = 2.0   # dark pool anomaly level
        self.FRICTION_THRESHOLD       = 70    # geopolitical alert level
        self.HALFLIFE_MIN_PEARSON     = 0.40  # signal decay alert level

        # ── Signal Thresholds ──────────────────────────────────
        self.RSI_OVERSOLD             = 30    # RSI buy zone
        self.RSI_OVERBOUGHT           = 70    # RSI sell zone
        self.SIGNAL_MIN_CONFLUENCE    = 60.0  # min % signals agreeing
        self.SIGNAL_STRONG_BIAS       = 70.0  # strong directional bias

        # ── SL/TP Defaults (in pips) ───────────────────────────
        self.SL_PIPS = {
            'XAUUSD': 150,   # Gold: wider SL
            'GBPUSD': 30,    # GBP/USD: tighter
            'GBPJPY': 50,    # GBP/JPY: medium
        }
        self.TP_MULTIPLIER            = 2.0   # TP = SL × 2 (1:2 R:R)

        # ── Challenge Mode Defaults ────────────────────────────
        self.CHALLENGE_ACTIVE         = False
        self.CHALLENGE_CAPITAL        = 0.0
        self.CHALLENGE_TARGET_PCT     = 0.0
        self.CHALLENGE_DAYS           = 0
        self.CHALLENGE_START_DATE     = None

        logger.info('Configuration loaded successfully.')
        logger.info(f'Instruments: {self.INSTRUMENTS}')
        logger.info(f'Max daily loss: {self.MAX_DAILY_LOSS_PCT*100}%')
        logger.info(f'Max drawdown: {self.MAX_TOTAL_DRAWDOWN_PCT*100}%')


# ── SINGLETON INSTANCE ─────────────────────────────────────────
try:
    config = Config()
except Exception as e:
    logger.critical(f'FATAL: Configuration failed to load. {e}')
    raise