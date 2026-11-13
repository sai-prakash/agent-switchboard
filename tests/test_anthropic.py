import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import intelligence
import server
from server import create_app
from switchboard import Store


ANTHROPIC_REPLY = {
    'id': 'msg_1',
    'type': 'message',
    'role': 'assistant',
    'model': 'claude-sonnet-4-6',
    'content': [{'type': 'text', 'text': 'hello'}],
    'stop_reason': 'end_turn',
    'usage': {'input_tokens': 4, 'output_tokens': 1},
}


class AnthropicProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_anthropic_uses_saved_key_and_ignores_gateway_env(self):
        env = {
            'ANTHROPIC_BASE_URL': 'http://127.0.0.1:18765',
            'ANTHROPIC_API_KEY': 'env-must-not-win',
            'ANTHROPIC_AUTH_TOKEN': 'gateway-token',
        }
        with patch.dict(os.environ, env, clear=False), patch('server.litellm.anthropic.messages.acreate', new=AsyncMock(return_value=ANTHROPIC_REPLY)) as call:
            await server.call_model(
                {'model': 'anthropic/claude-sonnet-4-6', 'api_key': 'sk-ant-test'},
                {'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 8},
            )
        self.assertEqual(call.await_args.kwargs['model'], 'anthropic/claude-sonnet-4-6')
        self.assertEqual(call.await_args.kwargs['api_key'], 'sk-ant-test')
        self.assertEqual(call.await_args.kwargs['api_base'], 'https://api.anthropic.com')
        self.assertTrue(call.await_args.kwargs.get('drop_params') in (None, False))

    async def test_anthropic_call_rejects_switchboard_loop(self):
        with self.assertRaises(ValueError) as ctx:
            await server.call_model(
                {'model': 'anthropic/claude-sonnet-4-6', 'api_key': 'sk-ant-test', 'api_base': 'http://127.0.0.1:18765'},
                {'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 8},
            )
        self.assertNotIn('sk-ant-test', str(ctx.exception))


class AnthropicStoreTests(unittest.TestCase):
    def test_anthropic_provider_cannot_target_switchboard(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'config.json')
            with self.assertRaises(ValueError):
                store.save({'name': 'Anthropic', 'model': 'anthropic/claude-sonnet-4-6', 'api_base': 'http://127.0.0.1:18765', 'api_key': 'sk-ant-test'})

    def test_gateway_connection_is_included_for_other_tools(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'config.json')
            public = store.public()
            self.assertEqual(public['gateway']['base_url'], 'http://127.0.0.1:18765')
            self.assertEqual(public['gateway']['openai_base_url'], 'http://127.0.0.1:18765/v1')
            self.assertEqual(public['gateway']['token'], store.data['token'])
            self.assertNotIn('sk-ant', json.dumps(public))


class AnthropicCatalogTests(unittest.TestCase):
    def test_official_anthropic_is_a_likely_claude_route(self):
        self.assertEqual(intelligence.family('anthropic/claude-opus-4-6'), 'claude')
        self.assertEqual(intelligence.prior('anthropic/claude-opus-4-6'), 'likely')

    def test_anthropic_models_are_listed_without_a_custom_base(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'config.json')
            provider = store.save({'name': 'Anthropic Claude', 'model': 'anthropic/claude-sonnet-4-6', 'api_key': 'sk-ant-test'})
            client = TestClient(create_app(store, Path(d) / 'settings.json'), base_url='http://127.0.0.1:18765')
            response = client.post(f'/api/providers/{provider["id"]}/models', headers={'origin': 'http://127.0.0.1:18765', 'x-switchboard-ui': '1'})
        self.assertEqual(response.status_code, 200, response.text)
        models = response.json()['models']
        self.assertIn('claude-sonnet-4-6', models)
        self.assertIn('claude-opus-4-6', models)


class OpenAICompatibleGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'config.json')
        self.provider = self.store.save({'name': 'Anthropic Claude', 'model': 'anthropic/claude-sonnet-4-6', 'api_key': 'sk-ant-test'})
        self.store.activate(self.provider['id'])
        self.client = TestClient(create_app(self.store, Path(self.tmp.name) / 'settings.json'), base_url='http://127.0.0.1:18765')
        self.auth = {'authorization': 'Bearer ' + self.store.data['token']}

    def tearDown(self):
        self.tmp.cleanup()

    def test_models_endpoint_lists_switchboard_for_other_tools(self):
        response = self.client.get('/v1/models', headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        ids = [item['id'] for item in response.json()['data']]
        self.assertIn('switchboard', ids)
        self.assertIn('anthropic/claude-sonnet-4-6', ids)
        self.assertEqual(self.client.get('/v1/models').status_code, 401)

    def test_chat_completions_uses_the_active_route(self):
        with patch('server.call_model', new=AsyncMock(return_value=ANTHROPIC_REPLY)) as call:
            response = self.client.post(
                '/v1/chat/completions',
                headers=self.auth,
                json={'model': 'gpt-4o', 'messages': [{'role': 'system', 'content': 'Be brief.'}, {'role': 'user', 'content': 'hi'}], 'max_tokens': 16},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['object'], 'chat.completion')
        self.assertEqual(body['choices'][0]['message']['content'], 'hello')
        self.assertEqual(body['choices'][0]['finish_reason'], 'stop')
        outgoing = call.call_args.args[1]
        self.assertEqual(call.call_args.args[0]['model'], 'anthropic/claude-sonnet-4-6')
        self.assertEqual(outgoing['system'], 'Be brief.')
        self.assertEqual(outgoing['messages'][0]['content'], 'hi')
        self.assertEqual(outgoing['max_tokens'], 16)

    def test_chat_completions_stream_uses_openai_chunks(self):
        with patch('server.call_model', new=AsyncMock(return_value=ANTHROPIC_REPLY)):
            response = self.client.post(
                '/chat/completions',
                headers={'x-api-key': self.store.data['token']},
                json={'model': 'switchboard', 'messages': [{'role': 'user', 'content': 'hi'}], 'stream': True},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('chat.completion.chunk', response.text)
        self.assertIn('hello', response.text)
        self.assertIn('[DONE]', response.text)
