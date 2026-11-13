import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from server import create_app
from switchboard import Store


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.last_request = body
        self.server.last_path = self.path
        self.server.last_headers = {key.lower(): value for key, value in self.headers.items()}
        self.send_response(200)
        if self.path in ('/responses', '/v1/responses'):
            response = {'id':'resp_test','object':'response','created_at':1,'status':'completed','model':'test-model','output':[{'id':'msg_test','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':'Hello','annotations':[]}]}],'usage':{'input_tokens':5,'output_tokens':1,'total_tokens':6}}
            if body.get('stream'):
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(('event: response.completed\ndata: ' + json.dumps({'type':'response.completed','response':response}) + '\n\n').encode())
            else:
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            return
        if body.get('stream'):
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            chunks = [
                {'id':'chatcmpl-test','object':'chat.completion.chunk','created':1,'model':'test-model','choices':[{'index':0,'delta':{'role':'assistant','content':'Hello'},'finish_reason':None}]},
                {'id':'chatcmpl-test','object':'chat.completion.chunk','created':1,'model':'test-model','choices':[{'index':0,'delta':{},'finish_reason':'stop'}]},
            ]
            for chunk in chunks:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            message = {'role':'assistant','content':'Hello'}
            finish = 'stop'
            if body.get('tools'):
                message = {'role':'assistant','content':None,'tool_calls':[{'id':'call_1','type':'function','function':{'name':'lookup','arguments':'{"query":"test"}'}}]}
                finish = 'tool_calls'
            data = {'id':'chatcmpl-test','object':'chat.completion','created':1,'model':'test-model','choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':5,'completion_tokens':1,'total_tokens':6}}
            self.wfile.write(json.dumps(data).encode())


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.thread.join()

    def test_litellm_translates_nonstreaming_and_streaming(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'config.json')
            p = store.save({'name':'Mock','model':'openai/test-model','api_base':f'http://127.0.0.1:{self.upstream.server_port}/v1','api_key':'test-key'})
            store.activate(p['id'])
            client = TestClient(create_app(store, Path(d) / 'settings.json'), base_url='http://127.0.0.1:18765')
            headers = {'x-api-key':store.data['token']}
            payload = {'model':'switchboard','messages':[{'role':'user','content':'Hi'}],'max_tokens':32}
            response = client.post('/v1/messages', headers=headers, json=payload)
            self.assertEqual(response.status_code, 200, f'{response.text}; path={getattr(self.upstream, "last_path", None)}; request={getattr(self.upstream, "last_request", None)}')
            self.assertEqual(response.json()['content'][0]['text'], 'Hello')
            self.assertEqual(self.upstream.last_request['model'], 'test-model')
            response = client.post('/v1/messages', headers=headers, json={**payload, 'stream':True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('event: message_start', response.text)
            self.assertIn('Hello', response.text)
            self.assertIn('event: message_stop', response.text)
            response = client.post('/v1/messages', headers=headers, json={**payload, 'tools':[{'name':'lookup', 'description':'Look something up', 'input_schema':{'type':'object','properties':{'query':{'type':'string'}}}}]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['content'][0]['type'], 'tool_use')
            self.assertEqual(response.json()['content'][0]['input'], {'query':'test'})

    def test_chatgpt_subscription_translation_uses_responses_endpoint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'auth.json').write_text(json.dumps({'access_token':'mock-access-token','refresh_token':'mock-refresh','account_id':'mock-account','expires_at':4102444800}))
            store = Store(root / 'config.json')
            p = store.save({'name':'ChatGPT','model':'chatgpt/gpt-6.1-sol'})
            store.activate(p['id'])
            client = TestClient(create_app(store, root / 'settings.json'), base_url='http://127.0.0.1:18765')
            with patch.dict(os.environ, {'CHATGPT_TOKEN_DIR':d, 'CHATGPT_API_BASE':f'http://127.0.0.1:{self.upstream.server_port}'}):
                response = client.post('/v1/messages', headers={'x-api-key':store.data['token']}, json={'model':'switchboard','messages':[{'role':'user','content':'Hi'}],'max_tokens':32})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.upstream.last_path, '/responses')
            self.assertEqual(response.json()['content'][0]['text'], 'Hello')

    def test_copilot_subscription_translation_uses_chat_completions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'access-token').write_text('mock-github-token')
            store = Store(root / 'config.json')
            p = store.save({'name':'Copilot','model':'github_copilot/gpt-4o'})
            store.activate(p['id'])
            client = TestClient(create_app(store, root / 'settings.json'), base_url='http://127.0.0.1:18765')
            with patch.dict(os.environ, {'GITHUB_COPILOT_TOKEN_DIR':d, 'GITHUB_COPILOT_API_BASE':f'http://127.0.0.1:{self.upstream.server_port}'}), patch('litellm.llms.github_copilot.authenticator.Authenticator.get_api_key', return_value='mock-copilot-key'):
                response = client.post('/v1/messages', headers={'x-api-key':store.data['token']}, json={'model':'switchboard','messages':[{'role':'user','content':'Hi'}],'max_tokens':32})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(self.upstream.last_path, ('/chat/completions', '/v1/chat/completions'))
            self.assertEqual(response.json()['content'][0]['text'], 'Hello')

    def test_grok_subscription_uses_cli_chat_proxy_headers(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / 'auth.json').write_text(json.dumps({
                'https://auth.x.ai::example': {
                    'key': 'mock-grok-session',
                    'refresh_token': 'mock-refresh',
                    'expires_at': '2099-01-01T00:00:00Z',
                }
            }))
            store = Store(home / 'config.json')
            p = store.save({'name': 'Grok', 'model': 'grok/grok-4.6'})
            store.activate(p['id'])
            client = TestClient(create_app(store, home / 'settings.json'), base_url='http://127.0.0.1:18765')
            env = {
                'GROK_HOME': d,
                'GROK_CLI_CHAT_PROXY_BASE_URL': f'http://127.0.0.1:{self.upstream.server_port}/v1',
            }
            payload = {'model': 'switchboard', 'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 32}
            with patch.dict(os.environ, env), patch('grok_bridge.client_version', return_value='1.0.77'):
                response = client.post('/v1/messages', headers={'x-api-key': store.data['token']}, json=payload)
                plain_path = self.upstream.last_path
                plain_headers = dict(self.upstream.last_headers)
                thinking = client.post('/v1/messages', headers={'x-api-key': store.data['token']}, json={**payload, 'thinking': {'type': 'enabled', 'budget_tokens': 128}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(plain_path, ('/responses', '/v1/responses'))
            self.assertEqual(plain_headers.get('authorization'), 'Bearer mock-grok-session')
            self.assertEqual(plain_headers.get('x-xai-token-auth'), 'xai-grok-cli')
            self.assertEqual(plain_headers.get('x-grok-model-override'), 'grok-4.6')
            self.assertEqual(plain_headers.get('x-grok-client-version'), '1.0.77')
            self.assertEqual(plain_headers.get('user-agent'), 'grok-cli/1.0.77')
            self.assertEqual(response.json()['content'][0]['text'], 'Hello')
            self.assertEqual(thinking.status_code, 200, thinking.text)
            self.assertIn(self.upstream.last_path, ('/responses', '/v1/responses'))
            self.assertEqual(self.upstream.last_request.get('model'), 'grok-4.6')
            self.assertEqual(thinking.json()['content'][0]['text'], 'Hello')
