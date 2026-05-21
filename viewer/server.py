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
GEO_JSON       = os.path.join(DIR, 'helios_geometry.json')

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
        if self.path == '/api/data':
            # Placeholder for live EPICS data
            # Format: {"0": value, ...} keyed by detector ID
            self.send_json({})

        elif self.path == '/api/config':
            # Read detectorGeo.txt + reaction.dat from digios working dir
            result = {'ok': True, 'errors': []}
            if os.path.exists(DETECTOR_GEO):
                try:
                    result['detGeo'] = parse_detgeo(DETECTOR_GEO)
                except Exception as e:
                    result['errors'].append(f'detectorGeo: {e}')
            else:
                result['errors'].append(f'detectorGeo.txt not found at {DETECTOR_GEO}')

            if os.path.exists(REACTION_DAT):
                try:
                    result['reaction'] = parse_reaction(REACTION_DAT)
                except Exception as e:
                    result['errors'].append(f'reaction.dat: {e}')
            else:
                result['errors'].append(f'reaction.dat not found at {REACTION_DAT}')

            if os.path.exists(REACTION_CFG):
                try:
                    result['reactionConfig'] = parse_reaction_config(REACTION_CFG)
                except Exception as e:
                    result['errors'].append(f'reactionConfig.txt: {e}')
            else:
                result['errors'].append(f'reactionConfig.txt not found at {REACTION_CFG}')

            self.send_json(result)

        elif self.path == '/api/reaction_config':
            # Read helios_reaction.json
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
            # Run build_reaction.py to compute kinematics from helios_reaction.json
            try:
                python = sys.executable
                r = subprocess.run(
                    [python, BUILD_REACTION_PY, HELIOS_REACTION_JSON],
                    capture_output=True, text=True, timeout=15
                )
                if r.returncode == 0:
                    # Return updated reaction JSON
                    with open(HELIOS_REACTION_JSON) as f:
                        data = json.load(f)
                    self.send_json({'ok': True, 'output': r.stdout.strip(), 'reaction': data})
                else:
                    self.send_json({'ok': False, 'error': r.stderr.strip() or r.stdout.strip()}, 500)
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)

        elif self.path == '/api/rebuild_geo':
            # Regenerate helios_geometry.json from current detectorGeo.txt
            try:
                python = sys.executable
                r = subprocess.run(
                    [python, BUILD_GEO_PY, DETECTOR_GEO, GEO_JSON],
                    capture_output=True, text=True, timeout=10
                )
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
            # Save helios_reaction.json
            try:
                data = json.loads(body)
                with open(HELIOS_REACTION_JSON, 'w') as f:
                    json.dump(data, f, indent=2)
                self.send_json({'ok': True})
            except Exception as e:
                self.send_json({'ok': False, 'error': str(e)}, 500)
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress access log spam

if __name__ == '__main__':
    os.chdir(DIR)
    with socketserver.TCPServer(('', PORT), Handler) as httpd:
        httpd.allow_reuse_address = True
        print(f'HELIOS 3D viewer: http://localhost:{PORT}')
        print(f'From network:     http://192.168.1.101:{PORT}')
        print(f'Config source:    {DIGIOS_WORKING}')
        httpd.serve_forever()
