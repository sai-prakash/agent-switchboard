"""Official Anthropic API as a Switchboard provider, pinned off this gateway."""
import os
from urllib.parse import urlparse

import httpx

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


def has_key(provider):
    return bool(str(provider.get('api_key') or os.environ.get('SWITCHBOARD_ANTHROPIC_API_KEY') or '').strip())


def endpoint(provider):
    key = str(provider.get('api_key') or os.environ.get('SWITCHBOARD_ANTHROPIC_API_KEY') or '').strip()
    if not key:
        raise ValueError('Save an Anthropic API key on this provider, or set SWITCHBOARD_ANTHROPIC_API_KEY.')
    base = str(provider.get('api_base') or OFFICIAL_BASE).strip().rstrip('/')
    if is_loop(base):
        raise ValueError('Anthropic cannot use the Switchboard listen address. Leave the base URL blank to use https://api.anthropic.com.')
    return base, key


def incoming_headers(headers, api_key):
    outgoing = {
        'x-api-key': api_key,
        'content-type': 'application/json',
        'anthropic-version': '2023-06-01',
    }
    for name, value in headers.items():
        key = name.lower()
        if key.startswith('anthropic-') and value:
            outgoing[key] = value
    return outgoing


def passthrough_response_headers(headers):
    keep = {}
    for name, value in headers.items():
        key = name.lower()
        if key in ('content-type', 'retry-after', 'x-should-retry') or key.startswith('anthropic-'):
            keep[key] = value
    return keep


def messages_url(base, query=''):
    url = str(base).rstrip('/') + '/v1/messages'
    if query:
        url += '?' + query
    return url


def messages_body(provider, payload):
    body = dict(payload)
    native = provider['model']
    if native.startswith('anthropic/'):
        native = native.split('/', 1)[1]
    model = str(body.get('model') or '')
    if model.startswith('anthropic/'):
        body['model'] = model.split('/', 1)[1]
    elif not model or model == 'switchboard':
        body['model'] = native
    return body


def response_media(headers, stream=False):
    return headers.get('content-type') or ('text/event-stream' if stream else 'application/json')


async def post_messages(provider, payload, headers, stream=False, query=''):
    base, key = endpoint(provider)
    timeout = httpx.Timeout(600.0, connect=30.0)
    outgoing = incoming_headers(headers, key)
    body = messages_body(provider, payload)
    url = messages_url(base, query)
    if not stream:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(url, headers=outgoing, json=body)
            return response.status_code, passthrough_response_headers(response.headers), response.content
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        request = client.build_request('POST', url, headers=outgoing, json=body)
        response = await client.send(request, stream=True)
    except Exception:
        await client.aclose()
        raise
    header_map = passthrough_response_headers(response.headers)
    if response.status_code >= 400:
        error_body = await response.aread()
        await response.aclose()
        await client.aclose()
        return response.status_code, header_map, error_body

    async def events():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return response.status_code, header_map, events()
