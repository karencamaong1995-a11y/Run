"""Integration tests. Proxmox is an HTTPS fixture, never a user's hypervisor."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SOURCE = Path(__file__).resolve().parents[1] / 'klone-ultimate-merged-everything.py'
spec = importlib.util.spec_from_file_location('controller', SOURCE)
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)


class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.ca = cls.root / 'fixture.crt'
        cls.pemkey = cls.root / 'fixture.key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(cls.pemkey), '-out', str(cls.ca), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.calls = []
        cls.fixture_mode = 'normal'
        cls.fixture_secret = 'fixture@pve!controller=test-only-not-a-real-token'

        class Fixture(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, data, code=200):
                blob = json.dumps({'data': data}).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)

            def do_GET(self):
                cls.calls.append(('GET', self.path, self.headers.get('Authorization')))
                if cls.fixture_mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', 'https://127.0.0.1:1/never')
                    self.end_headers()
                elif cls.fixture_mode == 'denied':
                    self.respond('denied: ' + cls.fixture_secret, 403)
                elif self.path.endswith('/qemu'):
                    self.respond([{'vmid': 100, 'name': 'fixture-template', 'template': 1, 'status': 'stopped'},
                                  {'vmid': 101, 'name': 'fixture-vm', 'status': 'running'}])
                elif self.path.endswith('/config'):
                    self.respond({'template': int('/100/' in self.path)})
                elif '/tasks/' in self.path:
                    self.respond({'status': 'stopped', 'exitstatus': 'FAIL' if cls.fixture_mode == 'task-fail' else 'OK'})
                else:
                    self.respond(None, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                cls.calls.append(('POST', self.path, body, self.headers.get('Authorization')))
                self.respond('UPID:pve:0001:0002:0003:qmstart:101:fixture@pve:')

        cls.fixture = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cls.ca, cls.pemkey)
        cls.fixture.socket = ctx.wrap_socket(cls.fixture.socket, server_side=True)
        threading.Thread(target=cls.fixture.serve_forever, daemon=True).start()
        cls.cfg = dict(k.DEFAULTS, pve_host='https://127.0.0.1:' + str(cls.fixture.server_port),
                       pve_node='pve', pve_token=cls.fixture_secret, pve_ca_file=str(cls.ca), allowed_vmids=[100, 101, 102])
        cls.app = k.App(cls.cfg, cls.root)
        cls.servers = [k.Server(('127.0.0.1', 0), k.handler(cls.app)) for _ in range(2)]
        for server in cls.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers + [cls.fixture]:
            s.shutdown()
            s.server_close()
        cls.app.pool.shutdown()
        cls.temp.cleanup()

    def setUp(self):
        type(self).fixture_mode = 'normal'
        self.calls.clear()

    def request(self, path, data=None, auth=True, listener=0, extra=None, raw=None):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.app.key
        headers.update(extra or {})
        payload = raw if raw is not None else (None if data is None else json.dumps(data).encode())
        request = Request('http://127.0.0.1:%s%s' % (self.servers[listener].server_port, path), data=payload, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except HTTPError as e:
            return e.code, json.load(e)

    def test_01_both_listeners_live(self):
        for i in (0, 1):
            self.assertEqual(self.request('/health', auth=False, listener=i)[0], 200)
            self.assertEqual(self.request('/api/status', listener=i)[1]['controller'], 'RUNNING')

    def test_02_auth_required_on_every_private_route(self):
        for route in ['/api/status', '/api/jobs', '/api/evidence', '/api/pve/vms']:
            for i in (0, 1):
                self.assertEqual(self.request(route, auth=False, listener=i)[0], 401)
        self.assertEqual(self.request('/api/pve/action', {}, auth=False)[0], 401)
        self.assertEqual(self.calls, [])

    def test_03_real_workload_hash_replays(self):
        code, j = self.request('/api/jobs', {'rounds': 10000})
        self.assertEqual(code, 202)
        for _ in range(100):
            job = next(x for x in self.request('/api/jobs')[1]['jobs'] if x['id'] == j['id'])
            if job['status'] == 'SUCCEEDED':
                break
            time.sleep(.02)
        self.assertEqual(job['status'], 'SUCCEEDED')
        result = job['result']
        h = bytes.fromhex(result['seed_hex'])
        for _ in range(result['rounds']):
            h = hashlib.sha256(h).digest()
        self.assertEqual(h.hex(), result['result_sha256'])
        self.assertGreater(result['hashes_per_second'], 0)

    def test_04_workload_bounds(self):
        for n in [-1, 0, 1000001, True, '1000']:
            self.assertEqual(self.request('/api/jobs', {'rounds': n})[0], 400)

    def test_05_malformed_requests(self):
        self.assertEqual(self.request('/api/jobs', raw=b'{')[0], 400)
        self.assertEqual(self.request('/api/jobs', raw=b'[]')[0], 400)
        self.assertEqual(self.request('/api/jobs', raw=b'x'*9000)[0], 413)
        self.assertEqual(self.request('/api/jobs', {}, extra={'Content-Type': 'text/plain'})[0], 415)

    def test_06_cross_origin_rejected(self):
        self.assertEqual(self.request('/api/jobs', {}, extra={'Origin': 'https://example.invalid'})[0], 403)

    def test_07_inventory_and_auth_header(self):
        code, result = self.request('/api/pve/vms')
        self.assertEqual(code, 200)
        self.assertEqual(result['vms'][0]['vmid'], 100)
        self.assertEqual(self.calls[0][2], 'PVEAPIToken=' + self.fixture_secret)
        self.assertNotIn(self.fixture_secret, json.dumps(result))

    def test_08_exact_power_endpoints(self):
        for action in ('start', 'shutdown', 'stop'):
            code, result = self.request('/api/pve/action', {'vmid': 101, 'action': action, 'confirm': action.upper() + ' 101'})
            self.assertEqual(code, 202)
            self.assertEqual(result['status'], 'ACCEPTED')
            self.assertEqual(self.calls[-1][1], '/api2/json/nodes/pve/qemu/101/status/' + action)

    def test_09_no_mutation_without_confirmation(self):
        code, _ = self.request('/api/pve/action', {'vmid': 101, 'action': 'stop'})
        self.assertEqual(code, 409)
        self.assertFalse(self.calls)

    def test_10_allowlist_blocks_unapproved_vms(self):
        code, _ = self.request('/api/pve/action', {'vmid': 999, 'action': 'start', 'confirm': 'START 999'})
        self.assertEqual(code, 403)
        self.assertFalse(self.calls)

    def test_11_full_template_clone(self):
        code, result = self.request('/api/pve/action', {'vmid': 100, 'newid': 102, 'name': 'klone-102', 'action': 'clone', 'confirm': 'CLONE 100 TO 102'})
        self.assertEqual(code, 202)
        self.assertEqual(self.calls[-1][1], '/api2/json/nodes/pve/qemu/100/clone')
        self.assertEqual(self.calls[-1][2], {'newid': 102, 'name': 'klone-102', 'full': 1})

    def test_12_existing_clone_id_rejected(self):
        code, _ = self.request('/api/pve/action', {'vmid': 100, 'newid': 101, 'action': 'clone', 'confirm': 'CLONE 100 TO 101'})
        self.assertEqual(code, 409)
        self.assertFalse(any(c[0] == 'POST' for c in self.calls))

    def test_13_non_template_rejected(self):
        code, _ = self.request('/api/pve/action', {'vmid': 101, 'newid': 102, 'action': 'clone', 'confirm': 'CLONE 101 TO 102'})
        self.assertEqual(code, 409)

    def test_14_task_outcomes(self):
        upid = 'UPID:pve:0001:0002:0003:qmstart:101:fixture@pve:'
        self.assertEqual(self.request('/api/pve/task?upid=' + upid)[1]['status'], 'SUCCEEDED')
        type(self).fixture_mode = 'task-fail'
        self.assertEqual(self.request('/api/pve/task?upid=' + upid)[1]['status'], 'FAILED')
        self.assertEqual(self.request('/api/pve/task?upid=../../bad')[0], 400)

    def test_15_tls_not_bypassed(self):
        client = k.Proxmox(dict(self.cfg, pve_ca_file=''))
        with self.assertRaises(k.APIError) as raised:
            client.inventory()
        self.assertIn('verify', str(raised.exception))

    def test_16_no_redirect_with_secret(self):
        type(self).fixture_mode = 'redirect'
        code, body = self.request('/api/pve/vms')
        self.assertEqual(code, 502)
        self.assertNotIn(self.fixture_secret, str(body))
        self.assertEqual(len(self.calls), 1)

    def test_17_error_redaction(self):
        type(self).fixture_mode = 'denied'
        code, body = self.request('/api/pve/vms')
        self.assertEqual(code, 502)
        self.assertNotIn(self.fixture_secret, json.dumps(body))

    def test_18_browser_challenge_one_use(self):
        c = self.request('/api/challenge', {})[1]
        x = int(c['nonce'], 16)
        for _ in range(c['rounds']):
            x ^= x >> 16
            x = (x * 0x7feb352d) & 0xffffffff
            x ^= x >> 15
            x = (x * 0x846ca68b) & 0xffffffff
            x ^= x >> 16
        body = {'id': c['id'], 'result': f'{x:08x}'}
        self.assertEqual(self.request('/api/anchor', body)[0], 200)
        self.assertEqual(self.request('/api/anchor', body)[0], 409)

    def test_19_private_files_and_receipt_hash(self):
        self.assertEqual((self.root / 'owner.key').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'evidence.jsonl').stat().st_mode & 0o777, 0o600)
        for event in self.request('/api/evidence')[1]['events']:
            digest = event.pop('sha256')
            self.assertEqual(digest, hashlib.sha256(k.encode(event)).hexdigest())

    def test_20_missing_proxmox_is_not_live(self):
        app = k.App(dict(k.DEFAULTS), self.root)
        try:
            self.assertEqual(app.status()['proxmox'], 'NOT_CONFIGURED')
            with self.assertRaises(k.APIError):
                app.pve.inventory()
        finally:
            app.pool.shutdown()

    def test_21_nonlocal_http_refused(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith('PVE_')}
        env['KLONE_HOME'] = str(self.root / 'remote-test')
        proc = subprocess.run([sys.executable, str(SOURCE), 'up', '--bind', '0.0.0.0'], env=env, capture_output=True, timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b'requires --tls-cert', proc.stderr)

    def test_22_busy_second_port_aborts_startup(self):
        with socket.socket() as occupied, socket.socket() as free:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            free.bind(('127.0.0.1', 0))
            port = free.getsockname()[1]
            free.close()
            env = {key: value for key, value in os.environ.items() if not key.startswith('PVE_')}
            env['KLONE_HOME'] = str(self.root / 'busy-test')
            proc = subprocess.run([sys.executable, str(SOURCE), 'up', '--port', str(port), '--cloud-port', str(occupied.getsockname()[1])],
                                  env=env, capture_output=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'Address already in use', proc.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
