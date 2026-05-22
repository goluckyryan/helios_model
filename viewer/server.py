#!/usr/bin/env python3
"""
HELIOS 3D Model viewer server — port 8765
Serves static files + API endpoints.
"""

import http.server, json, os, socketserver, subprocess, sys

PORT = 8765
DIR  = os.path.dirname(os.path.abspath(__file__))

# Paths to digios working files
DIGIOS_WORKING = os.path.expanduser('~/digios/analysis/working')
HELIOS_REACTION_JSON = os.path.join(os.path.dirname(DIR), 'helios_reaction.json')
BUILD_REACTION_PY    = os.path.join(os.path.dirname(DIR), 'build_reaction.py')
DETECTOR_GEO   = os.path.join(DIGIOS_WORKING, 'detectorGeo.txt')
REACTION_DAT   = os.path.join(DIGIOS_WORKING, 'reaction.dat')
REACTION_CFG   = os.path.join(DIGIOS_WORKING, 'reactionConfig.txt')
BUILD_GEO_PY   = os.path.join(os.path.dirname(DIR), 'build_geometry.py')
GEO_JSON       = os.path.join(os.path.dirname(DIR), 'helios_geometry.json')

def parse_detgeo(path):
    """Parse detectorGeo.txt into a dict."""
    keys = [
        'Bfield', 'Bfield_theta', 'bore', 'perpDist', 'width', 'length',
        'recoilPos', 'recoilInner', 'recoilOuter', 'isCoincident',
        'recoilPos1', 'recoilPos2', 'elumPos1', 'elumPos2', 'blocker',
        'firstPos', 'eSigma', 'zSigma', 'facing', 'mDet',
    ]
    values = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            tok = line.split()[0]
            values.append(tok)

    result = {}
    for i, k in enumerate(keys):
        if i < len(values):
            try:
                result[k] = float(values[i])
            except ValueError:
                result[k] = values[i]

    # Remaining = detector near-positions
    det_pos = []
    for v in values[len(keys):]:
        try:
            det_pos.append(float(v))
        except ValueError:
            pass
    result['detPos'] = det_pos
    result['nDet'] = len(det_pos)

    # Compute firstPos and zRange for display
    first = result.get('firstPos', 0)
    length = result.get('length', 0)
    nDet = result['nDet']
    if nDet > 0 and det_pos:
        if first < 0:
            result['zMin'] = first - det_pos[-1] - length
            result['zMax'] = first - det_pos[0]
        else:
            result['zMin'] = first + det_pos[0]
            result['zMax'] = first + det_pos[-1] + length
    return result

def parse_reaction(path):
    """Parse reaction.dat into a dict."""
    keys = ['mass_b', 'charge_b', 'betaCM', 'Ecm', 'mass_B', 'alpha']
    values = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            tok = line.split()[0]
            values.append(tok)
    result = {}
    for i, k in enumerate(keys):
        if i < len(values):
            try:
                result[k] = float(values[i])
            except ValueError:
                result[k] = values[i]
    return result

def parse_reaction_config(path):
    """Parse reactionConfig.txt into a dict."""
    keys = [
        'beam_A', 'beam_Z', 'target_A', 'target_Z',
        'recoil_light_A', 'recoil_light_Z', 'beam_energy_MeVu',
        'beam_energy_sigma', 'beam_angle', 'beam_emittance',
        'x_offset', 'y_offset', 'n_events', 'isTargetScattering',
        'target_density', 'target_thickness',
    ]
    values = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            tok = line.split()[0]
            values.append(tok)
    result = {}
    for i, k in enumerate(keys):
        if i < len(values):
            try:
                result[k] = float(values[i])
            except ValueError:
                result[k] = values[i]
    # Derived: beam label e.g. "32Si"
    if 'beam_A' in result and 'beam_Z' in result:
        element_symbols = [
            '', 'H','He','Li','Be','B','C','N','O','F','Ne',
            'Na','Mg','Al','Si','P','S','Cl','Ar','K','Ca',
            'Sc','Ti','V','Cr','Mn','Fe','Co','Ni','Cu','Zn',
            'Ga','Ge','As','Se','Br','Kr','Rb','Sr','Y','Zr',
            'Nb','Mo','Tc','Ru','Rh','Pd','Ag','Cd','In','Sn',
            'Sb','Te','I','Xe','Cs','Ba','La','Ce','Pr','Nd',
        ]
        A = int(result['beam_A']); Z = int(result['beam_Z'])
        sym = element_symbols[Z] if Z < len(element_symbols) else f'Z{Z}'
        result['beam_label'] = f'{A}{sym}'
        # Also target label
        At = int(result.get('target_A', 2)); Zt = int(result.get('target_Z', 1))
        symt = element_symbols[Zt] if Zt < len(element_symbols) else f'Z{Zt}'
        result['target_label'] = f'{At}{symt}'
        # Light recoil label
        Al = int(result.get('recoil_light_A', 1)); Zl = int(result.get('recoil_light_Z', 1))
        syml = element_symbols[Zl] if Zl < len(element_symbols) else f'Z{Zl}'
        result['recoil_light_label'] = f'{Al}{syml}'
    return result

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/helios_geometry.json':
            # Serve helios_geometry.json from root folder
            try:
                with open(GEO_JSON, 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(404); self.end_headers()

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
                    if r.returncode != 0:
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
                        'Bfield': result['detGeo'].get('Bfield', -3.0) if 'detGeo' in result else -3.0,
                    }
                    with open(HELIOS_REACTION_JSON, 'w') as f:
                        import json as _json; _json.dump(rxData, f, indent=2)
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
            mass_path = os.path.join(os.path.dirname(DIR), 'mass20.txt')
            if not os.path.exists(mass_path):
                self.send_json({'ok': False, 'error': 'mass20.txt not found'}, 404)
            else:
                try:
                    sys.path.insert(0, os.path.dirname(DIR))
                    from build_reaction import parse_mass_table, element_symbol
                    masses = parse_mass_table(mass_path)
                    A = Z = None
                    if 'AZ' in params:
                        import re
                        az = params['AZ'][0].strip()
                        m = re.match(r'^(\d+)([A-Za-z]+)$', az)
                        if m: A,Z = int(m.group(1)), next((i for i,s in enumerate(['n','H','He','Li','Be','B','C','N','O','F','Ne','Na','Mg','Al','Si','P','S','Cl','Ar','K','Ca','Sc','Ti','V','Cr','Mn','Fe','Co','Ni','Cu','Zn','Ga','Ge','As','Se','Br','Kr','Rb','Sr','Y','Zr','Nb','Mo','Tc','Ru','Rh','Pd','Ag','Cd','In','Sn']) if s.lower()==m.group(2).lower()), None)
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
        if self.path == '/api/reaction_config':
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
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress access log spam

if __name__ == '__main__':
    os.chdir(DIR)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), Handler) as httpd:
        print(f'HELIOS 3D viewer: http://localhost:{PORT}')
        print(f'From network:     http://192.168.1.101:{PORT}')
        print(f'Config source:    {DIGIOS_WORKING}')
        httpd.serve_forever()
