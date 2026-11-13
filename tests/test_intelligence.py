import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import intelligence
import paths
from server import create_app
from switchboard import Store


class PathTests(unittest.TestCase):
    def test_data_dir_honors_switchboard_home(self):
        with patch.dict(os.environ, {'AGENT_SWITCHBOARD_HOME': '/tmp/switchboard-home'}):
            self.assertEqual(paths.data_dir(), Path('/tmp/switchboard-home'))


class IntelligenceTests(unittest.TestCase):
    def test_errors_are_classified_for_routing(self):
        self.assertEqual(intelligence.classify('{"code":"model_not_supported"}'), 'unsupported')
        self.assertEqual(intelligence.classify('RESOURCE_EXHAUSTED'), 'quota')
        self.assertEqual(intelligence.classify('usage_limit_reached'), 'quota')
        self.assertEqual(intelligence.classify('RateLimitError 429'), 'rate_limited')
        self.assertEqual(intelligence.classify('Could not read the Grok CLI session'), 'auth')

    def test_copilot_claude_is_treated_as_risky_until_proven(self):
        self.assertEqual(intelligence.family('github_copilot/claude-opus-5.5'), 'claude')
        self.assertEqual(intelligence.prior('github_copilot/claude-opus-5.5'), 'risky')
        self.assertEqual(intelligence.prior('github_copilot/gpt-4o'), 'likely')
        self.assertEqual(intelligence.prior('grok/grok-4.6'), 'likely')
        self.assertEqual(intelligence.prior('antigravity/claude-sonnet-4-6'), 'quota_sensitive')

    def test_recommend_skips_unsupported_and_prefers_working_grok_over_risky_copilot(self):
        routes = [
            {'id': 'copilot', 'name': 'Copilot', 'model': 'github_copilot/claude-opus-5.5', 'status': 'unsupported', 'prior': 'risky', 'family': 'claude', 'context_window': 1000000},
            {'id': 'grok', 'name': 'Grok Build', 'model': 'grok/grok-4.6', 'status': 'ready', 'prior': 'likely', 'family': 'grok', 'context_window': 256000},
            {'id': 'gravity', 'name': 'Antigravity', 'model': 'antigravity/claude-sonnet-4-6', 'status': 'quota', 'prior': 'quota_sensitive', 'family': 'claude', 'context_window': 200000},
        ]
        choice = intelligence.recommend(routes)
        self.assertEqual(choice['id'], 'grok')
        self.assertIn('unsupported', intelligence.explain(routes, choice).lower())

    def test_candidates_skip_blocked_routes(self):
        routes = [
            {'id': 'a', 'status': 'unsupported', 'score': 90, 'prior': 'risky', 'family': 'claude', 'model': 'github_copilot/claude-opus-5.5'},
            {'id': 'b', 'status': 'ready', 'score': 50, 'prior': 'likely', 'family': 'grok', 'model': 'grok/grok-4.6'},
            {'id': 'c', 'status': 'quota', 'score': 80, 'prior': 'quota_sensitive', 'family': 'claude', 'model': 'antigravity/claude-sonnet-4-6'},
        ]
        self.assertEqual([item['id'] for item in intelligence.candidates(routes)], ['b'])

    def test_scanned_error_reason_includes_last_error(self):
        route = intelligence.describe(
            {'id': 'c', 'name': 'Codex', 'model': 'chatgpt/gpt-6.1-sol', 'enabled': True},
            {'ok': False, 'error': "chatgpt does not support parameters: ['stop']", 'status': 'error'},
        )
        self.assertEqual(route['status'], 'error')
        self.assertEqual(route['error'], "chatgpt does not support parameters: ['stop']")
        self.assertIn('stop', route['reason'].lower())
        self.assertNotIn('usually listed', route['reason'].lower())

    def test_untested_quota_sensitive_route_keeps_catalog_copy(self):
        route = intelligence.describe({'id': 'g', 'name': 'Antigravity', 'model': 'antigravity/claude-sonnet-4-6', 'enabled': True})
        self.assertEqual(route['status'], 'untested')
        self.assertIn('quota is only known after a live scan', route['reason'])

    def test_snapshot_exposes_failover_catalog_and_active(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'config.json')
        grok = store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        store.save({'name': 'Antigravity', 'model': 'antigravity/claude-sonnet-4-6'})
        store.activate(grok['id'])
        store.record_probe(grok['id'], True, usage={'input_tokens': 8, 'output_tokens': 1})
        snap = intelligence.snapshot(
            store,
            {'connected': True, 'expires_at': '2099-01-01T00:00:00Z', 'models': [{'id': 'grok-4.6', 'context_window': 256000, 'compactions_remaining': 1}]},
            {'antigravity_models': ['claude-sonnet-4-6', 'claude-opus-4-6-thinking']},
        )
        self.assertEqual(snap['active'], grok['id'])
        self.assertEqual(snap['failover'][0]['id'], grok['id'])
        self.assertEqual(snap['sessions']['grok']['catalog_count'], 1)
        self.assertEqual(snap['sessions']['antigravity']['catalog_count'], 2)
        self.assertIn('platform', snap['machine'])
        self.assertIn('data_dir', snap['machine'])


class IntelligenceAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'config.json')
        self.app = create_app(self.store, Path(self.tmp.name) / 'settings.json')
        self.client = TestClient(self.app, base_url='http://127.0.0.1:18765')
        self.headers = {'origin': 'http://127.0.0.1:18765', 'x-switchboard-ui': '1'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_intelligence_snapshot_ranks_saved_providers(self):
        copilot = self.store.save({'name': 'Copilot', 'model': 'github_copilot/claude-opus-5.5'})
        grok = self.store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        self.store.record_probe(copilot['id'], False, 'model_not_supported')
        self.store.record_probe(grok['id'], True, usage={'input_tokens': 85, 'output_tokens': 1})
        response = self.client.get('/api/intelligence', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data['smart_route'])
        self.assertEqual(data['recommended']['id'], grok['id'])
        statuses = {item['id']: item['status'] for item in data['routes']}
        self.assertEqual(statuses[copilot['id']], 'unsupported')
        self.assertEqual(statuses[grok['id']], 'ready')
        grok_row = next(item for item in data['routes'] if item['id'] == grok['id'])
        self.assertEqual(grok_row['usage']['output_tokens'], 1)

    def test_smart_route_falls_back_when_active_model_is_unsupported(self):
        copilot = self.store.save({'name': 'Copilot', 'model': 'github_copilot/claude-opus-5.5'})
        grok = self.store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        self.store.activate(copilot['id'])
        self.store.set_smart_route(True)
        self.store.record_probe(copilot['id'], False, 'The requested model is not supported.')
        self.store.record_probe(grok['id'], True)
        result = {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'grok-4.6', 'content': [{'type': 'text', 'text': 'hello'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        with patch('server.call_model', new=AsyncMock(return_value=result)) as call:
            response = self.client.post('/v1/messages', json={'model': 'switchboard', 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 10}, headers={'x-api-key': self.store.data['token']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(call.call_args.args[0]['id'], grok['id'])

    def test_scan_records_live_access_without_changing_claude_connection(self):
        grok = self.store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        result = {'content': [{'type': 'text', 'text': 'OK'}], 'usage': {'input_tokens': 10, 'output_tokens': 1}}
        with patch('server.call_model', new=AsyncMock(return_value=result)):
            response = self.client.post('/api/intelligence/scan', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['recommended']['id'], grok['id'])
        self.assertTrue(self.store.data['probes'][grok['id']]['ok'])
        self.assertIsNone(self.store.data['claude_backup'])
        self.assertEqual(data['failover'][0]['id'], grok['id'])
        self.assertEqual(data['active'], grok['id'])


class CallModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_chatgpt_drops_params_claude_code_sends(self):
        import server
        result = {'content': [{'type': 'text', 'text': 'OK'}], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
        with patch('server.litellm.anthropic.messages.acreate', new=AsyncMock(return_value=result)) as call:
            await server.call_model({'model': 'chatgpt/gpt-6.1-sol', 'api_key': 'k'}, {'messages': [{'role': 'user', 'content': 'OK'}], 'max_tokens': 8, 'stop': ['\n']})
        self.assertTrue(call.await_args.kwargs.get('drop_params'))
        self.assertEqual(call.await_args.kwargs.get('model'), 'chatgpt/gpt-6.1-sol')


class ServicePathTests(unittest.TestCase):
    def test_venv_python_uses_scripts_on_windows(self):
        import service
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            script = root / '.venv' / 'Scripts' / 'python.exe'
            script.parent.mkdir(parents=True)
            script.write_text('')
            with patch.object(service, 'ROOT', root), patch.object(service.os, 'name', 'nt'):
                self.assertEqual(service.venv_python(), str(script))
