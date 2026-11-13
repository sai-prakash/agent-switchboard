import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import grok_bridge


def write_session(home, key='mock-session-token', refresh='mock-refresh-token', expires='2099-01-01T00:00:00Z', models=None):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    (home / 'auth.json').write_text(json.dumps({
        'https://auth.x.ai::example': {
            'key': key,
            'refresh_token': refresh,
            'expires_at': expires,
        }
    }))
    if models is not None:
        (home / 'models_cache.json').write_text(json.dumps({
            'models': {name: {'info': {'id': name}} for name in models}
        }))
    return home


class GrokBridgeTests(unittest.TestCase):
    def test_nested_cli_session_is_used_without_a_fixed_auth_key(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'GROK_HOME': d}):
            write_session(d, key='session-from-oidc-record')
            self.assertTrue(grok_bridge.connected())
            self.assertEqual(grok_bridge.api_key(), 'session-from-oidc-record')

    def test_expired_cli_session_is_not_connected(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'GROK_HOME': d}):
            write_session(d, expires='2000-01-01T00:00:00Z')
            self.assertFalse(grok_bridge.connected())
            with self.assertRaises(RuntimeError) as ctx:
                grok_bridge.api_key()
            message = str(ctx.exception)
            self.assertNotIn('mock-session-token', message)
            self.assertNotIn('mock-refresh-token', message)

    def test_cli_proxy_headers_identify_grok_cli_without_embedding_the_token(self):
        with patch.object(grok_bridge, 'client_version', return_value='1.0.77'):
            headers = grok_bridge.extra_headers('grok-4.6')
        self.assertEqual(headers['X-XAI-Token-Auth'], 'xai-grok-cli')
        self.assertEqual(headers['x-grok-model-override'], 'grok-4.6')
        self.assertEqual(headers['x-grok-client-version'], '1.0.77')
        self.assertEqual(headers['User-Agent'], 'grok-cli/1.0.77')
        self.assertNotIn('Authorization', headers)

    def test_proxy_base_honors_cli_override_and_defaults_to_official_proxy(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('GROK_CLI_CHAT_PROXY_BASE_URL', None)
            self.assertEqual(grok_bridge.api_base(), 'https://cli-chat-proxy.grok.com/v1')
        with patch.dict(os.environ, {'GROK_CLI_CHAT_PROXY_BASE_URL': 'http://127.0.0.1:9/v1/'}):
            self.assertEqual(grok_bridge.api_base(), 'http://127.0.0.1:9/v1')

    def test_models_are_listed_from_the_cli_cache(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'GROK_HOME': d}):
            write_session(d, models=['grok-4.6', 'grok-4.7'])
            self.assertEqual(grok_bridge.listed_models(), ['grok-4.6', 'grok-4.7'])
            info = grok_bridge.session_info()
            self.assertTrue(info['connected'])
            self.assertNotIn('key', info)
            self.assertNotIn('refresh_token', json.dumps(info))
