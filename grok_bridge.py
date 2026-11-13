"""Read the local Grok CLI session and talk to the CLI chat proxy."""
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_API_BASE = 'https://cli-chat-proxy.grok.com/v1'
FALLBACK_MODELS = ('grok-4.5', 'grok-4.6', 'grok-4.7', 'grok-4.7-build-fast')
_version_lock = threading.Lock()
_client_version = None


def grok_home():
    override = os.environ.get('GROK_HOME')
    return Path(override) if override else Path.home() / '.grok'


def api_base():
    return os.environ.get('GROK_CLI_CHAT_PROXY_BASE_URL', DEFAULT_API_BASE).rstrip('/')


def _parse_expiry(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _session():
    try:
        data = json.loads((grok_home() / 'auth.json').read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    chosen = None
    chosen_expiry = None
    now = datetime.now(timezone.utc)
    for value in data.values():
        if not isinstance(value, dict) or not value.get('key'):
            continue
        expiry = _parse_expiry(value.get('expires_at'))
        if expiry and expiry <= now:
            continue
        if chosen is None or (expiry and (chosen_expiry is None or expiry > chosen_expiry)):
            chosen = value
            chosen_expiry = expiry
    return chosen


def connected():
    return _session() is not None


def api_key():
    session = _session()
    if not session:
        raise RuntimeError('Could not read the Grok CLI session. Run grok login, then try again.')
    return session['key']


def _grok_binary():
    search = os.pathsep.join(['/opt/homebrew/bin', '/usr/local/bin', str(Path.home() / '.local/bin'), str(Path.home() / '.grok/bin'), os.environ.get('PATH', '')])
    return shutil.which('grok', path=search)


def client_version():
    global _client_version
    with _version_lock:
        if _client_version:
            return _client_version
        command = _grok_binary()
        if command:
            try:
                result = subprocess.run([command, '--version'], capture_output=True, text=True, timeout=5)
                match = re.search(r'(\d+\.\d+\.\d+)', (result.stdout or '') + (result.stderr or ''))
                if match:
                    _client_version = match.group(1)
                    return _client_version
            except (OSError, subprocess.SubprocessError):
                pass
        _client_version = '1.0.77'
        return _client_version


def extra_headers(model):
    version = client_version()
    return {
        'X-XAI-Token-Auth': 'xai-grok-cli',
        'x-grok-model-override': model,
        'x-grok-client-version': version,
        'User-Agent': 'grok-cli/' + version,
    }


def listed_models():
    return [item['id'] for item in model_facts()] or list(FALLBACK_MODELS)


def model_facts():
    try:
        data = json.loads((grok_home() / 'models_cache.json').read_text())
        models = data.get('models')
        if isinstance(models, dict) and models:
            facts = []
            for name, record in models.items():
                info = (record or {}).get('info') if isinstance(record, dict) else {}
                if not isinstance(info, dict):
                    info = {}
                facts.append({
                    'id': name,
                    'context_window': info.get('context_window'),
                    'api_backend': info.get('api_backend'),
                    'compactions_remaining': info.get('compactions_remaining'),
                })
            return sorted(facts, key=lambda item: item['id'])
        if isinstance(models, list):
            names = [item.get('id') if isinstance(item, dict) else str(item) for item in models]
            return [{'id': name, 'context_window': None, 'api_backend': None, 'compactions_remaining': None} for name in sorted(set(name for name in names if name))]
    except (OSError, ValueError, TypeError):
        pass
    return [{'id': name, 'context_window': None, 'api_backend': None, 'compactions_remaining': None} for name in FALLBACK_MODELS]


def session_info():
    live = _session()
    expires = live.get('expires_at') if live else None
    if expires is None:
        try:
            data = json.loads((grok_home() / 'auth.json').read_text())
            for value in data.values() if isinstance(data, dict) else []:
                if isinstance(value, dict) and value.get('expires_at'):
                    expires = value.get('expires_at')
                    break
        except (OSError, ValueError):
            pass
    return {'connected': live is not None, 'expires_at': expires, 'models': model_facts()}
