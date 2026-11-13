import hmac
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from local_agents import AgentManager, configure_auth_environment
configure_auth_environment()
os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
import httpx
import litellm
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from switchboard import Store, CLAUDE_SETTINGS, connect_claude, disconnect_claude
from antigravity_bridge import AntigravityBridge, listed_models as antigravity_models
import anthropic_bridge
import compat
import grok_bridge
import intelligence

ROOT = Path(__file__).parent
litellm.suppress_debug_info = True
litellm.use_chat_completions_url_for_anthropic_messages = True
antigravity = AntigravityBridge()


async def call_model(provider, payload):
    options = dict(payload)
    options['model'] = provider['model']
    if not provider['model'].startswith('anthropic/'):
        options['drop_params'] = True
    if provider['model'].startswith('antigravity/'):
        base, key = antigravity.endpoint()
        options.update(model='openai/' + provider['model'].split('/', 1)[1], api_base=base, api_key=key)
    elif provider['model'].startswith('grok/'):
        model_id = provider['model'].split('/', 1)[1]
        options.update(model='openai/responses/' + model_id, api_base=grok_bridge.api_base(), api_key=grok_bridge.api_key(), extra_headers=grok_bridge.extra_headers(model_id))
    elif provider['model'].startswith('anthropic/'):
        base, key = anthropic_bridge.endpoint(provider)
        options.update(api_key=key, api_base=base)
    elif provider.get('api_key'):
        options['api_key'] = provider['api_key']
    if provider.get('api_base') and not provider['model'].startswith(('antigravity/', 'grok/', 'anthropic/')):
        options['api_base'] = provider['api_base']
    return await litellm.anthropic.messages.acreate(**options)


def as_dict(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(exclude_none=True)
    if hasattr(value, 'dict'):
        return value.dict(exclude_none=True)
    return value


def safe_error(error, provider):
    message = str(error)
    secrets = [provider.get('api_key')]
    if provider.get('model', '').startswith('grok/'):
        try:
            secrets.append(grok_bridge.api_key())
        except Exception:
            pass
    for secret in secrets:
        if secret:
            message = message.replace(secret, '[redacted]')
    return message[:700] or type(error).__name__


def current_intelligence(store):
    return intelligence.snapshot(store, grok_bridge.session_info(), {'antigravity_models': antigravity_models()})


def routed_providers(store):
    with store.lock:
        snap = current_intelligence(store)
        by_id = {item['id']: dict(item) for item in store.data['providers']}
        smart = bool(store.data.get('smart_route', True))
        active = store.data.get('active')
    if not smart:
        item = by_id.get(active)
        return [item] if item and item.get('enabled') else []
    result = []
    for row in intelligence.candidates(snap['routes']):
        item = by_id.get(row['id'])
        if item and item.get('enabled'):
            result.append(item)
    if not result:
        item = by_id.get(active)
        if item and item.get('enabled'):
            result.append(item)
    return result[:3]


def retryable(message):
    return intelligence.classify(message) in ('unsupported', 'quota', 'rate_limited', 'auth')


def authorized(request, store):
    token = store.data['token']
    keys = (
        request.headers.get('x-api-key', ''),
        request.headers.get('authorization', '').removeprefix('Bearer '),
        request.headers.get('api-key', ''),
    )
    return any(key and hmac.compare_digest(key, token) for key in keys)


def auth_error():
    return JSONResponse({'type': 'error', 'error': {'type': 'authentication_error', 'message': 'Invalid gateway token.'}}, status_code=401)


def subscription_models(model):
    prefix = model.split('/', 1)[0] + '/' if '/' in model else ''
    if prefix == 'antigravity/':
        return list(antigravity_models())
    if prefix == 'grok/':
        return list(grok_bridge.listed_models())
    if prefix == 'anthropic/':
        return list(anthropic_bridge.listed_models())
    if prefix in ('chatgpt/', 'github_copilot/'):
        return sorted({key.split('/', 1)[1] for key in litellm.model_cost if key.startswith(prefix)})
    return []


async def complete_routed(store, payload):
    providers = routed_providers(store)
    if not providers:
        return None, 'No enabled provider is active in Switchboard.'
    last_error = 'No enabled provider is active in Switchboard.'
    for provider in providers:
        try:
            response = await call_model(provider, {**payload, 'stream': False})
            data = as_dict(response)
            if data and isinstance(data, dict):
                store.record_probe(provider['id'], True, usage=data.get('usage'))
            return data, None
        except Exception as exc:
            last_error = safe_error(exc, provider)
            store.record_probe(provider['id'], False, last_error)
            if not retryable(last_error):
                break
    return None, last_error


def buffered_events(response):
    data = as_dict(response)
    events = []
    def emit(kind, **fields):
        body = {'type':kind, **fields}
        events.append('event: ' + kind + '\ndata: ' + json.dumps(body, separators=(',', ':')) + '\n\n')
    usage = data.get('usage') or {}
    message = {**data, 'content':[], 'stop_reason':None, 'stop_sequence':None, 'usage':{**usage, 'output_tokens':0}}
    emit('message_start', message=message)
    for index, block in enumerate(data.get('content', [])):
        if block.get('type') == 'text':
            emit('content_block_start', index=index, content_block={'type':'text', 'text':''})
            emit('content_block_delta', index=index, delta={'type':'text_delta', 'text':block.get('text', '')})
        elif block.get('type') == 'tool_use':
            emit('content_block_start', index=index, content_block={'type':'tool_use', 'id':block['id'], 'name':block['name'], 'input':{}})
            emit('content_block_delta', index=index, delta={'type':'input_json_delta', 'partial_json':json.dumps(block.get('input', {}), separators=(',', ':'))})
        else:
            raise ValueError('Antigravity returned an unsupported response block: ' + str(block.get('type')))
        emit('content_block_stop', index=index)
    emit('message_delta', delta={'stop_reason':data.get('stop_reason'), 'stop_sequence':data.get('stop_sequence')}, usage={'output_tokens':usage.get('output_tokens', 0)})
    emit('message_stop')
    return events


def create_app(store=None, settings_path=CLAUDE_SETTINGS, agent_manager=None):
    store = store or Store()
    agents = agent_manager or AgentManager()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def guard(request, call_next):
        host = request.headers.get('host', '')
        if host not in ('127.0.0.1:18765', 'localhost:18765', 'testserver'):
            return JSONResponse({'error': 'Invalid host'}, status_code=403)
        if request.url.path.startswith('/api/'):
            origin = request.headers.get('origin', '')
            if (origin and origin not in ('http://127.0.0.1:18765', 'http://localhost:18765')) or request.headers.get('x-switchboard-ui') != '1':
                return JSONResponse({'error': 'A same-origin Switchboard request is required'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'error': str(exc)}, status_code=400)

    @app.get('/')
    async def index():
        return FileResponse(ROOT / 'static' / 'index.html')

    @app.get('/api/state')
    async def state():
        return store.public()

    @app.get('/api/intelligence')
    async def intelligence_state():
        return current_intelligence(store)

    @app.post('/api/intelligence')
    async def intelligence_settings(request: Request):
        payload = await request.json()
        if 'smart_route' in payload:
            store.set_smart_route(bool(payload['smart_route']))
        return current_intelligence(store)

    @app.post('/api/intelligence/scan')
    async def intelligence_scan():
        for provider in list(store.data.get('providers') or []):
            if not provider.get('enabled'):
                continue
            try:
                response = await call_model(dict(provider), {'messages': [{'role': 'user', 'content': 'Reply with the single word OK.'}], 'max_tokens': 8})
                data = as_dict(response)
                store.record_probe(provider['id'], True, usage=data.get('usage'))
            except Exception as exc:
                store.record_probe(provider['id'], False, safe_error(exc, provider))
        snap = current_intelligence(store)
        choice = snap.get('recommended')
        if store.data.get('smart_route', True) and choice:
            try:
                store.activate(choice['id'])
            except ValueError:
                pass
            snap = current_intelligence(store)
        return snap

    @app.get('/api/agents')
    async def installed_agents():
        result = agents.list_agents()
        for agent in result:
            if agent.get('prefix'):
                agent['models'] = subscription_models(agent['prefix'] + 'model')[:80]
        return {'agents': result}

    @app.get('/api/agents/{provider}/login')
    async def login_status(provider: str):
        return agents.status(provider)

    @app.post('/api/agents/{provider}/login')
    async def login(provider: str):
        agents.start_login(provider)
        return {'ok': True}

    @app.post('/api/providers')
    async def save(request: Request):
        return store.save(await request.json())

    @app.delete('/api/providers/{provider_id}')
    async def delete(provider_id: str):
        store.delete(provider_id)
        return {'ok': True}

    @app.post('/api/providers/{provider_id}/activate')
    async def activate(provider_id: str):
        store.activate(provider_id)
        return {'ok': True}

    @app.post('/api/claude/{action}')
    async def claude(action: str):
        if action == 'connect':
            connect_claude(store, settings_path)
        elif action == 'disconnect':
            disconnect_claude(store, settings_path)
        else:
            raise HTTPException(404)
        return {'ok': True}

    @app.post('/api/providers/{provider_id}/verify')
    async def verify(provider_id: str):
        provider = store.get(provider_id)
        if not provider:
            raise ValueError('Provider not found.')
        try:
            response = await call_model(dict(provider), {'messages': [{'role': 'user', 'content': 'Reply with the single word OK.'}], 'max_tokens': 32})
            data = as_dict(response)
            store.record_probe(provider_id, True, usage=data.get('usage'))
            return {'ok': True, 'message': 'The provider returned a response. Streaming and tool use have not been verified.', 'response': str(data.get('content', ''))[:300]}
        except Exception as exc:
            message = safe_error(exc, provider)
            store.record_probe(provider_id, False, message)
            return JSONResponse({'ok': False, 'error': message}, status_code=502)

    @app.post('/api/providers/{provider_id}/models')
    async def models(provider_id: str):
        provider = store.get(provider_id)
        if not provider:
            raise ValueError('Provider not found.')
        known = subscription_models(provider['model'])
        if known:
            return {'models': known[:500]}
        if not provider.get('api_base'):
            raise ValueError('Save an OpenAI-compatible base URL before discovering models.')
        headers = {'Authorization': 'Bearer ' + provider['api_key']} if provider.get('api_key') else {}
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.get(provider['api_base'] + '/models', headers=headers)
                response.raise_for_status()
                data = response.json()
            return {'models': [str(x['id']) for x in data.get('data', []) if isinstance(x, dict) and 'id' in x][:500]}
        except Exception as exc:
            return JSONResponse({'error': safe_error(exc, provider)}, status_code=502)

    @app.get('/v1/models')
    @app.get('/models')
    async def list_models(request: Request):
        if not authorized(request, store):
            return auth_error()
        models = [{'id': 'switchboard', 'object': 'model', 'owned_by': 'switchboard'}]
        seen = {'switchboard'}
        for provider in store.data.get('providers') or []:
            ident = provider.get('model')
            if provider.get('enabled') and ident and ident not in seen:
                models.append({'id': ident, 'object': 'model', 'owned_by': 'switchboard'})
                seen.add(ident)
        return {'object': 'list', 'data': models}

    @app.post('/v1/chat/completions')
    @app.post('/chat/completions')
    async def chat_completions(request: Request):
        if not authorized(request, store):
            return auth_error()
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({'error': {'message': 'Request body must be JSON.', 'type': 'invalid_request_error'}}, status_code=400)
        payload = compat.openai_to_anthropic(body)
        data, error = await complete_routed(store, payload)
        if error:
            code = 503 if error.startswith('No enabled provider') else 502
            return JSONResponse({'error': {'message': error, 'type': 'api_error'}}, status_code=code)
        completion = compat.anthropic_to_openai(data, body.get('model') or 'switchboard')
        if body.get('stream'):
            return StreamingResponse(compat.openai_sse(completion), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})
        return JSONResponse(completion)

    @app.post('/v1/messages')
    async def messages(request: Request):
        if not authorized(request, store):
            return auth_error()
        providers = routed_providers(store)
        if not providers:
            return JSONResponse({'type':'error','error':{'type':'api_error','message':'No enabled provider is active in Switchboard.'}}, status_code=503)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({'type':'error','error':{'type':'invalid_request_error','message':'Request body must be JSON.'}}, status_code=400)
        last_error = 'No enabled provider is active in Switchboard.'
        for provider in providers:
            try:
                buffered = bool(payload.get('stream') and provider['model'].startswith('antigravity/'))
                outgoing = {**payload, 'stream':False} if buffered else payload
                response = await call_model(provider, outgoing)
                data = as_dict(response) if not payload.get('stream') or buffered else None
                if data and isinstance(data, dict):
                    store.record_probe(provider['id'], True, usage=data.get('usage'))
                if buffered:
                    return StreamingResponse(iter(buffered_events(response)), media_type='text/event-stream', headers={'X-Accel-Buffering':'no'})
                if not payload.get('stream'):
                    return JSONResponse(data)
                async def events(current=provider, stream=response):
                    try:
                        async for event in stream:
                            if isinstance(event, (bytes, str)):
                                yield event
                                continue
                            item = as_dict(event)
                            yield 'event: ' + item.get('type', 'message') + '\ndata: ' + json.dumps(item, separators=(',', ':')) + '\n\n'
                    except Exception as exc:
                        yield 'event: error\ndata: ' + json.dumps({'type':'error', 'error': {'type':'api_error', 'message':safe_error(exc, current)}}) + '\n\n'
                return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering':'no'})
            except Exception as exc:
                last_error = safe_error(exc, provider)
                store.record_probe(provider['id'], False, last_error)
                if not retryable(last_error):
                    break
        return JSONResponse({'type':'error','error':{'type':'api_error','message':last_error}}, status_code=502)

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    return app


app = create_app()
