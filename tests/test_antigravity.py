import unittest
import base64
import json
import httpx

from antigravity_bridge import AntigravityBridge, parse_keychain_record


class AntigravityBridgeTests(unittest.TestCase):
    def test_keychain_record_is_decoded_without_changing_it(self):
        token = {'token': {'access_token':'example', 'refresh_token':'refresh'}}
        value = 'keyring-version:' + base64.b64encode(json.dumps(token).encode()).decode()
        self.assertEqual(parse_keychain_record(value), token)

    def test_loopback_bridge_requires_internal_token(self):
        bridge = AntigravityBridge()
        try:
            base, token = bridge.endpoint()
            self.assertEqual(httpx.get(base + '/models').status_code, 401)
            response = httpx.get(base + '/models', headers={'Authorization':'Bearer ' + token})
            self.assertEqual(response.status_code, 200)
            self.assertIn('data', response.json())
        finally:
            bridge.close()

