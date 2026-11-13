"""A token-protected loopback wrapper around the pinned Antigravity adapter."""
import hmac
import base64
import importlib.util
import json
import secrets
import subprocess
import threading
import time
from pathlib import Path

SOURCE = Path(__file__).parent / 'vendor-research' / 'antigravity-proxy' / 'antigravity_proxy.py'
spec = importlib.util.spec_from_file_location('antigravity_vendor', SOURCE)
vendor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vendor)

# agy currently lists these Cloud Code model IDs. Keep older vendor names too.
CURRENT_MODELS = (
    'gemini-3.8-flash-low',
    'gemini-3.8-flash-medium',
    'gemini-3.8-flash-high',
    'claude-sonnet-4-6',
    'claude-opus-4-6-thinking',
    'gpt-oss-120b-medium',
)
vendor.MODEL_MAP.update({name: (name, None) for name in CURRENT_MODELS})
vendor.MODEL_MAP.setdefault('claude-sonnet-4.6', ('claude-sonnet-4-6', None))
vendor.MODEL_MAP.setdefault('claude-opus-4.6', ('claude-opus-4-6-thinking', None))


def listed_models():
    return sorted(set(CURRENT_MODELS) | set(vendor.MODEL_MAP))


def parse_keychain_record(value):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        _, encoded = value.split(':', 1)
        return json.loads(base64.b64decode(encoded))


class AntigravityKeychain:
    def __init__(self):
        self.lock = threading.Lock()
        self.refreshed = None

    def get_access_token(self):
        with self.lock:
            if self.refreshed and vendor._parse_expiry(self.refreshed.get('expiry')) > time.time() + 120:
                return self.refreshed['access_token']
            try:
                result = subprocess.run(['/usr/bin/security','find-generic-password','-s','gemini','-a','antigravity','-w'], capture_output=True, text=True, check=True, timeout=30)
                record = parse_keychain_record(result.stdout.strip())
                token = record['token']
                if token.get('access_token') and vendor._parse_expiry(token.get('expiry')) > time.time() + 120:
                    return token['access_token']
                if not token.get('refresh_token'):
                    raise ValueError('Missing refresh token')
                self.refreshed = vendor._refresh_access_token(token['refresh_token'])
                return self.refreshed['access_token']
            except Exception as exc:
                raise RuntimeError('Could not access Antigravity Keychain credentials. Open agy and sign in, then allow Keychain access.') from exc


keychain = AntigravityKeychain()
vendor.get_access_token = keychain.get_access_token


class AntigravityBridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.server = None
        self.key = secrets.token_urlsafe(32)

    def endpoint(self):
        with self.lock:
            if self.server is None:
                key = self.key
                class ProtectedHandler(vendor.ProxyHandler):
                    def authorized(self):
                        given = self.headers.get('Authorization', '').removeprefix('Bearer ')
                        if not hmac.compare_digest(given, key):
                            self._send_error_json('unauthorized', 'Invalid bridge token', http_code=401)
                            return False
                        return True
                    def do_GET(self):
                        if self.authorized():
                            super().do_GET()
                    def do_POST(self):
                        if self.authorized():
                            super().do_POST()
                self.server = vendor.ThreadingHTTPServer(('127.0.0.1', 0), ProtectedHandler)
                self.server.daemon_threads = True
                threading.Thread(target=self.server.serve_forever, daemon=True).start()
            return f'http://127.0.0.1:{self.server.server_port}/v1', self.key

    def close(self):
        with self.lock:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                self.server = None
