import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


SAFEGUARDS = [{'type': 'dangerous_tool_use', 'permission_mode': 'auto'}]
SAFEGUARD_RESULTS = [{
    'type': 'dangerous_tool_use',
    'status': {
        'type': 'available',
        'tool_uses': {'toolu_01abc': {'type': 'evaluated', 'outcome': 'not_flagged'}},
    },
}]
CLASSIFIER_BETA = 'context-1m-2025-08-07,dangerous-tool-use-2026-09-03'


class FakeAnthropic(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.server.last_raw = raw
        self.server.last_path = self.path
        self.server.last_headers = {key.lower(): value for key, value in self.headers.items()}
        self.server.last_body = json.loads(raw) if raw else {}
        status = getattr(self.server, 'status', 200)
        if status >= 400:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('x-should-retry', 'true')
            if status == 429:
                self.send_header('retry-after', '2')
            self.end_headers()
            message = 'rate_limit 429' if status == 429 else 'upstream failed'
            self.wfile.write(json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': message}}).encode())
            return
        if self.server.last_body.get('stream'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('x-should-retry', 'false')
            self.send_header('anthropic-ratelimit-unified-status', 'allowed')
            self.end_headers()
            events = [
                'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-6"}}\n\n',
                'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_01abc","name":"Bash","input":{}}}\n\n',
                'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"command\\":\\"ls\\"}"}}\n\n',
                'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
                'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use","safeguard_results":' + json.dumps(SAFEGUARD_RESULTS, separators=(',', ':')) + '},"usage":{"output_tokens":12}}\n\n',
                'event: message_stop\ndata: {"type":"message_stop"}\n\n',
            ]
            for event in events:
                self.wfile.write(event.encode())
            return
        body = {
            'id': 'msg_1',
            'type': 'message',
            'role': 'assistant',
            'model': 'claude-sonnet-4-6',
            'content': [{'type': 'tool_use', 'id': 'toolu_01abc', 'name': 'Bash', 'input': {'command': 'ls'}}],
            'stop_reason': 'tool_use',
            'usage': {'input_tokens': 10, 'output_tokens': 12},
            'safeguard_results': SAFEGUARD_RESULTS,
        }
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('x-should-retry', 'false')
        self.send_header('retry-after', '2')
        self.send_header('anthropic-ratelimit-unified-status', 'allowed')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


class AutoModeClassifierTests(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), FakeAnthropic)
        self.thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.thread.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'config.json')
        self.provider = self.store.save({
            'name': 'Anthropic Claude',
            'model': 'anthropic/claude-sonnet-4-6',
            'api_key': 'sk-ant-test',
            'api_base': f'http://127.0.0.1:{self.upstream.server_port}',
        })
        self.store.activate(self.provider['id'])
        self.client = TestClient(create_app(self.store, Path(self.tmp.name) / 'settings.json'), base_url='http://127.0.0.1:18765')
        self.auth = {'x-api-key': self.store.data['token']}

    def tearDown(self):
        self.upstream.shutdown()
        self.upstream.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def classifier_payload(self, stream=False):
        payload = {
            'model': 'switchboard',
            'max_tokens': 32,
            'messages': [{'role': 'user', 'content': 'list files'}],
            'tools': [{'name': 'Bash', 'description': 'Run a shell command', 'input_schema': {'type': 'object', 'properties': {'command': {'type': 'string'}}}}],
            'safeguards': SAFEGUARDS,
        }
        if stream:
            payload['stream'] = True
        return payload

    def test_anthropic_messages_forwards_safeguards_and_returns_safeguard_results(self):
        response = self.client.post(
            '/v1/messages?beta=true',
            headers={**self.auth, 'anthropic-beta': CLASSIFIER_BETA, 'anthropic-version': '2023-06-01', 'authorization': 'Bearer ' + self.store.data['token']},
            json=self.classifier_payload(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(getattr(self.upstream, 'last_path', '').startswith('/v1/messages'), getattr(self.upstream, 'last_path', None))
        self.assertIn('beta=true', getattr(self.upstream, 'last_path', ''))
        self.assertEqual(self.upstream.last_headers.get('anthropic-beta'), CLASSIFIER_BETA)
        self.assertEqual(self.upstream.last_headers.get('anthropic-version'), '2023-06-01')
        self.assertEqual(self.upstream.last_headers.get('x-api-key'), 'sk-ant-test')
        self.assertNotEqual(self.upstream.last_headers.get('authorization'), 'Bearer ' + self.store.data['token'])
        self.assertEqual(self.upstream.last_body.get('safeguards'), SAFEGUARDS)
        self.assertEqual(self.upstream.last_body.get('model'), 'claude-sonnet-4-6')
        body = response.json()
        self.assertEqual(body['safeguard_results'], SAFEGUARD_RESULTS)
        self.assertEqual(body['content'][0]['id'], 'toolu_01abc')
        self.assertEqual(response.headers.get('x-should-retry'), 'false')
        self.assertEqual(response.headers.get('retry-after'), '2')
        self.assertEqual(response.headers.get('anthropic-ratelimit-unified-status'), 'allowed')

    def test_anthropic_stream_keeps_safeguard_results_and_tool_use_ids(self):
        response = self.client.post(
            '/v1/messages?beta=true',
            headers={**self.auth, 'anthropic-beta': CLASSIFIER_BETA, 'anthropic-version': '2023-06-01'},
            json=self.classifier_payload(stream=True),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('text/event-stream', response.headers.get('content-type', ''))
        self.assertEqual(self.upstream.last_body.get('safeguards'), SAFEGUARDS)
        self.assertEqual(self.upstream.last_headers.get('anthropic-beta'), CLASSIFIER_BETA)
        self.assertIn('"id":"toolu_01abc"', response.text)
        self.assertIn('event: message_stop', response.text)
        delta_events = [block for block in response.text.split('\n\n') if block.startswith('event: message_delta')]
        self.assertTrue(delta_events)
        last_delta = json.loads(delta_events[-1].split('data: ', 1)[1])
        self.assertEqual(last_delta['delta']['safeguard_results'], SAFEGUARD_RESULTS)
        self.assertEqual(response.headers.get('x-should-retry'), 'false')

    def test_safeguards_prefer_official_anthropic_over_ready_grok(self):
        grok = self.store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        self.store.record_probe(grok['id'], True, usage={'input_tokens': 8, 'output_tokens': 1})
        grok_reply = {
            'id': 'msg_g',
            'type': 'message',
            'role': 'assistant',
            'model': 'grok-4.6',
            'content': [{'type': 'text', 'text': 'from grok'}],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }
        with patch('server.call_model', new=AsyncMock(return_value=grok_reply)) as call:
            response = self.client.post(
                '/v1/messages?beta=true',
                headers={**self.auth, 'anthropic-beta': CLASSIFIER_BETA},
                json=self.classifier_payload(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get('safeguard_results'), SAFEGUARD_RESULTS)
        self.assertEqual(call.await_count, 0)
        self.assertEqual(self.upstream.last_body.get('safeguards'), SAFEGUARDS)

    def test_without_safeguards_ready_grok_still_wins(self):
        grok = self.store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        self.store.record_probe(grok['id'], True, usage={'input_tokens': 8, 'output_tokens': 1})
        grok_reply = {
            'id': 'msg_g',
            'type': 'message',
            'role': 'assistant',
            'model': 'grok-4.6',
            'content': [{'type': 'text', 'text': 'from grok'}],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }
        payload = self.classifier_payload()
        del payload['safeguards']
        with patch('server.call_model', new=AsyncMock(return_value=grok_reply)) as call:
            response = self.client.post('/v1/messages', headers=self.auth, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['content'][0]['text'], 'from grok')
        self.assertEqual(call.await_count, 1)
        self.assertNotIn('safeguard_results', response.json())

    def test_safeguards_promote_anthropic_when_smart_route_is_off(self):
        grok = self.store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        self.store.activate(grok['id'])
        self.store.set_smart_route(False)
        grok_reply = {
            'id': 'msg_g',
            'type': 'message',
            'role': 'assistant',
            'model': 'grok-4.6',
            'content': [{'type': 'text', 'text': 'from grok'}],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }
        with patch('server.call_model', new=AsyncMock(return_value=grok_reply)) as call:
            response = self.client.post(
                '/v1/messages?beta=true',
                headers={**self.auth, 'anthropic-beta': CLASSIFIER_BETA},
                json=self.classifier_payload(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json().get('safeguard_results'), SAFEGUARD_RESULTS)
        self.assertEqual(call.await_count, 0)

    def test_anthropic_stream_error_returns_json_body(self):
        self.upstream.status = 529
        response = self.client.post(
            '/v1/messages?beta=true',
            headers={**self.auth, 'anthropic-beta': CLASSIFIER_BETA},
            json=self.classifier_payload(stream=True),
        )
        self.assertEqual(response.status_code, 529, response.text)
        self.assertEqual(response.json()['error']['message'], 'upstream failed')
        self.assertEqual(response.headers.get('x-should-retry'), 'true')
        self.assertNotIn('event: message_start', response.text)

    def test_safeguards_skip_anthropic_without_a_key(self):
        with self.store.lock:
            for item in self.store.data['providers']:
                if item['id'] == self.provider['id']:
                    item['api_key'] = ''
            self.store.persist()
        grok = self.store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        self.store.record_probe(grok['id'], True, usage={'input_tokens': 8, 'output_tokens': 1})
        grok_reply = {
            'id': 'msg_g',
            'type': 'message',
            'role': 'assistant',
            'model': 'grok-4.6',
            'content': [{'type': 'text', 'text': 'from grok'}],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }
        with patch('server.call_model', new=AsyncMock(return_value=grok_reply)):
            response = self.client.post(
                '/v1/messages?beta=true',
                headers={**self.auth, 'anthropic-beta': CLASSIFIER_BETA},
                json=self.classifier_payload(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['content'][0]['text'], 'from grok')

    def test_safeguards_do_not_failover_to_grok_on_anthropic_rate_limit(self):
        self.upstream.status = 429
        grok = self.store.save({'name': 'Grok Build', 'model': 'grok/grok-4.6'})
        self.store.record_probe(grok['id'], True, usage={'input_tokens': 8, 'output_tokens': 1})
        grok_reply = {
            'id': 'msg_g',
            'type': 'message',
            'role': 'assistant',
            'model': 'grok-4.6',
            'content': [{'type': 'text', 'text': 'from grok'}],
            'stop_reason': 'end_turn',
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }
        with patch('server.call_model', new=AsyncMock(return_value=grok_reply)) as call:
            response = self.client.post(
                '/v1/messages?beta=true',
                headers={**self.auth, 'anthropic-beta': CLASSIFIER_BETA},
                json=self.classifier_payload(),
            )
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()['error']['message'], 'rate_limit 429')
        self.assertEqual(response.headers.get('retry-after'), '2')
        self.assertEqual(response.headers.get('x-should-retry'), 'true')
        self.assertEqual(call.await_count, 0)

    def test_anthropic_passthrough_keeps_unicode_bytes_in_the_body(self):
        payload = self.classifier_payload()
        payload['messages'] = [{'role': 'user', 'content': 'café'}]
        response = self.client.post(
            '/v1/messages?beta=true',
            headers={**self.auth, 'anthropic-beta': CLASSIFIER_BETA},
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        raw = getattr(self.upstream, 'last_raw', b'')
        self.assertIn('café'.encode(), raw)
        self.assertNotIn(b'\\u00e9', raw)
