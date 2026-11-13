import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_agents import AgentManager, parse_device_line


class LocalAgentTests(unittest.TestCase):
    def test_device_codes_are_extracted_without_exposing_other_output(self):
        self.assertEqual(parse_device_line('chatgpt', '2) Enter code: ABCD-EFGH'), 'ABCD-EFGH')
        self.assertEqual(parse_device_line('copilot', 'Please visit https://github.com/login/device and enter code 1234-5678 to authenticate.'), '1234-5678')
        self.assertIsNone(parse_device_line('chatgpt', 'access_token=private'))

    def test_catalog_distinguishes_installed_from_connected(self):
        with tempfile.TemporaryDirectory() as d:
            manager = AgentManager(Path(d))
            with patch('local_agents.shutil.which', return_value='/bin/example'), patch('local_agents.subprocess.run') as run, patch.dict(os.environ, {'GROK_HOME': d}):
                run.return_value.returncode = 1
                result = manager.list_agents()
            codex = next(x for x in result if x['id'] == 'chatgpt')
            self.assertTrue(codex['installed'])
            self.assertFalse(codex['connected'])
            gravity = next(x for x in result if x['id'] == 'antigravity')
            self.assertEqual(gravity['prefix'], 'antigravity/')
            self.assertFalse(gravity['connected'])
            self.assertEqual(gravity['suggested'], 'antigravity/claude-sonnet-4-6')
            grok = next(x for x in result if x['id'] == 'grok')
            self.assertEqual(grok['prefix'], 'grok/')
            self.assertEqual(grok['suggested'], 'grok/grok-4.6')
            self.assertFalse(grok['connected'])

    def test_antigravity_is_connected_when_keychain_item_exists(self):
        with tempfile.TemporaryDirectory() as d:
            manager = AgentManager(Path(d))
            with patch('local_agents.shutil.which', return_value='/bin/agy'), patch('local_agents.subprocess.run') as run:
                run.return_value.returncode = 0
                gravity = next(x for x in manager.list_agents() if x['id'] == 'antigravity')
            self.assertTrue(gravity['connected'])
            run.assert_called()

    def test_grok_is_connected_when_cli_session_exists(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / 'grok'
            home.mkdir()
            (home / 'auth.json').write_text(json.dumps({
                'https://auth.x.ai::example': {'key': 'mock-session', 'expires_at': '2099-01-01T00:00:00Z'}
            }))
            manager = AgentManager(Path(d) / 'accounts')
            with patch('local_agents.shutil.which', return_value='/bin/grok'), patch.dict(os.environ, {'GROK_HOME': str(home)}):
                grok = next(x for x in manager.list_agents() if x['id'] == 'grok')
            self.assertTrue(grok['connected'])
            self.assertEqual(grok['prefix'], 'grok/')

    def test_duplicate_login_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            manager = AgentManager(Path(d))
            manager.jobs['chatgpt'] = {'status':'pending'}
            with self.assertRaises(ValueError):
                manager.start_login('chatgpt')

