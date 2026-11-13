"""Rank saved providers by real access, usage, and Claude Code fit."""
import sys
from datetime import datetime, timezone

import paths

STATUS_SCORE = {'ready': 100, 'untested': 40, 'rate_limited': 15, 'error': 8, 'quota': 0, 'unsupported': 0, 'auth': 0, 'disabled': -100}
FAMILY_SCORE = {'claude': 40, 'grok': 35, 'openai': 20, 'gemini': 10, 'other': 0}
PRIOR_SCORE = {'likely': 20, 'unknown': 5, 'quota_sensitive': 0, 'risky': -30}
USABLE = {'ready', 'untested'}


def classify(message):
    text = str(message or '').lower()
    if 'model_not_supported' in text or 'requested model is not supported' in text:
        return 'unsupported'
    if 'resource_exhausted' in text or 'usage_limit' in text or 'quota' in text:
        return 'quota'
    if '429' in text or 'ratelimit' in text or 'rate_limit' in text or 'rate limit' in text:
        return 'rate_limited'
    if 'grok cli session' in text or 'unauthorized' in text or '401' in text:
        return 'auth'
    return 'error'


def family(model):
    ident = (model.split('/', 1)[-1] if model else '').lower()
    prefix = model.split('/', 1)[0].lower() if '/' in (model or '') else ''
    if prefix == 'grok' or ident.startswith('grok'):
        return 'grok'
    if any(token in ident for token in ('claude', 'sonnet', 'opus', 'haiku', 'fable', 'anthropic')):
        return 'claude'
    if ident.startswith(('gpt', 'o1', 'o3', 'o4')):
        return 'openai'
    if 'gemini' in ident:
        return 'gemini'
    return 'other'


def prior(model):
    prefix = model.split('/', 1)[0] if '/' in (model or '') else ''
    kind = family(model)
    if prefix == 'github_copilot' and kind == 'claude':
        return 'risky'
    if prefix in ('antigravity', 'chatgpt'):
        return 'quota_sensitive'
    if prefix in ('grok', 'anthropic') or (prefix == 'github_copilot' and kind == 'openai'):
        return 'likely'
    return 'unknown'


def score(route):
    window = min((route.get('context_window') or 0) / 1_000_000, 1)
    total = STATUS_SCORE.get(route.get('status'), 0) + FAMILY_SCORE.get(route.get('family'), 0) + window
    if route.get('status') == 'ready':
        if route.get('family') == 'claude':
            total += 15
        return total
    return total + PRIOR_SCORE.get(route.get('prior'), 0)


def recommend(routes):
    usable = [route for route in routes if route.get('status') in USABLE]
    if not usable:
        return None
    return max(usable, key=lambda route: (score(route), route.get('context_window') or 0))


def candidates(routes):
    return sorted((route for route in routes if route.get('status') in USABLE), key=score, reverse=True)


def explain(routes, choice):
    if not choice:
        return 'No usable route. Scan access or add a working provider.'
    parts = [choice.get('name', choice.get('model', 'This route')) + ' is the best available route for Claude Code.']
    for route in routes:
        if route.get('id') == choice.get('id'):
            continue
        status = route.get('status')
        if status in ('unsupported', 'quota', 'auth', 'rate_limited'):
            parts.append(route.get('name', route.get('model', 'Another provider')) + ' is ' + status + '.')
        elif prior(route.get('model', '')) == 'risky' and status != 'ready':
            parts.append(route.get('name', 'Copilot Claude') + ' is listed but often has no access.')
    return ' '.join(parts)


def parse_expiry(value):
    text = str(value or '').strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def expiry_warning(expires_at):
    moment = parse_expiry(expires_at)
    if not moment:
        return None
    seconds = (moment - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        return 'Grok CLI session has expired. Run grok login.'
    if seconds < 3600:
        return 'Grok CLI session expires in under an hour. Run grok login to refresh.'
    return None


def status_reason(route):
    status = route.get('status')
    error = str(route.get('error') or '').strip()
    if status == 'ready':
        return 'Proven on the last scan.'
    if status == 'unsupported':
        return 'The account listed this model but rejected it.'
    if status == 'quota':
        return 'The account is rate-limited or out of quota.'
    if status == 'auth':
        return 'The local session is missing or expired.'
    if status == 'rate_limited':
        return 'The provider asked us to wait before retrying.'
    if status == 'error':
        return 'Last scan failed: ' + (error[:180] if error else 'see logs.')
    if status == 'disabled':
        return 'This provider is disabled.'
    if status == 'untested':
        if route.get('prior') == 'risky':
            return 'Listed on the catalog; Copilot Claude ids often fail until proven.'
        if route.get('prior') == 'quota_sensitive':
            return 'Usually listed; quota is only known after a live scan.'
        return 'Saved, not scanned yet.'
    return 'See the last error for details.'


def session_view(grok_info, extra=None):
    extra = extra or {}
    grok_models = grok_info.get('models') or []
    sessions = {
        'grok': {
            'connected': bool(grok_info.get('connected')),
            'expires_at': grok_info.get('expires_at'),
            'catalog_count': len(grok_models),
            'models': [
                {
                    'id': item.get('id'),
                    'context_window': item.get('context_window'),
                    'compactions_remaining': item.get('compactions_remaining'),
                }
                for item in grok_models
                if isinstance(item, dict)
            ],
        }
    }
    names = extra.get('antigravity_models')
    if names is not None:
        listed = [str(name) for name in names]
        sessions['antigravity'] = {
            'catalog_count': len(listed),
            'models': [{'id': name} for name in listed[:40]],
        }
    return sessions


def describe(provider, probe=None, extra=None):
    probe = probe or {}
    extra = extra or {}
    model = provider.get('model') or ''
    if not provider.get('enabled', True):
        status = 'disabled'
    elif probe.get('ok'):
        status = 'ready'
    elif probe.get('error') or probe.get('status'):
        status = probe.get('status') or classify(probe.get('error'))
    elif model.startswith('grok/') and extra.get('connected') is False:
        status = 'auth'
    else:
        status = 'untested'
    window = extra.get('context_window') or probe.get('context_window')
    if extra.get('models') and not window:
        ident = model.split('/', 1)[-1]
        match = next((item for item in extra['models'] if item.get('id') == ident), None)
        if match:
            window = match.get('context_window')
    route = {
        'id': provider.get('id'),
        'name': provider.get('name'),
        'model': model,
        'enabled': bool(provider.get('enabled', True)),
        'family': family(model),
        'prior': prior(model),
        'status': status,
        'error': None if status == 'ready' else probe.get('error'),
        'usage': probe.get('usage'),
        'probed_at': probe.get('probed_at'),
        'context_window': window,
        'session_expires_at': extra.get('expires_at') if model.startswith('grok/') else None,
        'catalog_count': extra.get('catalog_count'),
        'compactions_remaining': extra.get('compactions_remaining'),
    }
    route['score'] = round(score(route), 3)
    route['reason'] = status_reason(route)
    return route


def snapshot(store, grok_info=None, extra=None):
    grok_info = grok_info or {}
    extra = extra or {}
    probes = store.data.get('probes') or {}
    routes = []
    for provider in store.data.get('providers') or []:
        details = dict(grok_info) if str(provider.get('model', '')).startswith('grok/') else {}
        if details.get('models'):
            ident = provider['model'].split('/', 1)[-1]
            match = next((item for item in details['models'] if item.get('id') == ident), None)
            if match:
                details['context_window'] = match.get('context_window')
                details['compactions_remaining'] = match.get('compactions_remaining')
                details['catalog_count'] = len(details['models'])
        routes.append(describe(provider, probes.get(provider['id']), details))
    routes.sort(key=lambda route: (score(route), route.get('context_window') or 0), reverse=True)
    choice = recommend(routes)
    recommended = None
    if choice:
        recommended = {key: choice[key] for key in ('id', 'name', 'model', 'status', 'family', 'reason') if key in choice}
    reason = explain(routes, choice)
    warning = expiry_warning(grok_info.get('expires_at'))
    if warning:
        reason = reason + ' ' + warning
    failover = [{'id': route['id'], 'name': route['name'], 'model': route['model']} for route in candidates(routes)[:3]]
    return {
        'smart_route': bool(store.data.get('smart_route', True)),
        'active': store.data.get('active'),
        'recommended': recommended,
        'reason': reason,
        'routes': routes,
        'failover': failover,
        'sessions': session_view(grok_info, extra),
        'machine': {'platform': sys.platform, 'data_dir': str(paths.data_dir())},
        'scanned_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
