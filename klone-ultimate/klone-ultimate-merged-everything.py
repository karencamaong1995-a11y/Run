#!/usr/bin/env python3
"""KLTECH Klone Ultimate 1.0.0 — portable controller, Python 3.10+ stdlib."""
from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

VERSION = '1.0.0'
DEFAULTS = {'bind': '127.0.0.1', 'port': 8420, 'cloud_port': 8423,
            'pve_host': '', 'pve_token': '', 'pve_node': '', 'pve_ca_file': '',
            'allowed_vmids': [], 'tls_cert': '', 'tls_key': ''}


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def private_write(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + secrets.token_hex(6))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def state_dir():
    return Path(os.environ.get('KLONE_HOME', str(Path.home() / '.klone-ultimate'))).expanduser().resolve()


def config_load(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    path = root / 'config.json'
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError('config.json must be a private regular file (chmod 600).')
        cfg.update(json.loads(path.read_text()))
    for key in ('HOST', 'TOKEN', 'NODE', 'CA_FILE'):
        if 'PVE_' + key in os.environ:
            cfg['pve_' + key.lower()] = os.environ['PVE_' + key]
    if 'PVE_ALLOWED_VMIDS' in os.environ:
        cfg['allowed_vmids'] = [int(x) for x in os.environ['PVE_ALLOWED_VMIDS'].split(',') if x.strip()]
    return cfg


def owner_key(root):
    path = root / 'owner.key'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32) + '\n')
    except FileExistsError:
        pass
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('owner.key must be a private regular file (chmod 600).')
    key = path.read_text().strip()
    if len(key) < 32:
        raise ValueError('Owner key is too short.')
    return key


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise APIError('Proxmox redirected the request; check the configured host.', 502)


class Proxmox:
    def __init__(self, cfg):
        self.cfg = cfg
        self.host = cfg['pve_host'].rstrip('/')
        self.token = cfg['pve_token']
        self.node = cfg['pve_node']
        self.allowed = {int(v) for v in cfg['allowed_vmids']}
        self.configured = bool(self.host and self.token and self.node)
        if any((self.host, self.token, self.node)) and not self.configured:
            raise ValueError('Set PVE_HOST, PVE_TOKEN and PVE_NODE together, or run configure.')
        if self.configured:
            u = urlsplit(self.host)
            if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
                raise ValueError('PVE_HOST must be an HTTPS origin, such as https://pve.example:8006')
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', self.node):
                raise ValueError('Invalid Proxmox node name.')
            if not re.fullmatch(r'[^\s=!]+@[^\s=!]+![^\s=]+=[^\s]+', self.token):
                raise ValueError('PVE_TOKEN must have the format user@realm!tokenid=secret.')
        context = ssl.create_default_context(cafile=cfg['pve_ca_file'] or None)
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    def request(self, path, payload=None):
        if not self.configured:
            raise APIError('Proxmox is not configured. Run the configure command on the host.', 503)
        req = Request(self.host + '/api2/json' + path,
                      data=None if payload is None else encode(payload),
                      headers={'Authorization': 'PVEAPIToken=' + self.token,
                               'Content-Type': 'application/json', 'Accept': 'application/json'},
                      method='GET' if payload is None else 'POST')
        try:
            with self.opener.open(req, timeout=12) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise APIError('Proxmox response exceeded the size limit.', 502)
            value = json.loads(raw)
            if not isinstance(value, dict) or 'data' not in value:
                raise ValueError()
            return value['data']
        except HTTPError as e:
            raise APIError('Proxmox HTTP %s. Check token permissions, VM state and task logs.' % e.code, 502) from None
        except (URLError, TimeoutError, ssl.SSLError, OSError):
            raise APIError('Cannot reach or verify Proxmox. Check its address, trusted CA and network route.', 502) from None
        except (ValueError, UnicodeError):
            raise APIError('Proxmox returned an invalid response.', 502) from None

    def vm_path(self, vmid):
        if type(vmid) is not int or not 100 <= vmid <= 999999999:
            raise APIError('VM ID must be an integer between 100 and 999999999.')
        return '/nodes/' + quote(self.node, safe='') + '/qemu/' + str(vmid)

    def allowed_vm(self, vmid):
        path = self.vm_path(vmid)
        if vmid not in self.allowed:
            raise APIError('VM ID is outside the configured allowlist. Run configure to change it.', 403)
        return path

    def inventory(self):
        value = self.request('/nodes/' + quote(self.node, safe='') + '/qemu')
        if not isinstance(value, list):
            raise APIError('Unexpected Proxmox inventory response.', 502)
        fields = ('vmid', 'name', 'status', 'cpus', 'mem', 'maxmem', 'disk', 'maxdisk', 'uptime', 'template')
        return [{**{k: v.get(k) for k in fields}, 'control_allowed': v.get('vmid') in self.allowed}
                for v in value if isinstance(v, dict)]

    def action(self, body):
        vmid, action = body.get('vmid'), body.get('action')
        path = self.allowed_vm(vmid)
        if action not in ('start', 'shutdown', 'stop', 'clone'):
            raise APIError('Supported actions: start, shutdown, stop, clone.')
        expected = action.upper() + ' ' + str(vmid)
        payload = {}
        if action == 'clone':
            newid = body.get('newid')
            self.allowed_vm(newid)
            if newid == vmid:
                raise APIError('Clone ID must differ from the source.')
            expected += ' TO ' + str(newid)
            name = body.get('name', 'klone-' + str(newid))
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,62}', name):
                raise APIError('Clone name must be a short hostname beginning with a letter.')
            payload = {'newid': newid, 'name': name, 'full': 1}
        if body.get('confirm') != expected:
            raise APIError('Confirmation required: ' + expected, 409)
        if action == 'clone':
            source = self.request(path + '/config')
            if not source.get('template'):
                raise APIError('Clone source must be a prepared Proxmox VM template.', 409)
            if any(v['vmid'] == body['newid'] for v in self.inventory()):
                raise APIError('Target VM ID already exists.', 409)
        result = self.request(path + ('/clone' if action == 'clone' else '/status/' + action), payload)
        if not isinstance(result, str) or not result.startswith('UPID:'):
            raise APIError('Request sent but no task ID was returned. Inspect Proxmox before retrying.', 502)
        return {'status': 'ACCEPTED', 'upid': result, 'action': action, 'vmid': vmid,
                **({'newid': body['newid']} if action == 'clone' else {}),
                'note': 'Task accepted. Check task completion and VM state; guest application health is separate.'}

    def task(self, upid):
        if not isinstance(upid, str) or not upid.startswith('UPID:' + self.node + ':') or len(upid) > 512:
            raise APIError('Invalid task ID for the configured node.')
        value = self.request('/nodes/' + quote(self.node, safe='') + '/tasks/' + quote(upid, safe='') + '/status')
        state = 'RUNNING'
        if value.get('status') == 'stopped':
            state = 'SUCCEEDED' if value.get('exitstatus') == 'OK' else 'FAILED'
        return {'status': state, 'task': value}


class App:
    def __init__(self, cfg, root):
        self.cfg, self.root = cfg, root
        self.key = owner_key(root)
        self.pve = Proxmox(cfg)
        self.lock = threading.RLock()
        self.power_lock = threading.Lock()
        self.jobs = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.started = time.monotonic()
        self.challenges = {}
        self.events = []
        self.audit = root / 'evidence.jsonl'
        if self.audit.exists():
            for line in self.audit.read_text().splitlines()[-100:]:
                try:
                    self.events.append(json.loads(line))
                except ValueError:
                    pass

    def receipt(self, kind, data):
        event = {'time': now(), 'kind': kind, 'data': data, 'version': VERSION}
        event['sha256'] = hashlib.sha256(encode(event)).hexdigest()
        with self.lock:
            # A digest detects accidental modification; it is not a hardware attestation.
            if self.audit.exists() and self.audit.stat().st_size > 2_000_000:
                os.replace(self.audit, self.root / 'evidence.previous.jsonl')
            fd = os.open(self.audit, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, 'a') as f:
                f.write(json.dumps(event) + '\n')
                f.flush()
                os.fsync(f.fileno())
            self.events = (self.events + [event])[-100:]
        return event

    def status(self):
        available = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()
        return {'version': VERSION, 'time': now(), 'controller': 'RUNNING',
                'uptime_seconds': round(time.monotonic() - self.started, 2),
                'host': {'os': platform.system(), 'architecture': platform.machine(),
                         'logical_cpus_visible': os.cpu_count(), 'cpu_affinity_count': available,
                         'python': platform.python_version(), 'kvm_device_present': Path('/dev/kvm').exists(),
                         'note': 'Values describe the controller environment. Container quotas may further limit compute.'},
                'proxmox': 'CONFIGURED_UNCHECKED' if self.pve.configured else 'NOT_CONFIGURED',
                'node': self.pve.node, 'allowed_vmids': sorted(self.pve.allowed),
                'workload_execution': 'controller host', 'physical_clone_verified': False,
                'api_port': self.cfg['cloud_port']}

    def submit(self, body):
        rounds = body.get('rounds', 200000)
        if type(rounds) is not int or not 1000 <= rounds <= 1000000:
            raise APIError('rounds must be an integer from 1000 to 1000000.')
        with self.lock:
            if any(j['status'] in ('QUEUED', 'RUNNING') for j in self.jobs.values()):
                raise APIError('A workload is already running.', 409)
            job = {'id': secrets.token_hex(12), 'status': 'QUEUED', 'rounds': rounds, 'created': now()}
            self.jobs = dict(list(self.jobs.items())[-49:])
            self.jobs[job['id']] = job
            self.pool.submit(self.work, job['id'])
            return dict(job)

    def work(self, jobid):
        with self.lock:
            job = self.jobs[jobid]
            job['status'] = 'RUNNING'
        try:
            seed = secrets.token_bytes(32)
            value = seed
            started = time.perf_counter()
            for _ in range(job['rounds']):
                value = hashlib.sha256(value).digest()
            elapsed = time.perf_counter() - started
            result = {'id': jobid, 'rounds': job['rounds'], 'seed_hex': seed.hex(),
                      'result_sha256': value.hex(), 'elapsed_seconds': elapsed,
                      'hashes_per_second': job['rounds'] / elapsed,
                      'executor': 'controller-host-process', 'algorithm': 'sha256-chain-v1',
                      'evidence_limit': 'Measured process workload; no device identity or additional hardware claim.'}
            self.receipt('local_workload', result)
            with self.lock:
                job.update(status='SUCCEEDED', result=result)
        except Exception:
            with self.lock:
                job.update(status='FAILED', error='Workload or evidence write failed.')

    def challenge(self):
        with self.lock:
            self.challenges = {k: v for k, v in self.challenges.items() if v['expires'] > time.monotonic()}
            if len(self.challenges) >= 16:
                raise APIError('Too many pending challenges.', 429)
            item = {'id': secrets.token_hex(16), 'nonce': secrets.token_hex(4), 'rounds': 100000}
            self.challenges[item['id']] = {**item, 'expires': time.monotonic() + 300}
            return item

    def anchor(self, body):
        with self.lock:
            c = self.challenges.pop(str(body.get('id', '')), None)
        if not c or c['expires'] < time.monotonic():
            raise APIError('Challenge expired, missing or already used.', 409)
        x = int(c['nonce'], 16)
        for _ in range(c['rounds']):
            x ^= x >> 16
            x = (x * 0x7feb352d) & 0xffffffff
            x ^= x >> 15
            x = (x * 0x846ca68b) & 0xffffffff
            x ^= x >> 16
        if not secrets.compare_digest(f'{x:08x}', str(body.get('result', ''))):
            raise APIError('Incorrect challenge response.', 409)
        return self.receipt('browser_challenge', {'id': c['id'], 'verdict': 'RESPONSE_VERIFIED',
            'evidence_limit': 'A client answered a fresh workload challenge. Phone model and physical identity are unverified.'})


HTML = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KLTECH · Klone Control</title><style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#09111d;color:#e5eef8}*{box-sizing:border-box}body{margin:0}main{max-width:1080px;margin:auto;padding:30px 20px 60px}header{display:flex;justify-content:space-between;gap:20px;align-items:center}.brand{font-size:12px;letter-spacing:3px;color:#72e4be}h1{font-size:clamp(30px,6vw,52px);margin:12px 0}h2{font-size:21px;margin-top:0}p,.muted{color:#a8b8cc;line-height:1.6}.tag{border:1px solid #34546c;border-radius:30px;padding:8px 14px;font-size:12px;white-space:nowrap}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}article,.panel{border:1px solid #263b50;background:#0e1b2c;padding:22px;border-radius:16px;margin:16px 0}.value{font-size:25px;margin:10px 0}.label{font-size:12px;letter-spacing:1px;color:#9db2c8}input,button{font:inherit;border-radius:9px;padding:12px;border:1px solid #34506a}input{background:#081320;color:white;max-width:100%;width:100%;margin:6px 0}button{background:#83edc7;color:#09271e;cursor:pointer;font-weight:650}button.secondary{background:#183149;color:#dcecff}button.danger{background:#5d2430;color:#ffe9ec}button:disabled{opacity:.5;cursor:wait}nav{display:flex;gap:8px;flex-wrap:wrap;margin:24px 0}nav button{background:#132439;color:#aabbcd}nav button.active{background:#83edc7;color:#09271e}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.row input{flex:1;min-width:170px}.vm{padding:16px 0;border-top:1px solid #263b50}.vm .row{margin-top:12px}.ok{color:#83edc7}.bad{color:#ffa9aa}pre{white-space:pre-wrap;word-break:break-word;max-height:480px;overflow:auto;font-size:12px;background:#070e17;border:1px solid #263b50;padding:16px;border-radius:10px}[hidden]{display:none!important}code{font-size:.9em}#notice{min-height:26px;line-height:1.5}label{display:block;font-size:14px}.small{font-size:12px}</style></head><body><main>
<header><div><div class="brand">KLTECH · OWNER CONTROL</div><h1>Klone Control</h1></div><span class="tag" id="connection">Locked</span></header>
<p>Connect your host. Run a workload. Inspect the evidence.</p>
<section id="login" class="panel"><h2>Open your controller</h2><p>Enter the owner key generated on the computer running this service.</p><label for="owner">Owner key</label><div class="row"><input id="owner" type="password" autocomplete="off" placeholder="Owner key"><button id="unlock">Unlock</button></div></section>
<div id="notice" role="status"></div><div id="content" hidden>
<nav aria-label="Controller sections"><button class="active" data-tab="overview">Overview</button><button data-tab="machines">Virtual machines</button><button data-tab="cloud">Cloud API</button><button data-tab="evidence">Evidence</button><button id="lock">Lock</button></nav>
<section id="overview"><div class="grid"><article><div class="label">CONTROLLER</div><div class="value ok" id="hoststate">—</div><div class="muted" id="hostinfo"></div></article><article><div class="label">PROXMOX</div><div class="value" id="pvestate">Unchecked</div><div class="muted" id="node"></div></article><article><div class="label">COMPUTE VISIBLE</div><div class="value" id="cpus">—</div><div class="muted">Logical CPUs visible to this process; quotas may apply.</div></article></div>
<div class="grid"><article><h2>Measure a workload</h2><p>Run 200,000 SHA-256 operations on the controller host and save the measured result.</p><button id="work">Run workload</button></article><article><h2>Phone challenge</h2><p>Complete a fresh challenge in this browser. This checks the response; device identity remains unverified.</p><button class="secondary" id="anchor">Run browser challenge</button></article></div></section>
<section id="machines" hidden><div class="panel"><div class="row"><h2>Proxmox virtual machines</h2><button class="secondary" id="refresh">Refresh</button></div><p>Controls apply to the VM IDs allowed in your host configuration. Shutdown requests a graceful exit. Force stop interrupts the VM.</p><div id="vms">Refresh to check the connection.</div></div><div class="panel"><h2>Clone a prepared template</h2><p>Creates a full VM copy on the configured Proxmox node. Source and destination IDs must be allowed. Disk space and CPU come from that host.</p><div class="row"><label>Source template ID<input id="source" type="number" min="100"></label><label>New VM ID<input id="target" type="number" min="100"></label><label>New name<input id="vmname" placeholder="klone-101"></label><button id="clone">Create clone</button></div></div></section>
<section id="cloud" hidden><div class="panel"><h2>Your local Cloud API</h2><p>The second listener exposes the same authenticated API at port <strong id="apiport">8423</strong>. Requests run on this controller or the configured Proxmox host.</p><pre>GET  /health                  Service liveness, no credentials
GET  /api/status              Controller observations
GET  /api/pve/vms             Live VM inventory
POST /api/pve/action          start / shutdown / stop / clone
GET  /api/pve/task?upid=…     Proxmox task completion
POST /api/jobs                Run bounded SHA-256 workload
GET  /api/jobs                Workload state and measurements
GET  /api/evidence            Recent receipts

Authentication: Authorization: Bearer OWNER_KEY</pre><p class="small">Private access by default. Use an SSH tunnel or configure HTTPS to connect from another device.</p></div></section>
<section id="evidence" hidden><div class="panel"><div class="row"><h2>Observed results</h2><button id="receipts" class="secondary">Refresh evidence</button></div><p>Receipts record local workloads, browser responses and Proxmox requests. Accepted tasks require a completion check. Receipt hashes are not hardware attestations.</p></div></section>
<h2>Activity</h2><pre id="output">Ready.</pre></div><p class="small muted">KLTECH · Portable controller v1.0.0 · Physical hardware equivalence is unverified.</p></main><script>
let key='',timer=null;const $=id=>document.getElementById(id);function note(s,bad=false){$('notice').textContent=s;$('notice').className=bad?'bad':'ok'}
async function api(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Authorization':'Bearer '+key,'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});const j=await r.json();if(!r.ok)throw Error(j.error||'Request failed');return j}
function show(j){$('output').textContent=JSON.stringify(j,null,2)}
async function run(fn){try{await fn()}catch(e){note(e.message,true)}}
async function loadStatus(){const s=await api('/api/status');$('hoststate').textContent=s.controller;$('hostinfo').textContent=s.host.os+' · '+s.host.architecture;$('cpus').textContent=s.host.cpu_affinity_count;$('pvestate').textContent=s.proxmox==='NOT_CONFIGURED'?'Not configured':'Ready to check';$('node').textContent=s.node||'Run configure on the host to connect.';$('apiport').textContent=s.api_port;return s}
$('unlock').onclick=()=>run(async()=>{key=$('owner').value;await loadStatus();$('owner').value='';$('login').hidden=true;$('content').hidden=false;$('connection').textContent='Owner connected';note('Controller connected.');});$('owner').onkeydown=e=>{if(e.key==='Enter')$('unlock').click()};
$('lock').onclick=()=>{key='';clearTimeout(timer);$('content').hidden=true;$('login').hidden=false;$('connection').textContent='Locked';$('output').textContent='Ready.';$('vms').replaceChildren();note('Locked.')};
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('active',x===b));['overview','machines','cloud','evidence'].forEach(id=>$(id).hidden=id!==b.dataset.tab)});
async function inventory(){const j=await api('/api/pve/vms');$('pvestate').textContent='Connected';$('vms').replaceChildren();if(!j.vms.length)$('vms').textContent='No VMs visible with this token.';j.vms.forEach(v=>{const box=document.createElement('div');box.className='vm';const label=document.createElement('strong');label.textContent=v.vmid+' · '+(v.name||'Unnamed')+' · '+v.status+(v.template?' · template':'');box.append(label);const row=document.createElement('div');row.className='row';if(v.control_allowed&&!v.template){[['start','Start'],['shutdown','Shutdown'],['stop','Force stop']].forEach(([action,title])=>{const b=document.createElement('button');b.textContent=title;b.className=action==='stop'?'danger':'secondary';b.onclick=()=>power({action,vmid:v.vmid});row.append(b)})}else{row.textContent=v.template?'Prepared template':'Control disabled for this VM ID'}box.append(row);$('vms').append(box)});show(j);note('Live inventory received from Proxmox.');}
$('refresh').onclick=()=>run(inventory);
async function track(upid){if(!key)return;try{const j=await api('/api/pve/task?upid='+encodeURIComponent(upid));show(j);note('Proxmox task: '+j.status,j.status==='FAILED');if(j.status==='RUNNING')timer=setTimeout(()=>track(upid),2000)}catch(e){note(e.message,true)}}
function power(b){run(async()=>{const phrase=b.action.toUpperCase()+' '+b.vmid+(b.action==='clone'?' TO '+b.newid:'');const value=prompt('Type '+phrase+' to confirm this Proxmox action.');if(value!==phrase){note('Action cancelled.');return}const j=await api('/api/pve/action',{...b,confirm:value});show(j);note('Proxmox accepted the request; checking task completion.');clearTimeout(timer);timer=setTimeout(()=>track(j.upid),1000)})}
$('clone').onclick=()=>power({action:'clone',vmid:Number($('source').value),newid:Number($('target').value),name:$('vmname').value||'klone-'+$('target').value});
$('work').onclick=()=>run(async()=>{const j=await api('/api/jobs',{rounds:200000});show(j);note('Workload submitted.');clearTimeout(timer);async function poll(){if(!key)return;try{const all=await api('/api/jobs');const job=all.jobs.find(x=>x.id===j.id);show(job);if(['QUEUED','RUNNING'].includes(job.status))timer=setTimeout(poll,500);else note('Workload '+job.status,job.status==='FAILED')}catch(e){note(e.message,true)}}timer=setTimeout(poll,500)});
$('anchor').onclick=()=>run(async()=>{const c=await api('/api/challenge',{});let x=parseInt(c.nonce,16)>>>0;for(let i=0;i<c.rounds;i++){x^=x>>>16;x=Math.imul(x,0x7feb352d)>>>0;x^=x>>>15;x=Math.imul(x,0x846ca68b)>>>0;x^=x>>>16}show(await api('/api/anchor',{id:c.id,result:(x>>>0).toString(16).padStart(8,'0')}));note('Fresh challenge response verified.');});
$('receipts').onclick=()=>run(async()=>show(await api('/api/evidence')));
</script></body></html>'''


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(16)
        self.tls_context = None
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        if self.tls_context:
            connection = self.tls_context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False)
        return connection, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Klone/' + VERSION
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass  # Tokens, request URLs and private VM names are not logged.

        def send(self, value, code=200, html=False):
            data = value.encode() if html else encode(value)
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (OSError, ssl.SSLError):
                pass

        def authorized(self):
            return secrets.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + app.key).encode())

        def body(self):
            if self.headers.get('Transfer-Encoding'):
                raise APIError('Transfer-Encoding is unsupported.', 400)
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise APIError('Invalid Content-Length.')
            if not 0 < length <= 8192:
                raise APIError('JSON body must contain 1 to 8192 bytes.', 413)
            if self.headers.get_content_type() != 'application/json':
                raise APIError('Content-Type must be application/json.', 415)
            try:
                value = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise APIError('Invalid JSON.')
            if not isinstance(value, dict):
                raise APIError('JSON body must be an object.')
            return value

        def dispatch(self, post=False):
            try:
                path = urlsplit(self.path).path
                if not post and path == '/':
                    return self.send(HTML, html=True)
                if not post and path == '/health':
                    return self.send({'status': 'ok', 'version': VERSION})
                if not self.authorized():
                    return self.send({'error': 'Owner authentication required.'}, 401)
                if post:
                    # Browser calls stay on the same origin. CLI calls have no Origin.
                    origin = self.headers.get('Origin')
                    scheme = 'https' if isinstance(self.connection, ssl.SSLSocket) else 'http'
                    if origin and origin != scheme + '://' + self.headers.get('Host', ''):
                        raise APIError('Cross-origin requests are blocked.', 403)
                    body = self.body()
                    if path == '/api/jobs':
                        return self.send(app.submit(body), 202)
                    if path == '/api/challenge':
                        return self.send(app.challenge())
                    if path == '/api/anchor':
                        return self.send(app.anchor(body))
                    if path == '/api/pve/action':
                        if not app.power_lock.acquire(blocking=False):
                            raise APIError('Another VM request is in progress.', 409)
                        try:
                            # Persist intent before sending a potentially consequential request.
                            app.receipt('proxmox_request', {k: body.get(k) for k in ('action', 'vmid', 'newid')})
                            try:
                                value = app.pve.action(body)
                            except APIError as e:
                                app.receipt('proxmox_request_error', {'error': str(e), 'note': 'Check host state before retrying a timed-out request.'})
                                raise
                            app.receipt('proxmox_task_accepted', value)
                            return self.send(value, 202)
                        finally:
                            app.power_lock.release()
                else:
                    if path == '/api/status':
                        return self.send(app.status())
                    if path == '/api/jobs':
                        with app.lock:
                            return self.send({'jobs': list(app.jobs.values())})
                    if path == '/api/evidence':
                        with app.lock:
                            return self.send({'events': app.events})
                    if path == '/api/pve/vms':
                        return self.send({'status': 'CONNECTED', 'time': now(), 'vms': app.pve.inventory()})
                    if path == '/api/pve/task':
                        upid = parse_qs(urlsplit(self.path).query).get('upid', [''])[0]
                        result = app.pve.task(upid)
                        if result['status'] != 'RUNNING':
                            with app.lock:
                                recorded = any(e['kind'] == 'proxmox_task_complete' and e['data'].get('upid') == upid for e in app.events)
                            if not recorded:
                                app.receipt('proxmox_task_complete', {'upid': upid, **result})
                        return self.send(result)
                raise APIError('Not found.', 404)
            except APIError as e:
                self.send({'error': str(e)}, e.status)
            except (TimeoutError, socket.timeout):
                self.send({'error': 'Request timed out.'}, 408)
            except Exception:
                self.send({'error': 'Internal operation failed. Check configuration and storage permissions.'}, 500)

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch(True)
    return Handler


def configure(root):
    cfg = config_load(root)
    print('Proxmox connection. Secrets are stored locally with mode 600.')
    for key, label in [('pve_host', 'HTTPS Proxmox origin'), ('pve_node', 'Node name'),
                       ('pve_ca_file', 'Trusted CA PEM path (blank uses system trust)')]:
        cfg[key] = input(label + ' [' + cfg[key] + ']: ').strip() or cfg[key]
    cfg['pve_token'] = getpass.getpass('API token user@realm!tokenid=secret (blank keeps existing): ').strip() or cfg['pve_token']
    ids = input('VM IDs allowed for power/clone actions, comma separated [' + ','.join(map(str, cfg['allowed_vmids'])) + ']: ').strip()
    if ids:
        cfg['allowed_vmids'] = [int(x) for x in ids.split(',')]
    Proxmox(cfg)
    private_write(root / 'config.json', json.dumps(cfg, indent=2) + '\n')
    print('Saved. Restart the controller, then refresh Virtual machines.')


def serve(cfg, root):
    if cfg['port'] == cfg['cloud_port']:
        raise ValueError('Controller and Cloud API ports must differ.')
    if any(type(cfg[k]) is not int or not 1 <= cfg[k] <= 65535 for k in ('port', 'cloud_port')):
        raise ValueError('Ports must be integers from 1 to 65535.')
    loopback = cfg['bind'] in ('localhost', '127.0.0.1')
    if not loopback and not (cfg['tls_cert'] and cfg['tls_key']):
        raise ValueError('Non-loopback access requires --tls-cert and --tls-key. An SSH tunnel also works.')
    context = None
    if cfg['tls_cert'] or cfg['tls_key']:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cfg['tls_cert'], cfg['tls_key'])
    app = App(cfg, root)
    servers = []
    try:
        # Bind both before serving: partial startup must fail clearly.
        for port in (cfg['port'], cfg['cloud_port']):
            server = Server((cfg['bind'], port), handler(app))
            if context:
                server.tls_context = context
            servers.append(server)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        scheme = 'https' if context else 'http'
        print('KLTECH Klone Ultimate ' + VERSION, flush=True)
        print('Controller: %s://%s:%s' % (scheme, cfg['bind'], cfg['port']), flush=True)
        print('Cloud API:  %s://%s:%s' % (scheme, cfg['bind'], cfg['cloud_port']), flush=True)
        print('Owner key file: ' + str(root / 'owner.key'), flush=True)
        print('Proxmox: ' + ('configured; connection unchecked' if app.pve.configured else 'NOT CONFIGURED'), flush=True)
        stop.wait()
        for server in servers:
            server.shutdown()
    finally:
        for server in servers:
            server.server_close()
        app.pool.shutdown(wait=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['up', 'configure', 'key', 'doctor'], nargs='?', default='up')
    parser.add_argument('--bind')
    parser.add_argument('--port', type=int)
    parser.add_argument('--cloud-port', type=int)
    parser.add_argument('--tls-cert')
    parser.add_argument('--tls-key')
    args = parser.parse_args()
    root = state_dir()
    cfg = config_load(root)
    for key in ('bind', 'port', 'cloud_port', 'tls_cert', 'tls_key'):
        if getattr(args, key) is not None:
            cfg[key] = getattr(args, key)
    if args.command == 'configure':
        configure(root)
    elif args.command == 'key':
        print(owner_key(root))
    elif args.command == 'doctor':
        app = App(cfg, root)
        result = app.status()
        result['controller'] = 'DIAGNOSTIC_ONLY'
        if app.pve.configured:
            try:
                result['proxmox'] = 'CONNECTED'
                result['vms'] = app.pve.inventory()
            except APIError as e:
                result.update(proxmox='ERROR', error=str(e))
        print(json.dumps(result, indent=2))
        app.pool.shutdown()
        return 1 if result['proxmox'] == 'ERROR' else 0
    else:
        serve(cfg, root)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as e:
        print('Klone startup error: ' + str(e), file=sys.stderr)
        sys.exit(1)
