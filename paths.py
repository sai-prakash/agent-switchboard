"""Per-user Switchboard paths that work on macOS, Linux, and Windows."""
import os
import sys
from pathlib import Path


def data_dir():
    override = os.environ.get('AGENT_SWITCHBOARD_HOME')
    if override:
        return Path(override)
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Agent Switchboard'
    if os.name == 'nt':
        root = os.environ.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
        return Path(root) / 'Agent Switchboard'
    return Path(os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config')) / 'agent-switchboard'


def claude_settings():
    override = os.environ.get('CLAUDE_CONFIG_DIR')
    root = Path(override) if override else Path.home() / '.claude'
    return root / 'settings.json'


def log_dir():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / 'Agent Switchboard'
    return data_dir() / 'logs'
