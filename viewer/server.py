#!/usr/bin/env python3
"""
HELIOS 3D viewer server — port 8765
Single source of runtime state: helios_state.json
helios_config.json is read-only (element symbols, SS presets).
"""

import http.server, json, math, os, socket, socketserver, subprocess, sys, threading, urllib.request, urllib.error

# ── Path setup ────────────────────────────────────────────────────────────────
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)
from build_reaction import (ELEMENT_SYMBOLS, element_symbol,
                             compute_kinematics, compute_kinematics_from_state)

PORT             = 8765
DIR              = os.path.dirname(os.path.abspath(__file__))
ROOT             = os.path.dirname(DIR)          # helios_model/
STATE_JSON       = os.path.join(ROOT, 'helios_state.json')
CONFIG_JSON      = os.path.join(ROOT, 'helios_config.json')
MCP_JSON         = os.path.join(ROOT, 'mcp.json')
DEFAULT_NDS_URL  = 'http://192.168.203.75:65432/sse'

DIGIOS_WORKING   = os.path.expanduser('~/digios/analysis/working')
DETECTOR_GEO     = os.path.join(DIGIOS_WORKING, 'detectorGeo.txt')
REACTION_CFG     = os.path.join(DIGIOS_WORKING, 'reactionConfig.txt')
PTOLEMY_BIN      = os.path.expanduser('~/digios/analysis/Cleopatra/ptolemy')
GEN_INFILE_PY    = os.path.join(ROOT, 'gen_infile.py')

# ── Mass table cache ──────────────────────────────────────────────────────────
_MASS_CACHE = None
_MASS_LOCK  = threading.Lock()
_MASS_PATH  = os.path.join(ROOT, 'mass20.txt')
_STATE_LOCK = threading.Lock()  # serialize helios_state.json reads/writes

def _get_masses():
    global _MASS_CACHE
    if _MASS_CACHE is not None:
        return _MASS_CACHE
    with _MASS_LOCK:
        if _MASS_CACHE is not None:
            return _MASS_CACHE
        if not os.path.exists(_MASS_PATH):
            return None
        from build_reaction import parse_mass_table
        _MASS_CACHE = parse_mass_table(_MASS_PATH)
    return _MASS_CACHE

# ── helios_config.json (read-only, cached) ────────────────────────────────────
_CONFIG_CACHE = None
def _get_config():
    """Read helios_config.json (committed to repo). Required file — raise if missing."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    if not os.path.exists(CONFIG_JSON):
        raise FileNotFoundError(f'{CONFIG_JSON} missing — should be in the repo')
    with open(CONFIG_JSON) as f:
        _CONFIG_CACHE = json.load(f)
    return _CONFIG_CACHE

# ── State management ──────────────────────────────────────────────────────────

def _default_state():
    """Return default state from helios_config.json -> defaults (required)."""
    cfg = _get_config()
    d   = cfg['defaults']  # required: helios_config.json is in the repo
    g   = d['geometry']
    r   = d['reaction']
    return {
        'config_source': 'manual',
        'ss_type': d.get('ss_type', 'HELIOS'),
        'geometry': {
            'firstPos':    float(g['firstPos']),
            'recoilPos':   float(g['recoilPos']),
            'Bfield':      float(g['Bfield']),
            'recoilInner': float(g['recoilInner']),
            'recoilOuter': float(g['recoilOuter']),
        },
        'reaction': {
            'beam_A':           int(r['beam_A']),
            'beam_Z':           int(r['beam_Z']),
            'target_A':         int(r['target_A']),
            'target_Z':         int(r['target_Z']),
            'light_A':          int(r['light_A']),
            'light_Z':          int(r['light_Z']),
            'beam_energy_MeVu': float(r['beam_energy_MeVu']),
        },
        'computed': {},
    }

def read_state():
    """Read helios_state.json (locked). Returns default if missing/corrupt (logged)."""
    with _STATE_LOCK:
        if os.path.exists(STATE_JSON):
            try:
                with open(STATE_JSON) as f:
                    return json.load(f)
            except Exception as e:
                print(f'[server] WARNING: {STATE_JSON} unreadable ({e}); using defaults')
        return _default_state()

def write_state(state):
    """Atomic write of helios_state.json (locked, tmp+rename)."""
    tmp = STATE_JSON + '.tmp'
    with _STATE_LOCK:
        with open(tmp, 'w') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_JSON)

def recompute_state(state):
    """Fill state['computed'] from reaction + ss_type + geometry. Returns updated state."""
    cfg     = _get_config()
    presets = cfg['spectrometers']  # required key in helios_config.json
    ss      = state.get('ss_type', 'HELIOS')
    geo     = state.get('geometry', {})
    rxn     = state.get('reaction', {})
    if ss not in presets:
        raise KeyError(f'ss_type {ss!r} not in helios_config.json spectrometers')
    preset = presets[ss]

    fp = geo.get('firstPos', preset['firstPos'])

    # Generate detectors from preset + firstPos
    dets, z_min, z_max = _make_detectors(preset, fp)

    # Compute kinematics
    kinem = {}
    masses = _get_masses()
    if masses and all(k in rxn for k in ('beam_A','beam_Z','target_A','target_Z','light_A','light_Z','beam_energy_MeVu')):
        try:
            kinem = compute_kinematics_from_state(rxn, masses)
        except Exception as e:
            kinem = {'error': str(e)}

    state['computed'] = {
        **kinem,
        'detectors': dets,
        'zMin': z_min,
        'zMax': z_max,
    }
    return state

def _make_detectors(preset, first_pos):
    """Generate detector array from SS preset + firstPos. Returns (dets, zMin, zMax).

    NOTE: posOffsets comes from helios_config.json SS preset only, NEVER from state.geometry.
    To change the array layout, edit the preset in helios_config.json and restart.
    state.geometry only carries the user-tunable parameters (firstPos, recoilPos, Bfield).
    """
    offsets = preset.get('posOffsets', [0.0])
    n_det   = len(offsets)
    m_det   = int(preset.get('mDet', 4))
    length  = float(preset.get('detLen', 50.0))
    perp    = float(preset.get('perpDist', 11.5))
    width   = float(preset.get('detWidth', 10.0))
    fp      = float(first_pos)

    start      = m_det // 2 if fp < 0 else 0
    side_order = [(start + i) % m_det for i in range(m_det)]

    dets = []
    for col_idx, offset in enumerate(offsets):
        z_near = round(fp - offset if fp < 0 else fp + offset, 4)
        z_ctr  = round(z_near - length / 2 if fp < 0 else z_near + length / 2, 4)
        for row in range(m_det):
            phi = 2 * math.pi / m_det * row
            dets.append({
                'id': -1, 'row': row,
                'col': n_det - 1 - col_idx,
                'phi_deg': round(math.degrees(phi), 2),
                'z_near': z_near, 'z_center': z_ctr, 'z': z_ctr,
                'x': round(perp * math.cos(phi), 4),
                'y': round(perp * math.sin(phi), 4),
                'detLen': length, 'detWidth': width,
            })

    new_id = 0
    for s in side_order:
        for c in range(n_det):
            for d in dets:
                if d['row'] == s and d['col'] == c:
                    d['id'] = new_id; new_id += 1; break

    znears = [d['z_near'] for d in dets]
    z_min = round(min(znears) - (length if fp < 0 else 0), 4)
    z_max = round(max(znears) + (length if fp > 0 else 0), 4)
    return dets, z_min, z_max

# ── Digios loader (single source for both startup refresh and /api/config) ────────

def _load_state_from_digios():
    """Build a fully-computed state dict from digios files.

    Returns (state, extras, None) on success or (None, None, error_str) on failure.
    `extras` is {'detGeo': dg, 'reactionConfig': rc} for callers that want the raw parses.
    Defaults for missing fields fall back to helios_config.json -> "defaults".
    """
    from build_geometry import parse_detgeo, parse_reaction_config
    if not os.path.exists(DETECTOR_GEO):
        return None, None, f'detectorGeo.txt not found at {DETECTOR_GEO}'
    if not os.path.exists(REACTION_CFG):
        return None, None, f'reactionConfig.txt not found at {REACTION_CFG}'
    try:
        dg  = parse_detgeo(DETECTOR_GEO)
        rc  = parse_reaction_config(REACTION_CFG)
        cfg = _get_config()
        gdef = cfg['defaults']['geometry']
        rdef = cfg['defaults']['reaction']
        # Pick SS type by mDet match against presets
        m  = int(dg.get('mDet', 4))
        ss = next((k for k, v in cfg['spectrometers'].items()
                   if v.get('mDet') == m), cfg['defaults'].get('ss_type', 'HELIOS'))
        state = {
            'config_source': 'digios',
            'ss_type': ss,
            'geometry': {
                'firstPos':    float(dg.get('firstPos',    gdef['firstPos'])),
                'recoilPos':   float(dg.get('recoilPos',   gdef['recoilPos'])),
                'Bfield':      float(dg.get('Bfield',      gdef['Bfield'])),
                'recoilInner': float(dg.get('recoilInner', gdef['recoilInner'])),
                'recoilOuter': float(dg.get('recoilOuter', gdef['recoilOuter'])),
            },
            'reaction': {
                'beam_A':           int(rc.get('beam_A',           rdef['beam_A'])),
                'beam_Z':           int(rc.get('beam_Z',           rdef['beam_Z'])),
                'target_A':         int(rc.get('target_A',         rdef['target_A'])),
                'target_Z':         int(rc.get('target_Z',         rdef['target_Z'])),
                'light_A':          int(rc.get('recoil_light_A',   rdef['light_A'])),
                'light_Z':          int(rc.get('recoil_light_Z',   rdef['light_Z'])),
                'beam_energy_MeVu': float(rc.get('beam_energy_MeVu', rdef['beam_energy_MeVu'])),
            },
            'computed': {},
        }
        recompute_state(state)
        return state, {'detGeo': dg, 'reactionConfig': rc}, None
    except Exception as e:
        return None, None, str(e)


# ── Startup: ensure state exists ─────────────────────────────

def ensure_state():
    """On startup: create helios_state.json if missing, or refresh if config_source=digios."""
    if not os.path.exists(STATE_JSON):
        print('[server] helios_state.json missing — creating...')
        state, _extras, err = _load_state_from_digios()
        if state is None:
            print(f'[server] digios unavailable ({err}) — writing defaults (manual)')
            state = _default_state()
            recompute_state(state)
        write_state(state)
        print(f'[server] state created: ss={state["ss_type"]} source={state["config_source"]}')
        return

    state = read_state()
    src = state.get('config_source', 'manual')
    if src == 'digios':
        print('[server] config_source=digios — refreshing from digios...')
        fresh, _extras, err = _load_state_from_digios()
        if fresh:
            write_state(fresh)
            print('[server] state refreshed from digios')
        else:
            print(f'[server] digios unavailable ({err}) — keeping existing state')
    else:
        # manual — recompute computed section in case code changed
        recompute_state(state)
        write_state(state)
        print(f'[server] state loaded: ss={state.get("ss_type")} source={src}')

# ── MCP/NDS helpers ───────────────────────────────────────────────────────────

def read_mcp_config():
    if os.path.exists(MCP_JSON):
        try:
            with open(MCP_JSON) as f: return json.load(f)
        except Exception: pass
    return {'nds_url': DEFAULT_NDS_URL}

def save_mcp_config(data):
    with open(MCP_JSON, 'w') as f: json.dump(data, f, indent=2)

def probe_nds(url, timeout=3):
    try:
        req = urllib.request.Request(url, headers={'Accept': 'text/event-stream'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False

def mcp_tool_call(nds_url, tool_name, arguments, timeout=15):
    import time
    endpoint_path = None
    result_event  = threading.Event()
    messages      = []
    sse_error     = []

    def sse_reader(resp):
        nonlocal endpoint_path
        event_type = None
        try:
            for raw in resp:
                line = raw.decode('utf-8').rstrip('\n\r')
                if line.startswith('event:'):   event_type = line[6:].strip()
                elif line.startswith('data:'):
                    data = line[5:].strip()
                    if event_type == 'endpoint':
                        endpoint_path = data; ready_evt.set()
                    elif event_type == 'message':
                        try:
                            msg = json.loads(data); messages.append(msg)
                            if msg.get('id') == 1: result_event.set()
                        except Exception: pass
                elif line == '': event_type = None
                if result_event.is_set(): break
        except Exception as e:
            sse_error.append(str(e)); ready_evt.set(); result_event.set()

    ready_evt = threading.Event()
    base = nds_url.rsplit('/sse', 1)[0]
    req_sse = urllib.request.Request(nds_url, headers={'Accept': 'text/event-stream'})
    try:    sse_resp = urllib.request.urlopen(req_sse, timeout=timeout)
    except Exception as e: raise RuntimeError(f'SSE connect failed: {e}')

    t = threading.Thread(target=sse_reader, args=(sse_resp,), daemon=True)
    t.start()
    try:
        if not ready_evt.wait(timeout=5): raise RuntimeError('Timed out waiting for SSE endpoint')
        if sse_error: raise RuntimeError(sse_error[0])

        def post(payload):
            url = base + endpoint_path
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data,
                headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=5) as r: return r.status

        post({'jsonrpc':'2.0','id':0,'method':'initialize',
              'params':{'protocolVersion':'2024-11-05','capabilities':{},
                        'clientInfo':{'name':'helios-model','version':'1.0'}}})
        time.sleep(0.1)
        post({'jsonrpc':'2.0','method':'notifications/initialized'})
        time.sleep(0.1)
        post({'jsonrpc':'2.0','id':1,'method':'tools/call',
              'params':{'name': tool_name, 'arguments': arguments}})

        if not result_event.wait(timeout=10): raise RuntimeError('Timed out waiting for tool result')
        for msg in messages:
            if msg.get('id') == 1:
                content = msg.get('result', {}).get('content', [])
                if content and content[0].get('type') == 'text':
                    return json.loads(content[0]['text'])
                if 'error' in msg:
                    raise RuntimeError(msg['error'].get('message', str(msg['error'])))
        raise RuntimeError('No result message received')
    finally:
        try: sse_resp.close()
        except Exception: pass
        t.join(timeout=1.0)

# ── HTTP Handler ──────────────────────────────────────────────────────────────

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

    def serve_json_file(self, path):
        try:
            with open(path, 'rb') as f: body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(body))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self.send_response(404); self.end_headers()

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)

        # ── Static config (read-only) ──────────────────────────────────────────
        if path == '/helios_config.json':
            self.serve_json_file(CONFIG_JSON)

        # ── Runtime state ─────────────────────────────────────────────────────
        elif path == '/api/state':
            self.send_json({'ok': True, 'state': read_state()})

        # ── Load from digios (explicit user action) ───────────────────────
        elif path == '/api/config':
            state, extras, err = _load_state_from_digios()
            if state is None:
                self.send_json({'ok': False, 'error': err, 'errors': [err]})
            else:
                write_state(state)
                self.send_json({'ok': True, 'state': state,
                                 'detGeo': extras['detGeo'],
                                 'reactionConfig': extras['reactionConfig'],
                                 'errors': []})

        # ── Capabilities ──────────────────────────────────────────────────────
        elif path == '/api/capabilities':
            self.send_json({
                'ok': True,
                'digios':      os.path.exists(DETECTOR_GEO),
                'ptolemy':     os.path.exists(PTOLEMY_BIN),
                'mass_table':  os.path.exists(_MASS_PATH),
            })

        # ── Mass lookup ───────────────────────────────────────────────────────
        elif path.startswith('/api/mass'):
            masses = _get_masses()
            if masses is None:
                self.send_json({'ok': False, 'error': 'mass20.txt not found'}, 404)
            else:
                try:
                    from build_reaction import get_mass
                    A = Z = None
                    if 'AZ' in params:
                        import re
                        az = params['AZ'][0].strip()
                        m = re.match(r'^(\d+)([A-Za-z]+)$', az)
                        if m:
                            sym = m.group(2)
                            sym_canon = sym if len(sym)==1 else sym[0].upper()+sym[1:].lower()
                            Z = next((i for i,s in enumerate(ELEMENT_SYMBOLS) if s==sym_canon), None)
                            A = int(m.group(1))
                    elif 'A' in params and 'Z' in params:
                        A, Z = int(params['A'][0]), int(params['Z'][0])
                    if A is None or Z is None:
                        self.send_json({'ok': False, 'error': 'Bad A/Z'})
                    else:
                        mn = masses.get((0,1), 939.565)
                        mp = masses.get((1,1), 938.272)
                        m_alpha = masses.get((2,4), 3727.379)
                        mass = masses.get((Z,A))
                        Sn = (masses.get((Z,A-1),0) + mn - mass)       if mass and A>1 else None
                        Sp = (masses.get((Z-1,A-1),0) + mp - mass)     if mass and Z>1 else None
                        Sa = (masses.get((Z-2,A-4),0)+m_alpha - mass)  if mass and A>4 and Z>2 else None
                        self.send_json({'ok':True,'A':A,'Z':Z,'N':A-Z,
                            'mass': round(mass,4) if mass else None,
                            'name': f'{A}{element_symbol(Z)}',
                            'Sn': round(Sn,4) if Sn else None,
                            'Sp': round(Sp,4) if Sp else None,
                            'Sa': round(Sa,4) if Sa else None})
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)

        # ── NDS/MCP ───────────────────────────────────────────────────────────
        elif path == '/api/mcp_config':
            self.send_json({'ok': True, **read_mcp_config()})

        elif path == '/api/nds/status':
            cfg = read_mcp_config()
            url = cfg.get('nds_url', DEFAULT_NDS_URL)
            self.send_json({'ok': True, 'reachable': probe_nds(url), 'nds_url': url})

        elif path.startswith('/api/nds/query'):
            tool   = params.get('tool', [None])[0]
            args_s = params.get('args', ['{}'])[0]
            if not tool:
                self.send_json({'ok': False, 'error': 'Missing tool param'}, 400)
            else:
                try:
                    cfg = read_mcp_config()
                    url = cfg.get('nds_url', DEFAULT_NDS_URL)
                    result = mcp_tool_call(url, tool, json.loads(args_s))
                    self.send_json({'ok': True, 'result': result})
                except Exception as e:
                    self.send_json({'ok': False, 'error': str(e)}, 500)

        elif path == '/api/data':
            self.send_json({})

        else:
            super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(length)
        from urllib.parse import urlparse
        path = urlparse(self.path).path

        # ── Save state (main Save Config endpoint) ────────────────────────────
        if path == '/api/state':
            try:
                data  = json.loads(body)
                state = read_state()
                # Merge incoming fields
                if 'ss_type'       in data: state['ss_type']       = data['ss_type']
                if 'config_source' in data: state['config_source'] = data['config_source']
                if 'geometry'      in data: state.setdefault('geometry', {}).update(data['geometry'])
                if 'reaction'      in data: state.setdefault('reaction', {}).update(data['reaction'])
                recompute_state(state)
                write_state(state)
                self.send_json({'ok': True, 'state': state})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        # ── MCP config save ───────────────────────────────────────────────────
        elif path == '/api/mcp_config':
            try:
                data = json.loads(body); cfg = read_mcp_config()
                cfg.update({k: v for k, v in data.items() if k in ('nds_url',)})
                save_mcp_config(cfg)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        # ── Ptolemy DWBA ──────────────────────────────────────────────────────
        elif path == '/api/ptolemy':
            import tempfile, shutil, re, glob, math as _math, importlib.util as _ilu
            try:
                data     = json.loads(body)
                state    = read_state()
                rx       = data.get('reaction', {**state['reaction'], **state['computed']})
                states   = data.get('states', [])
                ang_min  = float(data.get('angle_min',   0.0))
                ang_max  = float(data.get('angle_max', 180.0))
                ang_step = float(data.get('angle_step',   1.0))
                jbiga    = data.get('jbiga', '0+')

                if not states:
                    self.send_json({'ok': False, 'error': 'No states provided'}, 400); return
                if not os.path.exists(PTOLEMY_BIN):
                    self.send_json({'ok': False, 'error': 'Ptolemy binary not found'}, 503); return

                beam_A = int(rx.get('beam_A', state['reaction']['beam_A']))
                tgt_A  = int(rx.get('target_A', state['reaction']['target_A']))
                tgt_Z  = int(rx.get('target_Z', state['reaction']['target_Z']))
                lt_A   = int(rx.get('light_A', rx.get('recoil_light_A', state['reaction']['light_A'])))
                lt_Z   = int(rx.get('light_Z', rx.get('recoil_light_Z', state['reaction']['light_Z'])))
                beam_Z = int(rx.get('beam_Z', state['reaction']['beam_Z']))
                hvy_A  = beam_A + tgt_A - lt_A
                hvy_Z  = beam_Z + tgt_Z - lt_Z
                sym    = element_symbol

                _POT_REFS = {
                    'A':'An and Cai (2006)','H':'Han, Shi, Shen (2006)',
                    'D':'Daehnick (1980) REL','C':'Daehnick (1980) NON-REL',
                    'K':'Koning and Delaroche (2009)','V':'Varner CH89 (1991)',
                    'M':'Menet (1971)','G':'Becchetti and Greenlees (1969)',
                    'x':'Xu, Guo, Han, Shen (2011)','X':'Xu, Guo, Han, Shen (2011)',
                    'l':'Liang, Li, Cai (2009)','s':'Su and Han (2015)',
                    'S':'Su and Han (2015)','n':'zero (neutron)',
                }
                reaction_label = f'{beam_A}{sym(beam_Z)}({tgt_A}{sym(tgt_Z)},{lt_A}{sym(lt_Z)}){hvy_A}{sym(hvy_Z)}'
                qvalue = rx.get('Q', state['computed'].get('Q'))
                if qvalue is not None: qvalue = float(qvalue)

                _gf_spec = _ilu.spec_from_file_location('gen_infile', GEN_INFILE_PY)
                _gf = _ilu.module_from_spec(_gf_spec); _gf_spec.loader.exec_module(_gf)

                tmpdir  = tempfile.mkdtemp(prefix='ptolemy_')
                results = []; errors = []
                try:
                    for st in states:
                        ex  = float(st.get('ex', 0.0)); l = int(st.get('l', 0))
                        j_str = str(st.get('j', '0.5')); n = int(st.get('nodes', 0))
                        recoil_jpi = st.get('recoil_jpi', '0+')
                        j_val = float(j_str.split('/')[0])/float(j_str.split('/')[1]) if '/' in j_str else float(j_str)

                        def auto_pot(A, Z):
                            if A==1 and Z==1: return 'K'
                            if A==2 and Z==1: return 'A'
                            if A==3 and Z==1: return 'c'
                            if A==3 and Z==2: return 'x'
                            if A==4 and Z==2: return 's'
                            return 'n'

                        pot_in  = st.get('pot_in',  'auto'); pot_out = st.get('pot_out', 'auto')
                        if pot_in  == 'auto': pot_in  = auto_pot(tgt_A, tgt_Z)
                        if pot_out == 'auto': pot_out = auto_pot(lt_A,  lt_Z)

                        try:
                            in_content = _gf.gen_infile(
                                beam_A=beam_A, beam_Z=beam_Z,
                                target_A=tgt_A, target_Z=tgt_Z,
                                light_A=lt_A, light_Z=lt_Z,
                                beam_energy_MeVu=float(rx.get('beam_energy_MeVu', state['reaction']['beam_energy_MeVu'])),
                                ex=ex, nodes=n, l=l, j=j_val,
                                recoil_jpi=recoil_jpi, jbiga=jbiga,
                                pot_in_code=pot_in, pot_out_code=pot_out,
                                pot_in_ref=_POT_REFS.get(pot_in, pot_in),
                                pot_out_ref=_POT_REFS.get(pot_out, pot_out),
                                ang_min=ang_min, ang_max=ang_max, ang_step=ang_step,
                                qvalue=qvalue,
                            )
                        except Exception as ge:
                            errors.append({'msg': f'Ex={ex}: gen_infile failed: {ge}', 'in_file': ''}); continue

                        in_file = os.path.join(tmpdir, f'state_ex{ex:.3f}_l{l}.in')
                        with open(in_file, 'w') as fh: fh.write(in_content)
                        for f in glob.glob(os.path.join(tmpdir, 'fort.*')): os.remove(f)

                        with open(in_file) as _si:
                            pty_r = subprocess.run([PTOLEMY_BIN], stdin=_si,
                                capture_output=True, text=True, timeout=60, cwd=tmpdir)

                        angles = []; xsec = []; in_xsec = False
                        for line in pty_r.stdout.splitlines():
                            if 'COMPUTATION OF CROSS SECTIONS' in line: in_xsec = True; continue
                            if not in_xsec: continue
                            m2 = re.match(r'^\s+(\d+\.\d+)\s+(NaN|[\d\.Ee+\-]+)\s+', line)
                            if m2:
                                angles.append(float(m2.group(1)))
                                xsec.append(float('nan') if m2.group(2)=='NaN' else float(m2.group(2)))
                            if line.strip().startswith('0TOTAL:'): break

                        valid = [(a,x) for a,x in zip(angles,xsec) if not _math.isnan(x)]
                        if valid: angles, xsec = map(list, zip(*valid))
                        else:     angles, xsec = [], []

                        if not angles:
                            errors.append({'msg': f'Ex={ex} (l={l} j={j_str}): no cross section', 'in_file': in_content}); continue

                        results.append({'ex':ex,'l':l,'j':j_str,'nodes':n,
                            'recoil_jpi':st.get('recoil_jpi',''),
                            'angles':angles,'xsec':xsec,'in_file':in_content})
                        for f in os.listdir(tmpdir):
                            if f.endswith('.in'): os.remove(os.path.join(tmpdir, f))
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)

                self.send_json({'ok':True,'results':results,'errors':errors,'reaction':reaction_label})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress access log spam


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _detect_host_ip():
    """Best-effort: find the IP this host uses to reach the local network.
    Falls back to gethostbyname(hostname) and finally None."""
    try:
        # Trick: UDP socket to a public address — no packets sent, but kernel
        # populates the source IP that would be used for the route.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return None

if __name__ == '__main__':
    os.chdir(DIR)
    _get_masses()       # warm up mass cache
    _get_config()       # warm up config cache
    ensure_state()      # create/refresh helios_state.json
    with ThreadedTCPServer(('', PORT), Handler) as httpd:
        print(f'HELIOS 3D viewer: http://localhost:{PORT}', flush=True)
        host_ip = _detect_host_ip()
        if host_ip and host_ip != '127.0.0.1':
            print(f'From network:     http://{host_ip}:{PORT}', flush=True)
        if os.path.exists(DIGIOS_WORKING):
            print(f'Digios path:      {DIGIOS_WORKING}', flush=True)
        else:
            print(f'Digios path:      not found ({DIGIOS_WORKING})', flush=True)
        httpd.serve_forever()
