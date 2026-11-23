import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import service


class ServiceControlTests(unittest.TestCase):
    def test_status_is_running_when_port_accepts(self):
        with patch.object(service, 'port_open', return_value=True), patch.object(service, 'PLIST', Path('/tmp/switchboard-missing.plist')):
            data = service.status()
        self.assertTrue(data['running'])
        self.assertFalse(data['installed'])
        self.assertEqual(data['url'], 'http://127.0.0.1:18765')
        self.assertEqual(data['label'], 'local.agent-switchboard')

    def test_stop_unloads_launchagent_and_keeps_plist(self):
        with tempfile.TemporaryDirectory() as folder:
            plist = Path(folder) / 'local.agent-switchboard.plist'
            plist.write_text('placeholder')
            with patch.object(service, 'PLIST', plist), patch.object(service, 'DOMAIN', 'gui/1'), patch.object(service.sys, 'platform', 'darwin'), patch.object(service.subprocess, 'run') as run:
                run.return_value = type('Result', (), {'returncode': 0})()
                service.stop()
            self.assertTrue(plist.exists())
            self.assertEqual(run.call_args.args[0][:3], ['/bin/launchctl', 'bootout', 'gui/1'])

    def test_start_bootstraps_when_agent_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            plist = Path(folder) / 'local.agent-switchboard.plist'
            plist.write_text('placeholder')
            calls = []

            def fake_run(args, **kwargs):
                calls.append(list(args))
                result = type('Result', (), {'returncode': 1 if args[1] == 'print' else 0})()
                return result

            with patch.object(service, 'PLIST', plist), patch.object(service, 'DOMAIN', 'gui/1'), patch.object(service.sys, 'platform', 'darwin'), patch.object(service.subprocess, 'run', side_effect=fake_run):
                service.start()
            self.assertEqual(calls[0][:3], ['/bin/launchctl', 'print', 'gui/1/local.agent-switchboard'])
            self.assertEqual(calls[1][:3], ['/bin/launchctl', 'bootstrap', 'gui/1'])
            self.assertEqual(calls[2][:3], ['/bin/launchctl', 'kickstart', '-k', 'gui/1/local.agent-switchboard'][:3])
            self.assertIn('gui/1/local.agent-switchboard', calls[2])
