const $ = id => document.getElementById(id);
let state = {providers: []}, agentState = [], intel = {routes: []}, editing = null, toastTimer, loginPoll;
const presets = {
  openai: ['OpenAI', 'openai/', 'https://api.openai.com/v1'],
  gemini: ['Gemini', 'gemini/', ''],
  xai: ['xAI', 'xai/', ''],
  anthropic: ['Anthropic Claude', 'anthropic/claude-sonnet-4-6', ''],
  openrouter: ['OpenRouter', 'openrouter/', 'https://openrouter.ai/api/v1'],
  ollama: ['Ollama', 'ollama_chat/', 'http://127.0.0.1:11434'],
  compatible: ['Compatible endpoint', 'openai/', ''],
  custom: ['', '', '']
};
async function api(path, options = {}) {
  const response = await fetch('/api' + path, { ...options, headers: {'Content-Type':'application/json', 'X-Switchboard-UI':'1'} });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function notify(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 8000); }
function button(label, action, className = '') { const b = document.createElement('button'); b.textContent = label; b.className = className; b.type = 'button'; b.addEventListener('click', async () => { b.disabled = true; try { await action(); } catch (e) { notify(e.message); } finally { b.disabled = false; } }); return b; }
async function refresh() {
  [state, agentState, intel] = await Promise.all([api('/state'), api('/agents').then(x => x.agents), api('/intelligence')]);
  $('smart-route').checked = !!intel.smart_route;
  render(); renderAgents(); renderIntel();
}
function renderIntel() {
  $('intel-reason').textContent = intel.reason || 'Scan access to rank the models this machine can use.';
  const grok = intel.sessions?.grok || {};
  const gravity = intel.sessions?.antigravity || {};
  const meta = [];
  if (grok.expires_at) meta.push('Grok session ' + grok.expires_at.replace('T', ' ').replace('Z', ' UTC').slice(0, 22) + (grok.connected ? '' : ' expired'));
  if (grok.catalog_count) meta.push(grok.catalog_count + ' Grok models listed');
  if (gravity.catalog_count) meta.push(gravity.catalog_count + ' Antigravity models listed');
  if (intel.failover?.length) meta.push('Failover: ' + intel.failover.map(item => item.model).join(' → '));
  $('intel-sessions').textContent = meta.join(' · ');
  $('intel-sessions').hidden = !meta.length;
  const list = $('intel-list'); list.replaceChildren();
  if (!intel.routes?.length) {
    const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = 'Add a provider, then scan access. Switchboard will skip listed models this account cannot call.'; list.append(empty); return;
  }
  for (const row of intel.routes) {
    const item = document.createElement('div'); item.className = 'provider';
    const badge = document.createElement('div'); badge.className = 'provider-badge'; badge.textContent = (row.name || '?').slice(0, 1);
    const info = document.createElement('div'); info.className = 'provider-info';
    const title = document.createElement('strong'); title.textContent = row.name + (intel.recommended?.id === row.id ? ' · Recommended' : '');
    const details = document.createElement('small');
    const bits = [row.model, row.status, row.family];
    if (row.context_window) bits.push(Math.round(row.context_window / 1000) + 'k context');
    if (row.session_expires_at) bits.push('session ' + row.session_expires_at.replace('T', ' ').replace('Z', ' UTC').slice(0, 22));
    if (row.usage && row.usage.output_tokens != null) bits.push('last usage in ' + row.usage.input_tokens + ' / out ' + row.usage.output_tokens);
    if (row.compactions_remaining != null) bits.push(row.compactions_remaining + ' compact left');
    bits.push(row.reason);
    if (row.error && !['ready', 'quota', 'unsupported', 'auth', 'rate_limited'].includes(row.status)) bits.push(row.error);
    details.textContent = bits.join(' · ');
    info.append(title, details);
    const actions = document.createElement('div'); actions.className = 'provider-actions';
    const tag = document.createElement('span'); tag.className = 'tag status-' + row.status; tag.textContent = row.status; actions.append(tag);
    if (row.enabled && intel.recommended?.id !== row.id) actions.append(button('Use', async () => { await api(`/providers/${row.id}/activate`, {method:'POST'}); await refresh(); notify('Route selected for new requests.'); }));
    item.append(badge, info, actions); list.append(item);
  }
}
function renderAgents() {
  const list = $('agent-list'); list.replaceChildren();
  for (const a of agentState) {
    const row = document.createElement('div'); row.className = 'provider';
    const badge = document.createElement('div'); badge.className = 'provider-badge'; badge.textContent = a.name.slice(0,1);
    const info = document.createElement('div'); info.className = 'provider-info';
    const title = document.createElement('strong'); title.textContent = a.name;
    const details = document.createElement('small'); details.textContent = (a.installed ? 'Installed' : 'Not detected') + (a.connected ? ' · Account connected' : '') + ' · ' + a.detail; info.append(title, details);
    const actions = document.createElement('div'); actions.className = 'provider-actions';
    if (a.prefix) {
      if (!a.connected && (a.id === 'chatgpt' || a.id === 'copilot')) actions.append(button('Connect', async () => startLogin(a)));
      actions.append(button('Add model', async () => {
        if (a.id === 'anthropic') {
          if (a.models?.length) return showAgentModels(a);
          openEditor(); $('preset').value = 'anthropic'; $('name').value = a.name; $('model').value = a.suggested || a.prefix; syncCredentialFields(); $('key').focus();
          return;
        }
        if (a.models?.length) return showAgentModels(a);
        openEditor(); $('name').value = a.name; $('model').value = a.suggested || a.prefix; $('model').focus();
      }));
    }
    row.append(badge, info, actions); list.append(row);
  }
}
async function startLogin(agent) {
  await api(`/agents/${agent.id}/login`, {method:'POST'});
  $('login-title').textContent = `Connect ${agent.name}`; $('login-message').textContent = 'Preparing sign-in…'; $('login-code').textContent = ''; $('login-link').hidden = true; $('login-dialog').showModal();
  const poll = async () => {
    try {
      const result = await api(`/agents/${agent.id}/login`);
      if (result.status === 'code') { $('login-message').textContent = 'Open the provider’s sign-in page and enter this code:'; $('login-code').textContent = result.code; $('login-link').href = result.url; $('login-link').hidden = false; }
      if (result.status === 'connected') { $('login-message').textContent = 'Account connected. Add a model to test it.'; $('login-code').textContent = ''; $('login-link').hidden = true; await refresh(); return; }
      if (result.status === 'error') { $('login-message').textContent = result.message; $('login-code').textContent = ''; $('login-link').hidden = true; return; }
      loginPoll = setTimeout(poll, 2000);
    } catch (e) { $('login-message').textContent = e.message; }
  };
  await poll();
}
function render() {
  const active = state.providers.find(p => p.id === state.active);
  $('active-name').textContent = active ? active.name : 'Choose a provider';
  $('active-model').textContent = active ? active.model : 'No model selected';
  $('route-status').textContent = active ? 'Route selected' : 'No provider selected';
  $('route-status').classList.toggle('ready', !!active);
  $('claude-state').textContent = state.claude_connected ? 'Connected to Switchboard' : 'Not connected';
  $('claude-connect').textContent = state.claude_connected ? 'Disconnect Claude Code' : 'Connect Claude Code';
  $('claude-note').textContent = state.claude_connected ? 'Restart Claude Code to apply changes. Use Anthropic default to talk to api.anthropic.com with your Claude subscription.' : 'Connect routes Claude Code through this gateway. Use Anthropic default to clear gateway env and use Anthropic again.';
  if (state.gateway) {
    $('anthropic-base').textContent = state.gateway.base_url;
    $('openai-base').textContent = state.gateway.openai_base_url;
  }
  $('count').textContent = state.providers.length + (state.providers.length === 1 ? ' connection' : ' connections');
  const list = $('provider-list'); list.replaceChildren();
  if (!state.providers.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = 'No providers yet. Add an API provider or a local model to get started.'; list.append(empty); return; }
  for (const p of state.providers) {
    const row = document.createElement('div'); row.className = 'provider';
    const badge = document.createElement('div'); badge.className = 'provider-badge'; badge.textContent = p.name.slice(0, 1).toUpperCase();
    const info = document.createElement('div'); info.className = 'provider-info';
    const name = document.createElement('strong'); name.textContent = p.name;
    const details = document.createElement('small'); details.textContent = p.model + (p.enabled ? '' : ' · Disabled') + (isSubscription(p.model) ? ' · Subscription' : (p.has_key ? ' · Key saved' : ' · No key')); info.append(name, details);
    const actions = document.createElement('div'); actions.className = 'provider-actions';
    if (active?.id === p.id) actions.append(button('Active', async () => {}, 'active'));
    else if (p.enabled) actions.append(button('Use', async () => { await api(`/providers/${p.id}/activate`, {method:'POST'}); await refresh(); notify('Route selected for new requests.'); }));
    actions.append(button('Test', async () => { notify('Testing ' + p.name + '…'); const result = await api(`/providers/${p.id}/verify`, {method:'POST'}); notify(result.message); }));
    if (p.api_base || /^(chatgpt|github_copilot|antigravity|grok|anthropic)\//.test(p.model)) actions.append(button('Models', async () => { const result = await api(`/providers/${p.id}/models`, {method:'POST'}); showModels(p, result.models); }));
    actions.append(button(p.enabled ? 'Disable' : 'Enable', async () => { await api('/providers', {method:'POST', body:JSON.stringify({...p, enabled:!p.enabled})}); await refresh(); }));
    actions.append(button('Edit', async () => openEditor(p)));
    actions.append(button('Delete', async () => { if (confirm(`Delete ${p.name} and its saved key?`)) { await api(`/providers/${p.id}`, {method:'DELETE'}); await refresh(); notify('Provider deleted.'); } }));
    row.append(badge, info, actions); list.append(row);
  }
}
function showModels(p, models) {
  const list = $('models-list'); list.replaceChildren();
  if (!models.length) list.textContent = 'This endpoint returned no models.';
  for (const model of models) list.append(button(model, async () => { $('models-dialog').close(); openEditor(p); const prefix = p.model.includes('/') ? p.model.slice(0, p.model.indexOf('/') + 1) : ''; $('model').value = prefix + model; }));
  $('models-dialog').showModal();
}
function showAgentModels(agent) {
  const list = $('models-list'); list.replaceChildren();
  for (const model of agent.models) list.append(button(model, async () => {
    $('models-dialog').close();
    if (agent.id === 'anthropic') {
      openEditor();
      $('preset').value = 'anthropic';
      $('name').value = agent.name;
      $('model').value = agent.prefix + model;
      syncCredentialFields();
      $('key').focus();
      return;
    }
    await api('/providers', {method:'POST', body: JSON.stringify({name: agent.name, model: agent.prefix + model, enabled: true})});
    await refresh();
    notify('Saved ' + model + '. Test it before use.');
  }));
  $('models-dialog').showModal();
}
function isSubscription(model) { return /^(chatgpt|github_copilot|antigravity|grok)\//.test(model || ''); }
function syncCredentialFields() {
  const hidden = isSubscription($('model').value);
  $('base').closest('label').hidden = hidden;
  $('key').closest('label').hidden = hidden;
}
function openEditor(p = null) {
  editing = p; $('provider-form').reset(); $('preset').value = 'custom'; $('panel-title').textContent = p ? 'Edit provider' : 'Add provider';
  $('name').value = p?.name || ''; $('model').value = p?.model || ''; $('base').value = p?.api_base || ''; $('key').value = ''; $('enabled').checked = p ? p.enabled : true;
  $('key-help').textContent = p?.has_key ? 'A key is saved. Leave blank to keep it, or enter a replacement.' : 'Stored in a private local file, never displayed again.';
  syncCredentialFields();
  $('shade').hidden = false; $('name').focus();
}
function closeEditor() { $('shade').hidden = true; $('add').focus(); }
$('add').onclick = () => openEditor(); $('close').onclick = closeEditor; $('cancel').onclick = closeEditor; $('models-close').onclick = () => $('models-dialog').close();
$('login-close').onclick = () => $('login-dialog').close();
$('login-dialog').addEventListener('close', () => clearTimeout(loginPoll));
$('shade').addEventListener('click', e => { if (e.target === $('shade')) closeEditor(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('shade').hidden) closeEditor(); });
$('preset').onchange = e => { const [name, model, base] = presets[e.target.value]; $('name').value = name; $('model').value = model; $('base').value = base; syncCredentialFields(); };
$('model').addEventListener('input', syncCredentialFields);
$('provider-form').onsubmit = async e => { e.preventDefault(); try { await api('/providers', {method:'POST', body:JSON.stringify({id:editing?.id, name:$('name').value, model:$('model').value, api_base:$('base').value, api_key:$('key').value, enabled:$('enabled').checked})}); closeEditor(); await refresh(); notify('Provider saved. Test it before use.'); } catch (error) { notify(error.message); } };
$('claude-connect').onclick = async () => { try { await api('/claude/' + (state.claude_connected ? 'disconnect' : 'connect'), {method:'POST'}); await refresh(); notify('Claude Code settings updated. Restart Claude Code to apply changes.'); } catch (error) { notify(error.message); } };
$('claude-restore').onclick = async () => { try { await api('/claude/restore', {method:'POST'}); await refresh(); notify('Claude Code will use Anthropic again. Restart Claude Code to apply changes.'); } catch (error) { notify(error.message); } };
$('smart-route').onchange = async e => { try { intel = await api('/intelligence', {method:'POST', body: JSON.stringify({smart_route: e.target.checked})}); await refresh(); notify(e.target.checked ? 'Smart routing is on. New requests use the best proven route.' : 'Smart routing is off. New requests use the selected route.'); } catch (error) { notify(error.message); } };
$('scan-access').onclick = async () => { const button = $('scan-access'); button.disabled = true; try { notify('Scanning saved models…'); intel = await api('/intelligence/scan', {method:'POST'}); await refresh(); notify(intel.reason || 'Scan complete.'); } catch (error) { notify(error.message); } finally { button.disabled = false; } };
async function copyText(value, message) {
  try { await navigator.clipboard.writeText(value); notify(message); }
  catch (error) { notify(error.message); }
}
$('copy-openai').onclick = () => copyText(state.gateway?.openai_base_url || 'http://127.0.0.1:18765/v1', 'OpenAI-compatible base URL copied.');
$('copy-token').onclick = () => {
  if (!state.gateway?.token) return notify('Gateway token is unavailable.');
  copyText(state.gateway.token, 'Gateway API key copied. Paste it into other coding agents as the API key.');
};
refresh().catch(e => notify(e.message));
