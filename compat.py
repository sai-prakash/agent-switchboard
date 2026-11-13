"""Translate OpenAI Chat Completions payloads to Anthropic Messages and back."""
import json
from datetime import datetime, timezone


def text_of(content):
    if content is None:
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get('text') or item.get('content') or '')
        return ''.join(parts)
    return str(content)


def openai_tool_to_anthropic(tool):
    if not isinstance(tool, dict):
        return {'name': 'tool', 'description': '', 'input_schema': {'type': 'object', 'properties': {}}}
    fn = tool.get('function') if isinstance(tool.get('function'), dict) else tool
    return {
        'name': fn.get('name') or 'tool',
        'description': fn.get('description') or '',
        'input_schema': fn.get('parameters') or {'type': 'object', 'properties': {}},
    }


def openai_to_anthropic(body):
    body = body or {}
    system = []
    converted = []
    for message in body.get('messages') or []:
        if not isinstance(message, dict):
            continue
        role = message.get('role')
        if role == 'system':
            text = text_of(message.get('content'))
            if text:
                system.append(text)
            continue
        if role == 'tool':
            converted.append({
                'role': 'user',
                'content': [{'type': 'tool_result', 'tool_use_id': message.get('tool_call_id'), 'content': text_of(message.get('content'))}],
            })
            continue
        if role == 'assistant' and message.get('tool_calls'):
            blocks = []
            text = text_of(message.get('content'))
            if text:
                blocks.append({'type': 'text', 'text': text})
            for call in message.get('tool_calls') or []:
                fn = (call or {}).get('function') or {}
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except json.JSONDecodeError:
                    args = {}
                blocks.append({'type': 'tool_use', 'id': call.get('id'), 'name': fn.get('name'), 'input': args})
            converted.append({'role': 'assistant', 'content': blocks})
            continue
        converted.append({'role': role or 'user', 'content': message.get('content') if isinstance(message.get('content'), str) else text_of(message.get('content'))})
    payload = {
        'model': body.get('model') or 'switchboard',
        'messages': converted or [{'role': 'user', 'content': 'Hello'}],
        'max_tokens': int(body.get('max_completion_tokens') or body.get('max_tokens') or 4096),
        'stream': bool(body.get('stream')),
    }
    if system:
        payload['system'] = '\n\n'.join(system)
    if body.get('temperature') is not None:
        payload['temperature'] = body['temperature']
    if body.get('stop'):
        payload['stop_sequences'] = body['stop'] if isinstance(body['stop'], list) else [body['stop']]
    if body.get('tools'):
        payload['tools'] = [openai_tool_to_anthropic(tool) for tool in body['tools']]
    return payload


def anthropic_to_openai(data, requested_model='switchboard'):
    data = data or {}
    text_parts = []
    tool_calls = []
    for block in data.get('content') or []:
        if not isinstance(block, dict):
            continue
        if block.get('type') == 'text':
            text_parts.append(block.get('text') or '')
        elif block.get('type') == 'tool_use':
            tool_calls.append({
                'id': block.get('id'),
                'type': 'function',
                'function': {'name': block.get('name'), 'arguments': json.dumps(block.get('input') or {}, separators=(',', ':'))},
            })
    finish = {'end_turn': 'stop', 'max_tokens': 'length', 'tool_use': 'tool_calls', 'stop_sequence': 'stop'}.get(data.get('stop_reason'), 'stop')
    message = {'role': 'assistant', 'content': ''.join(text_parts) or (None if tool_calls else '')}
    if tool_calls:
        message['tool_calls'] = tool_calls
    usage = data.get('usage') or {}
    prompt = usage.get('input_tokens') or 0
    completion = usage.get('output_tokens') or 0
    return {
        'id': data.get('id') or 'chatcmpl-switchboard',
        'object': 'chat.completion',
        'created': int(datetime.now(timezone.utc).timestamp()),
        'model': requested_model or 'switchboard',
        'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
        'usage': {'prompt_tokens': prompt, 'completion_tokens': completion, 'total_tokens': prompt + completion},
    }


def openai_sse(completion):
    message = ((completion.get('choices') or [{}])[0].get('message')) or {}
    model = completion.get('model') or 'switchboard'
    cid = completion.get('id') or 'chatcmpl-switchboard'
    created = completion.get('created') or int(datetime.now(timezone.utc).timestamp())
    finish = ((completion.get('choices') or [{}])[0].get('finish_reason')) or 'stop'

    def chunk(delta, reason=None):
        return {'id': cid, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}

    events = [chunk({'role': 'assistant'})]
    if message.get('content'):
        events.append(chunk({'content': message['content']}))
    if message.get('tool_calls'):
        events.append(chunk({'tool_calls': message['tool_calls']}))
    events.append(chunk({}, finish))
    for event in events:
        yield 'data: ' + json.dumps(event, separators=(',', ':')) + '\n\n'
    yield 'data: [DONE]\n\n'
