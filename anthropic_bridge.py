"""Official Anthropic API as a Switchboard provider, pinned off this gateway."""
import os
from urllib.parse import urlparse

OFFICIAL_BASE = 'https://api.anthropic.com'
CURRENT_MODELS = (
    'claude-haiku-4-5',
    'claude-haiku-5-5',
    'claude-opus-4-6',
    'claude-opus-4-7',
    'claude-opus-4-8',
    'claude-opus-5',
    'claude-opus-5-5',
    'claude-sonnet-4-6',
    'claude-sonnet-5',
    'claude-sonnet-5-5',
    'claude-fable-5-2',
)


def is_loop(base):
    parsed = urlparse(str(base or '').strip())
    host = (parsed.hostname or '').lower()
    if host in ('127.0.0.1', 'localhost', '::1') and parsed.port == 18765:
        return True
    return False


def listed_models():
    names = set(CURRENT_MODELS)
    try:
        import litellm
        names.update(key for key in litellm.model_cost if key.startswith('claude-') and '/' not in key and not key.endswith('-latest'))
    except Exception:
        pass
    return sorted(names)


def endpoint(provider):
    key = str(provider.get('api_key') or os.environ.get('SWITCHBOARD_ANTHROPIC_API_KEY') or '').strip()
    if not key:
        raise ValueError('Save an Anthropic API key on this provider, or set SWITCHBOARD_ANTHROPIC_API_KEY.')
    base = str(provider.get('api_base') or OFFICIAL_BASE).strip().rstrip('/')
    if is_loop(base):
        raise ValueError('Anthropic cannot use the Switchboard listen address. Leave the base URL blank to use https://api.anthropic.com.')
    return base, key
