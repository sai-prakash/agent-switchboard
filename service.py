#!/usr/bin/env python3
"""Start Switchboard on this machine. macOS can also install a login LaunchAgent."""
import json
import os
import plistlib
import socket
import subprocess
import sys
from pathlib import Path

import paths

ROOT = Path(__file__).resolve().parent
LABEL = 'local.agent-switchboard'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
LOGS = paths.log_dir()
DOMAIN = f'gui/{os.getuid()}' if hasattr(os, 'getuid') else ''
HOST = '127.0.0.1'
PORT = '18765'
COMMANDS = ('install', 'uninstall', 'start', 'stop', 'status')


def venv_python():
    unix = ROOT / '.venv' / 'bin' / 'python'
    windows = ROOT / '.venv' / 'Scripts' / 'python.exe'
    candidate = windows if os.name == 'nt' else unix
    other = unix if candidate == windows else windows
    chosen = candidate if candidate.exists() else other
    return str(chosen if chosen.exists() else Path(sys.executable))


def port_open():
    sock = socket.socket()
    sock.settimeout(0.3)
    try:
        sock.connect((HOST, int(PORT)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def loaded():
    if sys.platform != 'darwin' or not DOMAIN:
        return False
    result = subprocess.run(['/bin/launchctl', 'print', f'{DOMAIN}/{LABEL}'], capture_output=True)
    return result.returncode == 0


def status():
    return {
        'running': port_open(),
        'installed': PLIST.exists(),
        'loaded': loaded(),
        'url': 'http://' + HOST + ':' + PORT,
        'label': LABEL,
        'root': str(ROOT),
    }


def install():
    if sys.platform != 'darwin':
        raise SystemExit('Login-item install is macOS-only. Use: python service.py start')
    if PLIST.exists():
        raise SystemExit(f'{PLIST} already exists; remove it explicitly before reinstalling.')
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {'Label': LABEL, 'ProgramArguments': [venv_python(), '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', '18765', '--no-access-log'], 'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True, 'StandardOutPath': str(LOGS / 'service.log'), 'StandardErrorPath': str(LOGS / 'service.log'), 'EnvironmentVariables': {'LITELLM_LOCAL_MODEL_COST_MAP': 'True'}}
    with PLIST.open('xb') as f:
        os.chmod(PLIST, 0o600)
        plistlib.dump(data, f)
    result = subprocess.run(['/bin/launchctl', 'bootstrap', DOMAIN, str(PLIST)])
    if result.returncode:
        PLIST.unlink()
        raise SystemExit('Could not start the LaunchAgent. No LaunchAgent was installed.')
    print('Installed and started. Open http://127.0.0.1:18765')


def uninstall():
    if not PLIST.exists():
        raise SystemExit('No Switchboard LaunchAgent is installed.')
    result = subprocess.run(['/bin/launchctl', 'bootout', DOMAIN, str(PLIST)])
    if result.returncode:
        raise SystemExit('Could not stop the LaunchAgent; plist was left in place.')
    PLIST.unlink()
    print('Service removed. Provider data and Claude Code settings were not changed.')


def start():
    if sys.platform == 'darwin' and PLIST.exists() and DOMAIN:
        if not loaded():
            subprocess.run(['/bin/launchctl', 'bootstrap', DOMAIN, str(PLIST)], check=False)
        subprocess.run(['/bin/launchctl', 'kickstart', '-k', f'{DOMAIN}/{LABEL}'], check=False)
        print('Started. Open http://127.0.0.1:18765')
        return
    executable = venv_python()
    os.chdir(ROOT)
    os.execv(executable, [executable, '-m', 'uvicorn', 'server:app', '--host', HOST, '--port', PORT, '--no-access-log'])


def stop():
    if sys.platform != 'darwin' or not DOMAIN:
        raise SystemExit('Stop is macOS LaunchAgent only.')
    if not PLIST.exists():
        raise SystemExit('No Switchboard LaunchAgent is installed.')
    subprocess.run(['/bin/launchctl', 'bootout', DOMAIN, str(PLIST)], check=False)
    print('Stopped.')


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit('Usage: python service.py start|stop|status|install|uninstall')
    command = sys.argv[1]
    if command == 'status':
        print(json.dumps(status()))
    else:
        {'install': install, 'uninstall': uninstall, 'start': start, 'stop': stop}[command]()
