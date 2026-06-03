#!/usr/bin/env python3
"""
HELIOS 3D Model viewer server — port 8765
Serves static files + API endpoints.
"""

import http.server, json, os, socketserver, subprocess, sys, threading, urllib.request, urllib.error

# Element symbols and element_symbol() — imported from build_reaction.py (single source of truth)
# Add parent dir to path once so build_reaction and build_geometry are importable
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)
from build_reaction import ELEMENT_SYMBOLS, element_symbol
from build_geometry import parse_detgeo, parse_reaction_config
def sym_for_Z(Z):
    return element_symbol(int(Z))

# ── Mass table cache — parsed once at startup, reused for all /api/mass calls ──
_MASS_CACHE = None
_MASS_LOCK  = __import__('threading').Lock()
_MASS_PATH  = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'mass20.txt'))

def _get_masses():
    """Return cached AME mass dict, parsing the table on first call. Thread-safe. Returns None if missing."""
    global _MASS_CACHE
    if _MASS_CACHE is not None:  # fast path — no lock needed after first load
        return _MASS_CACHE
    with _MASS_LOCK:
        if _MASS_CACHE is not None:  # re-check inside lock
            return _MASS_CACHE
        if not os.path.exists(_MASS_PATH):
            return None
        from build_reaction import parse_mass_table
        _MASS_CACHE = parse_mass_table(_MASS_PATH)
    return _MASS_CACHE

PORT = 8765
DIR  = os.path.dirname(os.path.abspath(__file__))

# Paths to digios working files
DIGIOS_WORKING = os.path.expanduser('~/digios/analysis/working')
HELIOS_REACTION_JSON = os.path.join(os.path.dirname(DIR), 'helios_reaction.json')
MCP_JSON            = os.path.join(os.path.dirname(DIR), 'mcp.json')
DEFAULT_NDS_URL     = 'http://192.168.203.75:65432/sse'
BUILD_REACTION_PY    = os.path.join(os.path.dirname(DIR), 'build_reaction.py')
DETECTOR_GEO   = os.path.join(DIGIOS_WORKING, 'detectorGeo.txt')
REACTION_CFG   = os.path.join(DIGIOS_WORKING, 'reactionConfig.txt')
BUILD_GEO_PY   = os.path.join(os.path.dirname(DIR), 'build_geometry.py')
GEO_JSON       = os.path.join(os.path.dirname(DIR), 'helios_geometry.json')

def ensure_default_files():
    """On startup: check config_source in existing JSONs and act accordingly.

    Rules:
      - JSON missing               → try digios; if unavailable write hardcoded defaults (manual)
      - JSON exists, source=digios → try to reload from digios; if that fails, leave file as-is
      - JSON exists, source=manual → leave untouched (user saved this deliberately)
      - JSON exists, source=default/missing → treat as digios (fresh clone)
    """
    import subprocess

    def _read_source(path):
        try:
            with open(path) as f:
                d = json.load(f)
                # default/missing config_source → treat as manual (conservative)
                return d.get('config_source', 'manual') or 'manual'
        except Exception:
            return None  # file missing or unreadable

    def _rebuild_geo_from_digios():
        """Try to build helios_geometry.json from detectorGeo.txt. Returns True on success."""
        if not (os.path.exists(DETECTOR_GEO) and os.path.exists(BUILD_GEO_PY)):
            return False
        r = subprocess.run([sys.executable, BUILD_GEO_PY, DETECTOR_GEO, GEO_JSON],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            # Stamp digios source
            try:
                with open(GEO_JSON) as f: gd = json.load(f)
                gd['config_source'] = 'digios'
                with open(GEO_JSON, 'w') as f: json.dump(gd, f, indent=2)
            except Exception: pass
            print('[server] helios_geometry.json loaded from detectorGeo.txt (digios)')
            return True
        print(f'[server] build_geometry.py failed: {r.stderr.strip()}')
        return False

    def _write_default_geo():
        """Write hardcoded geometry defaults, stamped manual."""
        default_geo = {
            "config_source": "manual",
            "Bfield": -2.85, "bore": 462.5, "perpDist": 11.5,
            "width": 10.0, "length": 50.0, "recoilPos": 350.0,
            "recoilInner": 10.0, "recoilOuter": 40.2,
            "recoilPos1": 0.0, "recoilPos2": 0.0,
            "elumPos1": 0.0, "elumPos2": 0.0, "blocker": 0.0,
            "firstPos": -100.0, "facing": "Out",
            "nDet": 6, "mDet": 4, "zMin": -394.5, "zMax": -100.0,
            "detectors": _make_default_detectors(),
        }
        with open(GEO_JSON, 'w') as f: json.dump(default_geo, f, indent=2)
        print('[server] helios_geometry.json created with built-in defaults (manual)')

    def _rebuild_rx_from_digios():
        """Try to build helios_reaction.json from reactionConfig.txt. Returns True on success."""
        if not os.path.exists(REACTION_CFG):
            return False
        try:
            rc = parse_reaction_config(REACTION_CFG)
            rxData = {
                'beam_A': rc.get('beam_A'), 'beam_Z': rc.get('beam_Z'),
                'target_A': rc.get('target_A'), 'target_Z': rc.get('target_Z'),
                'recoil_light_A': rc.get('recoil_light_A'), 'recoil_light_Z': rc.get('recoil_light_Z'),
                'beam_energy_MeVu': rc.get('beam_energy_MeVu'),
                'config_source': 'digios',
            }
            with open(HELIOS_REACTION_JSON, 'w') as f: json.dump(rxData, f, indent=2)
            if os.path.exists(BUILD_REACTION_PY):
                r = subprocess.run([sys.executable, BUILD_REACTION_PY, HELIOS_REACTION_JSON],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    print(f'[server] build_reaction.py failed: {r.stderr.strip()}')
                    return False
            print('[server] helios_reaction.json loaded from reactionConfig.txt (digios)')
            return True
        except Exception as e:
            print(f'[server] reaction config load failed: {e}')
            return False

    def _write_default_rx():
        """Write hardcoded reaction defaults, stamped manual."""
        default_rx = {
            "config_source": "manual",
            "beam_A": 32, "beam_Z": 14,
            "target_A": 2, "target_Z": 1,
            "recoil_light_A": 1, "recoil_light_Z": 1,
            "beam_energy_MeVu": 8.8,
            "beam_label": "32Si", "target_label": "2H",
            "recoil_light_label": "1H", "recoil_heavy_label": "33Si",
            "reaction_str": "32Si(2H,1H)33Si",
            "Q": None, "betaCM": None,
            "mass_b": None, "charge_b": 1,
            "mass_B": None, "charge_B": 14,
            "Ma": None, "Mt": None, "Ecm": None,
        }
        if os.path.exists(BUILD_REACTION_PY):
            with open(HELIOS_REACTION_JSON, 'w') as f: json.dump(default_rx, f, indent=2)
            r = subprocess.run([sys.executable, BUILD_REACTION_PY, HELIOS_REACTION_JSON],
                               capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                with open(HELIOS_REACTION_JSON, 'w') as f: json.dump(default_rx, f, indent=2)
        else:
            with open(HELIOS_REACTION_JSON, 'w') as f: json.dump(default_rx, f, indent=2)
        print('[server] helios_reaction.json created with built-in defaults (manual)')

    # ── helios_geometry.json ──────────────────────────────────────────────────
    geo_source = _read_source(GEO_JSON)
    if geo_source is None:                        # missing
        if not _rebuild_geo_from_digios():
            _write_default_geo()
    elif geo_source == 'digios':                  # exists, digios → refresh
        if not _rebuild_geo_from_digios():
            print('[server] helios_geometry.json: digios unavailable, keeping existing file')
    else:                                         # manual/default/unknown → leave untouched
        print(f'[server] helios_geometry.json: source={geo_source!r}, keeping as-is')

    # ── helios_reaction.json ──────────────────────────────────────────────────
    rx_source = _read_source(HELIOS_REACTION_JSON)
    if rx_source is None:                         # missing
        if not _rebuild_rx_from_digios():
            _write_default_rx()
    elif rx_source == 'digios':                   # exists, digios → refresh
        if not _rebuild_rx_from_digios():
            print('[server] helios_reaction.json: digios unavailable, keeping existing file')
    else:                                         # manual/default/unknown → leave untouched
        print(f'[server] helios_reaction.json: source={rx_source!r}, keeping as-is')


def _make_default_detectors():
    """Generate 6x4 upstream detector array with default offsets."""
    import math
    firstPos = -100.0
    length   = 50.0
    perpDist = 11.5
    pos_offsets = [0.0, 58.6, 117.9, 176.8, 235.8, 294.5]
    nDet, mDet = len(pos_offsets), 4
    # z_near for upstream: firstPos - offset (nearest det has offset=0)
    detectors = []
    for col_idx, offset in enumerate(pos_offsets):
        z_near   = round(firstPos - offset, 2)
        z_center = round(z_near - length / 2, 2)
        for row in range(mDet):
            phi = 2 * math.pi / mDet * row
            detectors.append({
                "id": col_idx * mDet + row,
                "row": row, "col": col_idx,
                "phi_deg": round(math.degrees(phi), 2),
                "z_near": z_near, "z_center": z_center, "z": z_center,
                "x": round(perpDist * math.cos(phi), 4),
                "y": round(perpDist * math.sin(phi), 4),
                "length": length, "width": 10.0,
            })
    return detectors


def read_mcp_config():
    """Read mcp.json, return dict with nds_url."""
    if os.path.exists(MCP_JSON):
        try:
            with open(MCP_JSON) as f:
                return json.load(f)
        except Exception:
            pass
    return {'nds_url': DEFAULT_NDS_URL}

def save_mcp_config(data):
    with open(MCP_JSON, 'w') as f:
        json.dump(data, f, indent=2)

def probe_nds(url, timeout=3):
    """Return True if the SSE endpoint responds with HTTP 200."""
    try:
        req = urllib.request.Request(url, headers={'Accept': 'text/event-stream'})
        # We just need the headers — close immediately
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False

def mcp_tool_call(nds_url, tool_name, arguments, timeout=15):
    """
    Run one MCP tool call over SSE transport.
    Returns parsed result dict, or raises RuntimeError on failure.
    """
    import time
    endpoint_path = None
    result_event  = threading.Event()
    messages      = []   # SSE message payloads
    sse_error     = []

    def sse_reader(resp):
        nonlocal endpoint_path
        event_type = None
        try:
            for raw in resp:
                line = raw.decode('utf-8').rstrip('\n\r')
                if line.startswith('event:'):
                    event_type = line[6:].strip()
                elif line.startswith('data:'):
                    data = line[5:].strip()
                    if event_type == 'endpoint':
                        endpoint_path = data
                        ready_evt.set()
                    elif event_type == 'message':
                        try:
                            msg = json.loads(data)
                            messages.append(msg)
                            # Only signal done when we have the tool result (id=1)
                            if msg.get('id') == 1:
                                result_event.set()
                        except Exception:
                            pass
                elif line == '':
                    event_type = None
                if result_event.is_set():
                    break
        except Exception as e:
            sse_error.append(str(e))
            ready_evt.set()
            result_event.set()

    ready_evt = threading.Event()

    # Open SSE stream in background thread
    base = nds_url.rsplit('/sse', 1)[0]
    req_sse = urllib.request.Request(nds_url, headers={'Accept': 'text/event-stream'})
    try:
        sse_resp = urllib.request.urlopen(req_sse, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f'SSE connect failed: {e}')

    t = threading.Thread(target=sse_reader, args=(sse_resp,), daemon=True)
    t.start()

    try:
        # Wait for session endpoint
        if not ready_evt.wait(timeout=5):
            raise RuntimeError('Timed out waiting for SSE endpoint')
        if sse_error:
            raise RuntimeError(sse_error[0])

        def post(payload):
            url = base + endpoint_path
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data,
                headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status

        # MCP handshake
        post({'jsonrpc':'2.0','id':0,'method':'initialize',
              'params':{'protocolVersion':'2024-11-05',
                        'capabilities':{},'clientInfo':{'name':'helios-model','version':'1.0'}}})
        time.sleep(0.1)
        post({'jsonrpc':'2.0','method':'notifications/initialized'})
        time.sleep(0.1)

        # Tool call
        post({'jsonrpc':'2.0','id':1,'method':'tools/call',
              'params':{'name': tool_name, 'arguments': arguments}})

        # Wait for result on SSE stream
        if not result_event.wait(timeout=10):
            raise RuntimeError('Timed out waiting for tool result')

        # Find the response with id=1
        for msg in messages:
            if msg.get('id') == 1:
                content = msg.get('result', {}).get('content', [])
                if content and content[0].get('type') == 'text':
                    return json.loads(content[0]['text'])
                if 'error' in msg:
                    raise RuntimeError(msg['error'].get('message', str(msg['error'])))
        raise RuntimeError('No result message received')
    finally:
        # Always close the SSE stream — unblocks the reader thread and releases the FD
        try: sse_resp.close()
        except Exception: pass
        # Give the reader a moment to exit cleanly
        t.join(timeout=1.0)





class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/helios_geometry.json':
            # Serve helios_geometry.json from root folder (no-cache: rebuilt by apply-geo)
            try:
                with open(GEO_JSON, 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(404); self.end_headers()

        elif self.path == '/api/mcp_config':
            self.send_json({'ok': True, **read_mcp_config()})

        elif self.path == '/api/nds/status':
            cfg = read_mcp_config()
            url = cfg.get('nds_url', DEFAULT_NDS_URL)
            reachable = probe_nds(url)
            self.send_json({'ok': True, 'reachable': reachable, 'nds_url': url})

        elif self.path.startswith('/api/nds/query'):
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            tool   = params.get('tool', [None])[0]
            args_s = params.get('args', ['{}'])[0]
            if not tool:
                self.send_json({'ok': False, 'error': 'Missing tool param'}, 400)
            else:
                try:
                    cfg = read_mcp_config()
                    url = cfg.get('nds_url', DEFAULT_NDS_URL)
                    arguments = json.loads(args_s)
                    result = mcp_tool_call(url, tool, arguments)
                    self.send_json({'ok': True, 'result': result})
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)

        elif self.path == '/api/data':
            # Placeholder for live EPICS data
            # Format: {"0": value, ...} keyed by detector ID
            self.send_json({})

        elif self.path == '/api/config':
            # Read digios files, update helios_geometry.json + helios_reaction.json, return data
            result = {'ok': True, 'errors': []}

            # 1. Read + save helios_geometry.json
            if os.path.exists(DETECTOR_GEO):
                try:
                    result['detGeo'] = parse_detgeo(DETECTOR_GEO)
                    # Save geometry JSON
                    r = subprocess.run(
                        [sys.executable, BUILD_GEO_PY, DETECTOR_GEO, GEO_JSON],
                        capture_output=True, text=True, timeout=10
                    )
                    if r.returncode == 0:
                        # Stamp config_source=digios into geometry JSON
                        try:
                            with open(GEO_JSON) as _gf: _gd = json.load(_gf)
                            _gd['config_source'] = 'digios'
                            with open(GEO_JSON, 'w') as _gf: json.dump(_gd, _gf, indent=2)
                        except Exception: pass
                    else:
                        result['errors'].append(f'rebuild_geo: {r.stderr.strip()}')
                except Exception as e:
                    result['errors'].append(f'detectorGeo: {e}')
            else:
                result['errors'].append(f'detectorGeo.txt not found at {DETECTOR_GEO}')

            # 2. Read reactionConfig + run build_reaction.py -> save helios_reaction.json
            if os.path.exists(REACTION_CFG):
                try:
                    rc = parse_reaction_config(REACTION_CFG)
                    result['reactionConfig'] = rc
                    # Save to helios_reaction.json
                    rxData = {
                        'beam_A': rc.get('beam_A'), 'beam_Z': rc.get('beam_Z'),
                        'target_A': rc.get('target_A'), 'target_Z': rc.get('target_Z'),
                        'recoil_light_A': rc.get('recoil_light_A'), 'recoil_light_Z': rc.get('recoil_light_Z'),
                        'beam_energy_MeVu': rc.get('beam_energy_MeVu'),
                        'config_source': 'digios',
                    }
                    with open(HELIOS_REACTION_JSON, 'w') as f:
                        json.dump(rxData, f, indent=2)
                    # Run build_reaction.py
                    r2 = subprocess.run(
                        [sys.executable, BUILD_REACTION_PY, HELIOS_REACTION_JSON],
                        capture_output=True, text=True, timeout=15
                    )
                    if r2.returncode == 0:
                        with open(HELIOS_REACTION_JSON) as f:
                            result['reaction'] = json.load(f)
                    else:
                        result['errors'].append(f'build_reaction: {r2.stderr.strip()}')
                except Exception as e:
                    result['errors'].append(f'reactionConfig: {e}')
            else:
                result['errors'].append(f'reactionConfig.txt not found at {REACTION_CFG}')

            self.send_json(result)

        elif self.path.startswith('/api/mass'):
            # Mass lookup from AME2020: /api/mass?AZ=32Si  or  /api/mass?A=32&Z=14
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            masses = _get_masses()
            if masses is None:
                self.send_json({'ok': False, 'error': 'mass20.txt not found'}, 404)
            else:
                try:
                    A = Z = None
                    if 'AZ' in params:
                        import re
                        az = params['AZ'][0].strip()
                        m = re.match(r'^(\d+)([A-Za-z]+)$', az)
                        if m:
                            sym = m.group(2)
                            # Case-sensitive: canonicalize to Title-case so 'N' != 'n'
                            sym_canon = sym if len(sym)==1 else sym[0].upper()+sym[1:].lower()
                            A,Z = int(m.group(1)), next((i for i,s in enumerate(ELEMENT_SYMBOLS) if s==sym_canon), None)
                    elif 'A' in params and 'Z' in params:
                        A,Z = int(params['A'][0]), int(params['Z'][0])
                    if A is None or Z is None:
                        self.send_json({'ok': False, 'error': 'Bad A/Z'})
                    else:
                        N = A - Z
                        mass = masses.get((Z,A))
                        mn = masses.get((0,1), 939.565)
                        mp = masses.get((1,1), 938.272)
                        m_alpha = masses.get((2,4), 3727.379)  # 4He nuclear mass
                        Sn = (masses.get((Z,A-1),0) + mn - mass)     if mass and A>1 else None
                        Sp = (masses.get((Z-1,A-1),0) + mp - mass)   if mass and Z>1 else None
                        Sa = (masses.get((Z-2,A-4),0) + m_alpha - mass) if mass and A>4 and Z>2 else None
                        self.send_json({'ok': True, 'A':A,'Z':Z,'N':N,
                            'mass': round(mass,4) if mass else None,
                            'name': f'{A}{element_symbol(Z)}',
                            'Sn': round(Sn,4) if Sn else None,
                            'Sp': round(Sp,4) if Sp else None,
                            'Sa': round(Sa,4) if Sa else None})
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)

        elif self.path == '/api/reaction_config':
            # GET: read helios_reaction.json
            if os.path.exists(HELIOS_REACTION_JSON):
                try:
                    with open(HELIOS_REACTION_JSON) as f:
                        data = json.load(f)
                    self.send_json({'ok': True, 'reaction': data})
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)
            else:
                self.send_json({'ok': False, 'error': 'helios_reaction.json not found'}, 404)

        elif self.path == '/api/build_reaction':
            # GET: run build_reaction.py using existing helios_reaction.json
            try:
                python = sys.executable
                r = subprocess.run(
                    [python, BUILD_REACTION_PY, HELIOS_REACTION_JSON],
                    capture_output=True, text=True, timeout=15
                )
                if r.returncode == 0:
                    with open(HELIOS_REACTION_JSON) as f:
                        data = json.load(f)
                    self.send_json({'ok': True, 'output': r.stdout.strip(), 'reaction': data})
                else:
                    self.send_json({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}, 500)
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        elif self.path.startswith('/api/rebuild_geo'):
            # Regenerate helios_geometry.json from detectorGeo.txt
            # Optional query params: firstPos, recoilPos passed as CLI args to build_geometry.py
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            try:
                cmd = [sys.executable, BUILD_GEO_PY, DETECTOR_GEO, GEO_JSON]
                if 'firstPos' in params:
                    cmd += ['--firstPos', params['firstPos'][0]]
                if 'recoilPos' in params:
                    cmd += ['--recoilPos', params['recoilPos'][0]]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    # Stamp config_source into the written geometry JSON
                    source = params.get('config_source', ['manual'])[0]
                    try:
                        with open(GEO_JSON) as _gf: _gd = json.load(_gf)
                        _gd['config_source'] = source
                        if 'Bfield' in params:
                            _gd['Bfield'] = float(params['Bfield'][0])
                        with open(GEO_JSON, 'w') as _gf: json.dump(_gd, _gf, indent=2)
                    except Exception: pass
                    self.send_json({'ok': True, 'output': r.stdout.strip()})
                else:
                    self.send_json({'ok': False, 'error': r.stderr.strip()}, 500)
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        else:
            super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        if self.path == '/api/mcp_config':
            try:
                data = json.loads(body)
                cfg  = read_mcp_config()
                cfg.update({k: v for k, v in data.items() if k in ('nds_url',)})
                save_mcp_config(cfg)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)
        elif self.path == '/api/reaction_config':
            # Save helios_reaction.json only (no build)
            try:
                data = json.loads(body)
                with open(HELIOS_REACTION_JSON, 'w') as f:
                    json.dump(data, f, indent=2)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)
        elif self.path == '/api/build_reaction':
            # POST: save body to helios_reaction.json + run build_reaction.py in one step
            try:
                data = json.loads(body)
                # Preserve or set config_source; caller may pass it explicitly
                if 'config_source' not in data:
                    data['config_source'] = 'manual'
                with open(HELIOS_REACTION_JSON, 'w') as f:
                    json.dump(data, f, indent=2)
                python = sys.executable
                r = subprocess.run(
                    [python, BUILD_REACTION_PY, HELIOS_REACTION_JSON],
                    capture_output=True, text=True, timeout=15
                )
                if r.returncode == 0:
                    with open(HELIOS_REACTION_JSON) as f:
                        result = json.load(f)
                    self.send_json({'ok': True, 'output': r.stdout.strip(), 'reaction': result})
                else:
                    self.send_json({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}, 500)
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        elif self.path == '/api/ptolemy':
            # POST: run Ptolemy DWBA for a set of Ex states
            # Body: { reaction: {...helios_reaction.json...}, states: [{ex, l, j, nodes}, ...],
            #         angle_min, angle_max, angle_step }
            import tempfile, shutil, re
            try:
                data      = json.loads(body)
                rx        = data.get('reaction', {})
                states    = data.get('states', [])
                ang_min   = float(data.get('angle_min',   0.0))
                ang_max   = float(data.get('angle_max', 180.0))
                ang_step  = float(data.get('angle_step',   1.0))
                jbiga     = data.get('jbiga', '0+')  # beam ground state J^pi

                if not states:
                    self.send_json({'ok': False, 'error': 'No states provided'}, 400)
                    return

                # Build reaction string from helios_reaction.json fields
                # e.g. "13B(d,3He)12Be"
                rx_str = rx.get('reaction_str', '')
                beam_A_val = int(rx.get('beam_A', 1))
                elab   = float(rx.get('beam_energy_MeVu', 14.0)) * beam_A_val  # total MeV

                # A+Z → element symbol from shared module-level ELEMENT_SYMBOLS
                sym = sym_for_Z

                # Potential reference strings — built once per request, shared across all states
                _POT_REFS = {
                    'A':'An and Cai (2006)', 'H':'Han, Shi, Shen (2006)',
                    'D':'Daehnick (1980) REL', 'C':'Daehnick (1980) NON-REL',
                    'L':'Lohr and Haeberli (1974)', 'Q':'Perey and Perey (1963)',
                    'Z':'Zhang, Pang, Lou (2016)',
                    'K':'Koning and Delaroche (2009)', 'V':'Varner CH89 (1991)',
                    'M':'Menet (1971)', 'G':'Becchetti and Greenlees (1969)',
                    'P':'Perey (1963)',
                    'x':'Xu, Guo, Han, Shen (2011)', 'X':'Xu, Guo, Han, Shen (2011)',
                    'l':'Liang, Li, Cai (2009)', 'p':'Pang (2009)',
                    'c':'Li, Liang, Cai (2007)', 't':'Trost (1987)',
                    'h':'Hyakutake (1980)', 'b':'Becchetti and Greenlees (1971)',
                    's':'Su and Han (2015)', 'S':'Su and Han (2015)',
                    'a':'Avrigeanu (2009)', 'f':'Bassani and Picard (1969)',
                    'n':'zero (neutron)',
                }

                beam_A  = beam_A_val;  beam_Z  = int(rx.get('beam_Z',  1))
                tgt_A   = int(rx.get('target_A',2));  tgt_Z   = int(rx.get('target_Z',1))
                lt_A    = int(rx.get('recoil_light_A',1)); lt_Z = int(rx.get('recoil_light_Z',1))
                hvy_A   = beam_A + tgt_A - lt_A
                hvy_Z   = beam_Z + tgt_Z - lt_Z

                # projectile = target nucleus in Ptolemy convention
                # beam hits target → projectile=target particle, target=beam nucleus
                # Cleopatra convention: BeamNuc(target,light)HeavyNuc
                beam_lbl = f'{beam_A}{sym(beam_Z)}'
                tgt_lbl  = f'{tgt_A}{sym(tgt_Z)}'
                lt_lbl   = f'{lt_A}{sym(lt_Z)}'
                hvy_lbl  = f'{hvy_A}{sym(hvy_Z)}'

                reaction_label = f'{beam_lbl}({tgt_lbl},{lt_lbl}){hvy_lbl}'

                PTOLEMY    = os.path.expanduser('~/digios/analysis/Cleopatra/ptolemy')
                GEN_INFILE = os.path.expanduser('~/helios_model/gen_infile.py')
                PYTHON     = sys.executable

                # Load gen_infile module for direct use
                import importlib.util as _ilu
                _gf_spec = _ilu.spec_from_file_location('gen_infile', GEN_INFILE)
                _gf = _ilu.module_from_spec(_gf_spec)
                _gf_spec.loader.exec_module(_gf)

                # Compute Q-value from NDS masses if possible
                # Approximate: use mass excesses from AME (skip for now, pass None -> 0)
                qvalue = rx.get('Q', None)
                if qvalue is not None:
                    qvalue = float(qvalue)

                tmpdir = tempfile.mkdtemp(prefix='ptolemy_')
                results = []
                errors  = []

                try:
                    for st in states:
                        ex    = float(st.get('ex',    0.0))
                        l     = int(st.get('l',       0))
                        j_str = str(st.get('j',       '0.5'))
                        n     = int(st.get('nodes',   0))
                        recoil_jpi = st.get('recoil_jpi', '0+')

                        if '/' in j_str:
                            num, den = j_str.split('/')
                            j_val = float(num) / float(den)
                        else:
                            j_val = float(j_str)

                        # Determine potential codes
                        pot_in_key  = st.get('pot_in',  'auto')
                        pot_out_key = st.get('pot_out', 'auto')

                        # Auto: pick based on particle type
                        def auto_pot(A, Z):
                            if A==1 and Z==1: return 'K'
                            if A==2 and Z==1: return 'A'
                            if A==3 and Z==1: return 'c'
                            if A==3 and Z==2: return 'x'
                            if A==4 and Z==2: return 's'
                            return 'n'

                        if pot_in_key  == 'auto': pot_in_key  = auto_pot(tgt_A, tgt_Z)
                        if pot_out_key == 'auto': pot_out_key = auto_pot(lt_A,  lt_Z)

                        pot_in_ref  = _POT_REFS.get(pot_in_key,  pot_in_key)
                        pot_out_ref = _POT_REFS.get(pot_out_key, pot_out_key)

                        try:
                            in_content = _gf.gen_infile(
                                beam_A=beam_A, beam_Z=beam_Z,
                                target_A=tgt_A, target_Z=tgt_Z,
                                light_A=lt_A,  light_Z=lt_Z,
                                beam_energy_MeVu=float(rx.get('beam_energy_MeVu', elab/beam_A_val)),
                                ex=ex, nodes=n, l=l, j=j_val,
                                recoil_jpi=recoil_jpi,
                                jbiga=jbiga,
                                pot_in_code=pot_in_key,
                                pot_out_code=pot_out_key,
                                pot_in_ref=pot_in_ref,
                                pot_out_ref=pot_out_ref,
                                ang_min=ang_min, ang_max=ang_max, ang_step=ang_step,
                                qvalue=qvalue,
                            )
                        except Exception as ge:
                            errors.append({'msg': f'Ex={ex}: gen_infile failed: {ge}', 'in_file': ''})
                            continue

                        in_file = os.path.join(tmpdir, f'state_ex{ex:.3f}_l{l}.in')
                        with open(in_file, 'w') as fh: fh.write(in_content)


                        # Run Ptolemy (Cleopatra binary)
                        fort_pat = os.path.join(tmpdir, 'fort.*')
                        import glob
                        for f in glob.glob(fort_pat): os.remove(f)

                        with open(in_file) as _stdin_fh:
                          pty_r = subprocess.run(
                            [PTOLEMY],
                            stdin=_stdin_fh,
                            capture_output=True, text=True,
                            timeout=60, cwd=tmpdir
                        )

                        # Parse output: extract CM angle + dσ/dΩ (mb/sr) column
                        angles = []; xsec = []
                        in_xsec = False
                        for line in pty_r.stdout.splitlines():
                            if 'COMPUTATION OF CROSS SECTIONS' in line:
                                in_xsec = True; continue
                            if not in_xsec: continue
                            # Data lines: leading spaces + float angle + xsec (may be NaN)
                            m = re.match(r'^\s+(\d+\.\d+)\s+(NaN|[\d\.Ee+\-]+)\s+', line)
                            if m:
                                val = m.group(2)
                                angles.append(float(m.group(1)))
                                xsec.append(float('nan') if val == 'NaN' else float(val))
                            # Stop at TOTAL line
                            if line.strip().startswith('0TOTAL:'):
                                break

                        # Filter out all-NaN results
                        import math
                        valid = [(a, x) for a, x in zip(angles, xsec) if not math.isnan(x)]
                        if valid:
                            angles, xsec = zip(*valid)
                            angles, xsec = list(angles), list(xsec)
                        else:
                            angles, xsec = [], []

                        if not angles:
                            # Try to extract Ptolemy's reason from output
                            reason = ''
                            lines_out = pty_r.stdout.splitlines()
                            for i, line in enumerate(lines_out):
                                if 'INCOMPATABLE' in line:
                                    reason = line.strip().lstrip('0*').strip()
                                    break
                                if 'ERROR IN INPUT' in line:
                                    # Grab next non-empty line for JA/JB details
                                    for j in range(i+1, min(i+4, len(lines_out))):
                                        nxt = lines_out[j].strip()
                                        if nxt:
                                            reason = nxt
                                            break
                                    if not reason:
                                        reason = 'ERROR IN INPUT'
                                    break
                            if not reason:
                                recoil_jpi_str = st.get('recoil_jpi', '?')
                                recoil_par = recoil_jpi_str[-1] if recoil_jpi_str and recoil_jpi_str[-1] in '+-' else '?'
                                beam_par = jbiga[-1] if jbiga and jbiga[-1] in '+-' else '?'
                                # expected recoil parity = beam_parity * (-1)^l
                                if beam_par != '?' and recoil_par != '?':
                                    beam_sign   = +1 if beam_par  == '+' else -1
                                    recoil_sign = +1 if recoil_par == '+' else -1
                                    expected    = beam_sign * ((-1)**l)
                                    exp_char    = '+' if expected > 0 else '-'
                                    def parse_j(s):
                                        s = s.strip().rstrip('+-')
                                        if '/' in s:
                                            n2, d2 = s.split('/')
                                            return float(n2)/float(d2)
                                        try: return float(s)
                                        except: return None
                                    j_beam   = parse_j(jbiga)
                                    j_recoil = parse_j(recoil_jpi_str)
                                    j_trans  = parse_j(str(j_str))
                                    if expected != recoil_sign:
                                        reason = (f'parity mismatch: beam({beam_par}) x (-1)^l={l} = ({exp_char}), '
                                                  f'but recoil Jpi={recoil_jpi_str} needs ({recoil_par}). '
                                                  f'Try l={l+1}')
                                    elif j_beam is not None and j_recoil is not None and j_trans is not None:
                                        j_min = abs(j_beam - j_trans)
                                        j_max = j_beam + j_trans
                                        if not (j_min - 0.01 <= j_recoil <= j_max + 0.01):
                                            reason = (f'coupling blocked: beam J={j_beam}, j={j_str} '
                                                      f'gives range [{j_min:.1f},{j_max:.1f}], cannot reach J={j_recoil}')
                                        else:
                                            reason = ('Ptolemy NaN: parity and coupling OK but numerical result is NaN. '
                                                      'Possible causes: high energy overflow, lmax too low, or wavefunction issue. '
                                                      'Try increasing lmax or check Show .in file.')
                                    else:
                                        reason = 'no cross section -- check selection rules'
                                else:
                                    reason = 'no cross section -- check l/j/Jpi selection rules'
                            errors.append({'msg': f'Ex={ex} (l={l} j={j_str} Jpi={st.get("recoil_jpi","?")}): {reason}', 'in_file': in_content})
                            continue

                        results.append({
                            'ex': ex, 'l': l, 'j': j_str, 'nodes': n,
                            'recoil_jpi': st.get('recoil_jpi', ''),
                            'angles': angles, 'xsec': xsec,
                            'in_file': in_content,
                        })

                        # Clean up .in files for next iteration
                        for f in os.listdir(tmpdir):
                            if f.endswith('.in'): os.remove(os.path.join(tmpdir, f))
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)

                self.send_json({'ok': True, 'results': results, 'errors': errors,
                                'reaction': reaction_label})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress access log spam

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Handle each request in a separate thread (prevents DWBA blocking NDS queries)."""
    allow_reuse_address = True
    daemon_threads = True

if __name__ == '__main__':
    os.chdir(DIR)
    _get_masses()  # warm up mass cache at startup
    ensure_default_files()  # create geometry/reaction JSON if missing
    with ThreadedTCPServer(('', PORT), Handler) as httpd:
        print(f'HELIOS 3D viewer: http://localhost:{PORT}')
        print(f'From network:     http://192.168.1.101:{PORT}')
        print(f'Config source:    {DIGIOS_WORKING}')
        httpd.serve_forever()
