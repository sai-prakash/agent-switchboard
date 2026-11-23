import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from server import create_app
from switchboard import Store, connect_claude


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'config.json')
        self.app = create_app(self.store, Path(self.tmp.name) / 'settings.json')
        self.client = TestClient(self.app, base_url='http://127.0.0.1:18765')
        self.headers = {'origin': 'http://127.0.0.1:18765', 'x-switchboard-ui': '1'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_management_requires_same_origin_and_custom_header(self):
        r = self.client.post('/api/providers', json={'name':'a','model':'openai/a'}, headers={'origin':'https://evil.example'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get('/api/state', headers={'x-switchboard-ui':'1'}).status_code, 200)

    def test_restore_claude_default_endpoint_clears_gateway_settings(self):
        settings = Path(self.tmp.name) / 'settings.json'
        settings.write_text('{"env":{"ANTHROPIC_MODEL":"old"},"model":"opusplan"}')
        connect_claude(self.store, settings, 'http://127.0.0.1:18765')
        response = self.client.post('/api/claude/restore', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = json.loads(settings.read_text())
        self.assertEqual(data['model'], 'opusplan')
        self.assertNotIn('ANTHROPIC_BASE_URL', data.get('env', {}))
        self.assertNotIn('ANTHROPIC_MODEL', data.get('env', {}))
        self.assertFalse(self.client.get('/api/state', headers=self.headers).json()['claude_connected'])

    def test_gateway_requires_token_and_active_route(self):
        self.assertEqual(self.client.post('/v1/messages', json={}).status_code, 401)
        r = self.client.post('/v1/messages', json={}, headers={'x-api-key': self.store.data['token']})
        self.assertEqual(r.status_code, 503)
        r = self.client.post('/v1/messages', json={}, headers={'x-api-key':'another-key', 'authorization':'Bearer ' + self.store.data['token']})
        self.assertEqual(r.status_code, 503)

    def test_gateway_routes_to_selected_model(self):
        p = self.store.save({'name':'A','model':'openai/a','api_key':'example'})
        self.store.activate(p['id'])
        result = {'id':'msg_1','type':'message','role':'assistant','model':'a','content':[{'type':'text','text':'hello'}], 'stop_reason':'end_turn','usage':{'input_tokens':1,'output_tokens':1}}
        with patch('server.call_model', new=AsyncMock(return_value=result)) as call:
            r = self.client.post('/v1/messages', json={'model':'switchboard','messages':[{'role':'user','content':'hi'}], 'max_tokens':10}, headers={'x-api-key':self.store.data['token']})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['content'][0]['text'], 'hello')
        self.assertEqual(call.call_args.args[0]['model'], 'openai/a')

    def test_antigravity_streams_buffered_response_as_events(self):
        p = self.store.save({'name':'Antigravity','model':'antigravity/test'})
        self.store.activate(p['id'])
        result = {'id':'msg_1','type':'message','role':'assistant','model':'test','content':[{'type':'text','text':'hello'},{'type':'tool_use','id':'tool_1','name':'ping','input':{'x':1}}], 'stop_reason':'tool_use','usage':{'input_tokens':3,'output_tokens':4}}
        with patch('server.call_model', new=AsyncMock(return_value=result)) as call:
            response = self.client.post('/v1/messages', json={'model':'switchboard','messages':[{'role':'user','content':'hi'}], 'max_tokens':10, 'stream':True}, headers={'x-api-key':self.store.data['token']})
        self.assertFalse(call.call_args.args[1]['stream'])
        self.assertIn('event: message_start', response.text)
        self.assertIn('"type":"input_json_delta"', response.text)
        self.assertIn('event: message_stop', response.text)

    def test_subscription_models_are_listed_without_a_base_url(self):
        gravity = self.store.save({'name':'Antigravity','model':'antigravity/claude-sonnet-4-6'})
        response = self.client.post(f'/api/providers/{gravity["id"]}/models', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        models = response.json()['models']
        self.assertIn('claude-sonnet-4-6', models)
        self.assertIn('gemini-3.8-flash-low', models)
        chatgpt = self.store.save({'name':'ChatGPT','model':'chatgpt/gpt-6.1-sol'})
        response = self.client.post(f'/api/providers/{chatgpt["id"]}/models', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(any(model.startswith('gpt-') for model in response.json()['models']))
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'GROK_HOME': d}):
            (Path(d) / 'models_cache.json').write_text(json.dumps({'models': {'grok-4.6': {}, 'grok-4.7': {}}}))
            grok = self.store.save({'name': 'Grok', 'model': 'grok/grok-4.6'})
            response = self.client.post(f'/api/providers/{grok["id"]}/models', headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            models = response.json()['models']
            self.assertIn('grok-4.6', models)
            self.assertIn('grok-4.7', models)

