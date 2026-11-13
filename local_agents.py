import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from grok_bridge import connected as grok_connected
import paths

ROOT = Path(__file__).resolve().parent
DATA = paths.data_dir()
AUTH = DATA / 'accounts'
LINKS = {'chatgpt':'https://auth.openai.com/codex/device', 'copilot':'https://github.com/login/device'}
SUGGESTED = {
    'chatgpt': 'chatgpt/gpt-6.1-sol',
    'copilot': 'github_copilot/gpt-4o',
    'antigravity': 'antigravity/claude-sonnet-4-6',
    'grok': 'grok/grok-4.6',
    'anthropic': 'anthropic/claude-sonnet-4-6',
}


def configure_auth_environment(root=AUTH):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for provider, key in [('chatgpt', 'CHATGPT_TOKEN_DIR'), ('copilot', 'GITHUB_COPILOT_TOKEN_DIR')]:
        directory = root / provider
        directory.mkdir(mode=0o700, exist_ok=True)
        os.environ[key] = str(directory)
    os.umask(0o077)


def parse_device_line(provider, line):
    pattern = r'Enter code:\s*([A-Za-z0-9-]{4,32})' if provider == 'chatgpt' else r'enter code\s+([A-Za-z0-9-]{4,32})\s+to authenticate'
    match = re.search(pattern, line, re.IGNORECASE)
    return match.group(1) if match else None


class AgentManager:
    def __init__(self, root=AUTH):
        self.root = Path(root)
        self.jobs = {}
        self.lock = threading.Lock()

    def connected(self, provider):
        try:
            if provider == 'chatgpt':
                record = json.loads((self.root / provider / 'auth.json').read_text())
                return bool(record.get('access_token') and record.get('refresh_token'))
            if provider == 'copilot':
                return bool((self.root / provider / 'access-token').read_text().strip())
            if provider == 'antigravity':
                security = '/usr/bin/security' if os.path.exists('/usr/bin/security') else shutil.which('security')
                if not security:
                    return False
                result = subprocess.run([security, 'find-generic-password', '-s', 'gemini', '-a', 'antigravity'], capture_output=True, timeout=10)
                return result.returncode == 0
            if provider == 'grok':
                return grok_connected()
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return False

    def list_agents(self):
        search = os.pathsep.join(['/opt/homebrew/bin', '/usr/local/bin', str(Path.home()/'.local/bin'), str(Path.home()/'.grok/bin'), os.environ.get('PATH', '')])
        definitions = [
            ('chatgpt', 'Codex / ChatGPT', 'codex', 'chatgpt/', 'Device sign-in for a ChatGPT plan. A separate Switchboard login is required.'),
            ('copilot', 'GitHub Copilot', 'copilot', 'github_copilot/', 'Device sign-in for a Copilot plan. A separate Switchboard login is required.'),
            ('gemini', 'Gemini CLI', 'gemini', None, 'Installed agent detected; its login is not yet available to the gateway.'),
            ('grok', 'Grok Build', 'grok', 'grok/', 'Uses the grok login session through the CLI chat proxy. Test a model to verify access.'),
            ('opencode', 'OpenCode', 'opencode', None, 'Installed agent detected; configure an accessible model endpoint separately.'),
            ('ollama', 'Ollama', 'ollama', None, 'Use the Ollama provider preset to connect its local model server.'),
        ]
        result = [{'id': i, 'name': name, 'installed': bool(shutil.which(executable, path=search)), 'connected': self.connected(i) if prefix else False, 'prefix': prefix, 'suggested': SUGGESTED.get(i), 'detail': detail} for i, name, executable, prefix, detail in definitions]
        result.append({'id':'antigravity','name':'Google Antigravity','installed':bool(shutil.which('agy', path=search)), 'connected': self.connected('antigravity'), 'prefix':'antigravity/', 'suggested': SUGGESTED['antigravity'], 'detail':'Uses the agy Keychain account through an unofficial Cloud Code adapter. Test a model to verify access.'})
        result.append({'id':'anthropic','name':'Anthropic Claude','installed':True, 'connected': bool(os.environ.get('SWITCHBOARD_ANTHROPIC_API_KEY')), 'prefix':'anthropic/', 'suggested': SUGGESTED['anthropic'], 'detail':'Official Claude API. Paste a console.anthropic.com key on the provider. Switchboard always calls https://api.anthropic.com, never this gateway.'})
        return result

    def status(self, provider):
        if provider not in LINKS:
            raise ValueError('This agent does not have a sign-in adapter.')
        with self.lock:
            job = dict(self.jobs.get(provider, {'status':'idle'}))
        job['connected'] = self.connected(provider)
        return job

    def start_login(self, provider):
        if provider not in LINKS:
            raise ValueError('This agent does not have a sign-in adapter.')
        if self.connected(provider):
            raise ValueError('Switchboard is already connected to this account.')
        with self.lock:
            if self.jobs.get(provider, {}).get('status') in ('pending', 'code'):
                raise ValueError('A sign-in is already in progress.')
            self.jobs[provider] = {'status':'pending', 'url':LINKS[provider]}
        thread = threading.Thread(target=self._login_worker, args=(provider,), daemon=True)
        thread.start()

    def _login_worker(self, provider):
        env = dict(os.environ, CHATGPT_TOKEN_DIR=str(self.root/'chatgpt'), GITHUB_COPILOT_TOKEN_DIR=str(self.root/'copilot'))
        try:
            child = subprocess.Popen([sys.executable, '-u', str(ROOT/'login_helper.py'), provider], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            timer = threading.Timer(900, child.terminate)
            timer.start()
            try:
                for line in child.stdout:
                    code = parse_device_line(provider, line)
                    if code:
                        with self.lock:
                            self.jobs[provider] = {'status':'code','url':LINKS[provider], 'code':code}
                code = child.wait()
            finally:
                timer.cancel()
            with self.lock:
                if code == 0 and self.connected(provider):
                    self.jobs[provider] = {'status':'connected'}
                else:
                    self.jobs[provider] = {'status':'error', 'message':'Sign-in did not finish. Check your account and try again.'}
        except OSError:
            with self.lock:
                self.jobs[provider] = {'status':'error','message':'Could not start the sign-in helper.'}
