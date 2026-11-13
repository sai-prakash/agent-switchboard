import json
import os
import secrets
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import anthropic_bridge
import paths
from intelligence import classify

DATA = paths.data_dir()
CLAUDE_SETTINGS = paths.claude_settings()
MANAGED = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL')


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.switchboard-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else paths.data_dir() / 'config.json'
        self.lock = threading.RLock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {'token': secrets.token_urlsafe(32), 'providers': [], 'active': None, 'claude_backup': None, 'smart_route': True, 'probes': {}}
            self.persist()
        self.data.setdefault('smart_route', True)
        self.data.setdefault('probes', {})

    def persist(self):
        write_json(self.path, self.data)

    def public(self):
        with self.lock:
            return {
                'providers': [self.redact(p) for p in self.data['providers']],
                'active': self.data['active'],
                'claude_connected': self.data['claude_backup'] is not None,
                'smart_route': bool(self.data.get('smart_route', True)),
                'gateway': {
                    'base_url': 'http://127.0.0.1:18765',
                    'openai_base_url': 'http://127.0.0.1:18765/v1',
                    'token': self.data['token'],
                },
            }

    def set_smart_route(self, enabled):
        with self.lock:
            self.data['smart_route'] = bool(enabled)
            self.persist()

    def record_probe(self, provider_id, ok, error=None, usage=None, extra=None):
        with self.lock:
            payload = {
                'ok': bool(ok),
                'error': str(error)[:300] if error else None,
                'status': 'ready' if ok else classify(error),
                'probed_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'usage': usage,
            }
            if extra:
                payload.update(extra)
            self.data.setdefault('probes', {})[provider_id] = payload
            self.persist()

    @staticmethod
    def redact(p):
        return {**{k: v for k, v in p.items() if k != 'api_key'}, 'has_key': bool(p.get('api_key'))}

    def get(self, provider_id):
        return next((p for p in self.data['providers'] if p['id'] == provider_id), None)

    def save(self, values):
        with self.lock:
            name, model = str(values.get('name', '')).strip(), str(values.get('model', '')).strip()
            if not name or not model or len(name) > 100 or len(model) > 300:
                raise ValueError('Enter a name and a LiteLLM model identifier.')
            base = str(values.get('api_base', '')).strip().rstrip('/')
            if base and model.startswith(('chatgpt/', 'github_copilot/', 'antigravity/', 'grok/')):
                raise ValueError('Subscription connections cannot override their authenticated endpoint.')
            if model.startswith('anthropic/') and anthropic_bridge.is_loop(base):
                raise ValueError('Anthropic cannot use the Switchboard listen address. Leave the base URL blank to use https://api.anthropic.com.')
            if base:
                parsed = urlparse(base)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError('Base URL must be an HTTP(S) URL without credentials, query, or fragment.')
                if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
                    raise ValueError('Remote provider URLs must use HTTPS.')
            existing = self.get(values.get('id'))
            if values.get('id') and not existing:
                raise ValueError('Provider not found.')
            item = {'id': existing['id'] if existing else str(uuid.uuid4()), 'name': name, 'model': model, 'api_base': base, 'enabled': bool(values.get('enabled', True)), 'api_key': (str(values.get('api_key') or '') or (existing or {}).get('api_key', ''))}
            if existing:
                self.data['providers'][self.data['providers'].index(existing)] = item
            else:
                self.data['providers'].append(item)
            if not item['enabled'] and self.data['active'] == item['id']:
                self.data['active'] = None
            self.persist()
            return self.redact(item)

    def activate(self, provider_id):
        with self.lock:
            item = self.get(provider_id)
            if not item or not item['enabled']:
                raise ValueError('Choose an enabled provider.')
            self.data['active'] = provider_id
            self.persist()

    def delete(self, provider_id):
        with self.lock:
            item = self.get(provider_id)
            if not item:
                raise ValueError('Provider not found.')
            self.data['providers'].remove(item)
            if self.data['active'] == provider_id:
                self.data['active'] = None
            self.persist()


def read_settings(path):
    settings = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(settings, dict) or not isinstance(settings.get('env', {}), dict):
        raise ValueError('Claude settings must contain an object and an env object.')
    return settings


def connect_claude(store, path=CLAUDE_SETTINGS, base='http://127.0.0.1:18765'):
    with store.lock:
        if store.data['claude_backup'] is not None:
            raise ValueError('Claude Code is already connected.')
        settings = read_settings(path)
        env = settings.setdefault('env', {})
        previous = {key: {'present': key in env, 'value': env.get(key)} for key in MANAGED}
        installed = {'ANTHROPIC_BASE_URL': base, 'ANTHROPIC_AUTH_TOKEN': store.data['token'], 'ANTHROPIC_MODEL': 'switchboard', 'ANTHROPIC_DEFAULT_OPUS_MODEL': 'switchboard', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'switchboard', 'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'switchboard'}
        store.data['claude_backup'] = {'previous': previous, 'installed': installed, 'env_existed': 'env' in (json.loads(path.read_text()) if path.exists() else {})}
        store.persist()
        env.update(installed)
        write_json(path, settings)


def disconnect_claude(store, path=CLAUDE_SETTINGS):
    with store.lock:
        backup = store.data['claude_backup']
        if backup is None:
            raise ValueError('Claude Code is not connected.')
        settings = read_settings(path)
        env = settings.setdefault('env', {})
        if any(env.get(k) != v for k, v in backup['installed'].items()):
            raise ValueError('Claude Code settings changed outside Switchboard. Restore or remove the gateway settings manually; no values were overwritten.')
        for k, value in backup['previous'].items():
            if value['present']:
                env[k] = value['value']
            else:
                env.pop(k, None)
        if not env and not backup['env_existed']:
            settings.pop('env', None)
        write_json(path, settings)
        store.data['claude_backup'] = None
        store.persist()
