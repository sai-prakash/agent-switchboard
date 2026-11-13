import os
import tempfile
import unittest
from pathlib import Path

from switchboard import Store, connect_claude, disconnect_claude


class StoreTests(unittest.TestCase):
    def test_secrets_are_private_and_redacted(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'config.json')
            provider = store.save({'name': 'Local', 'model': 'openai/test', 'api_base': 'http://127.0.0.1:1234/v1', 'api_key': 'secret'})
            self.assertNotIn('secret', str(store.public()))
            self.assertTrue(provider['has_key'])
            self.assertEqual(os.stat(store.path).st_mode & 0o777, 0o600)

    def test_disabled_provider_cannot_be_active(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'config.json')
            provider = store.save({'name': 'Test', 'model': 'openai/test', 'enabled': False})
            with self.assertRaises(ValueError):
                store.activate(provider['id'])

    def test_claude_settings_restore_and_detect_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'config.json')
            settings = Path(d) / 'settings.json'
            settings.write_text('{"env":{"ANTHROPIC_MODEL":"old"},"other":true}')
            connect_claude(store, settings, 'http://127.0.0.1:18765')
            self.assertIn('switchboard', settings.read_text())
            disconnect_claude(store, settings)
            self.assertEqual(__import__('json').loads(settings.read_text()), {'env': {'ANTHROPIC_MODEL': 'old'}, 'other': True})
            connect_claude(store, settings, 'http://127.0.0.1:18765')
            data = __import__('json').loads(settings.read_text())
            data['env']['ANTHROPIC_MODEL'] = 'changed-elsewhere'
            settings.write_text(__import__('json').dumps(data))
            with self.assertRaises(ValueError):
                disconnect_claude(store, settings)
            self.assertEqual(__import__('json').loads(settings.read_text())['env']['ANTHROPIC_MODEL'], 'changed-elsewhere')

    def test_grok_subscription_cannot_override_its_endpoint(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'config.json')
            with self.assertRaises(ValueError):
                store.save({'name': 'Grok', 'model': 'grok/grok-4.6', 'api_base': 'https://example.com/v1'})

