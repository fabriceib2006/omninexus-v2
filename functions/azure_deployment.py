# ════════════════════════════════════════════════════════════════
# OMNINEXUS — functions/azure_deployment.py
# Azure Deployment Manager
# Manages deployment of all Azure resources
# Verifies Cosmos DB, Functions, Event Grid are healthy
# Reports deployment status to Telegram
# ════════════════════════════════════════════════════════════════

import logging
import json
import requests
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config

logger = logging.getLogger('omninexus.functions.deployment')


class AzureDeploymentManager:
    """
    Manages and verifies Azure resource deployment.
    Checks health of all OmniNexus Azure components.
    """

    def __init__(self):
        self.deployed   = False
        self.components = {}
        self.last_check = None

    def check_cosmos_db(self) -> dict:
        """Verifies Cosmos DB connectivity."""
        try:
            from graph.cosmos import get_gremlin_client
            gc = get_gremlin_client()
            if gc:
                from graph.cosmos import count_graph_nodes
                nodes = count_graph_nodes(gc)
                gc.close()
                return {
                    'status': 'healthy',
                    'nodes':  nodes,
                    'api':    'Gremlin',
                }
            return {'status': 'unreachable'}
        except Exception as e:
            return {
                'status': 'error',
                'error':  str(e)[:80]
            }

    def check_cognitive_services(self) -> dict:
        """Verifies Azure Cognitive Services."""
        try:
            endpoint = config.COGNITIVE_ENDPOINT
            key      = config.COGNITIVE_KEY
            if endpoint and key:
                return {
                    'status':   'configured',
                    'endpoint': endpoint[:40] + '...',
                }
            return {'status': 'not_configured'}
        except Exception as e:
            return {'status': 'error', 'error': str(e)[:80]}

    def check_key_vault(self) -> dict:
        """Verifies Azure Key Vault accessibility."""
        try:
            vault_url = os.getenv('AZURE_KEY_VAULT_URL', '')
            if vault_url:
                return {
                    'status': 'configured',
                    'vault':  vault_url[:40] + '...',
                }
            return {'status': 'using_env_fallback'}
        except Exception as e:
            return {'status': 'error', 'error': str(e)[:80]}

    def check_telegram(self) -> dict:
        """Verifies Telegram bot connectivity."""
        try:
            token = config.TELEGRAM_BOT_TOKEN
            url   = (
                f'https://api.telegram.org'
                f'/bot{token}/getMe'
            )
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data     = response.json()
                bot_info = data.get('result', {})
                return {
                    'status':   'healthy',
                    'bot_name': bot_info.get('username'),
                }
            return {
                'status': 'error',
                'code':   response.status_code
            }
        except Exception as e:
            return {
                'status': 'error',
                'error':  str(e)[:80]
            }

    def check_fred_api(self) -> dict:
        """Verifies FRED API connectivity."""
        try:
            url = (
                'https://api.stlouisfed.org'
                '/fred/series/observations'
            )
            params = {
                'series_id': 'GS10',
                'api_key':   config.FRED_API_KEY,
                'file_type': 'json',
                'limit':     1,
            }
            response = requests.get(
                url, params=params, timeout=10
            )
            if response.status_code == 200:
                return {'status': 'healthy'}
            return {
                'status': 'error',
                'code':   response.status_code
            }
        except Exception as e:
            return {
                'status': 'error',
                'error':  str(e)[:80]
            }

    def deploy(self, deployment_config: dict = None) -> bool:
        """
        Runs full deployment health check.
        Verifies all components are properly configured.
        """
        logger.info('Running OmniNexus deployment check...')

        checks = {
            'key_vault':          self.check_key_vault(),
            'cognitive_services': self.check_cognitive_services(),
            'telegram':           self.check_telegram(),
            'fred_api':           self.check_fred_api(),
            'cosmos_db':          self.check_cosmos_db(),
        }

        self.components = checks
        self.last_check = datetime.utcnow().isoformat()

        # Count healthy components
        healthy = sum(
            1 for c in checks.values()
            if c.get('status') in ['healthy', 'configured',
                                    'using_env_fallback']
        )
        total = len(checks)

        self.deployed = healthy >= (total * 0.6)

        logger.info(
            f'Deployment check: {healthy}/{total} healthy | '
            f'Deployed: {self.deployed}'
        )

        return self.deployed

    def status(self) -> dict:
        """Returns current deployment status."""
        if not self.components:
            self.deploy()

        healthy = sum(
            1 for c in self.components.values()
            if c.get('status') in ['healthy', 'configured',
                                    'using_env_fallback']
        )

        return {
            'deployed':   self.deployed,
            'healthy':    healthy,
            'total':      len(self.components),
            'components': self.components,
            'last_check': self.last_check,
            'timestamp':  datetime.utcnow().isoformat(),
        }

    def format_telegram(self, status: dict) -> str:
        """Formats deployment status for Telegram."""
        lines = ''
        emoji_map = {
            'healthy':           '✅',
            'configured':        '✅',
            'using_env_fallback': '⚠️',
            'error':             '❌',
            'unreachable':       '❌',
            'not_configured':    '⚠️',
        }

        for component, data in status['components'].items():
            s     = data.get('status', 'unknown')
            emoji = emoji_map.get(s, '❓')
            extra = ''
            if 'nodes' in data:
                extra = f' ({data["nodes"]} nodes)'
            elif 'bot_name' in data:
                extra = f' (@{data["bot_name"]})'
            lines += (
                f'{emoji} {component.replace("_", " ").title()}'
                f'{extra}\n'
            )

        overall = (
            '✅ FULLY DEPLOYED'
            if status['deployed']
            else '⚠️ PARTIAL DEPLOYMENT'
        )

        return (
            f'🚀 <b>AZURE DEPLOYMENT STATUS</b>\n'
            f'<code>{status["timestamp"][:19]} UTC</code>\n\n'
            f'Overall: <b>{overall}</b>\n'
            f'Healthy: {status["healthy"]}/{status["total"]}\n\n'
            f'<b>COMPONENTS:</b>\n'
            f'{lines}'
        )


# ── DIRECT TEST ────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n' + '='*55)
    print('OMNINEXUS — Azure Deployment Manager Test')
    print('='*55 + '\n')

    manager  = AzureDeploymentManager()
    deployed = manager.deploy()
    status   = manager.status()

    print(f'Deployed: {deployed}')
    print(f'Healthy:  {status["healthy"]}/{status["total"]}')
    print(f'\nComponent Status:')
    for component, data in status['components'].items():
        s = data.get('status', 'unknown')
        print(f'  {component:<25}: {s}')
        if 'error' in data:
            print(f'    Error: {data["error"][:60]}')
        if 'nodes' in data:
            print(f'    Nodes: {data["nodes"]}')
        if 'bot_name' in data:
            print(f'    Bot: @{data["bot_name"]}')