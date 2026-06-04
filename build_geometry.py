#!/usr/bin/env python3
"""
HELIOS Silicon Array 3D Geometry parser.
Parses digios detectorGeo.txt + reactionConfig.txt for viewer/server.py.

Coordinate System:
  Z = beam axis (positive = downstream / forward)
  X = horizontal, Y = vertical
  Detectors at radius=perpDist from beam axis, mDet per z-position

Units: mm throughout

detPos calculation (mirrors AnalysisLibrary.h LoadDetectorGeo):
  pos[] = offsets read from file (in order of increasing offset)
  For firstPos < 0 (upstream):
    detPos[id] = firstPos - pos[nDet-1-id]
    → near edge of each detector (most-upstream det gets largest offset subtracted)
  For firstPos > 0 (downstream):
    detPos[id] = firstPos + pos[nDet-1-id]

  zMin = min(detPos) - length    (upstream case)
  zMax = max(detPos)             (upstream case, near edge is the far edge)
"""

import json, math, sys, os

def parse_detgeo(path):
    """Parse detectorGeo.txt into a named dict (single source of truth)."""
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
    det_pos = []
    for v in values[len(keys):]:
        try:
            det_pos.append(float(v))
        except ValueError:
            pass
    result['detPos'] = det_pos
    result['nDet'] = len(det_pos)
    first = result.get('firstPos', 0)
    length = result.get('length', 0)
    if det_pos:
        if first < 0:
            result['zMin'] = first - det_pos[-1] - length
            result['zMax'] = first - det_pos[0]
        else:
            result['zMin'] = first + det_pos[0]
            result['zMax'] = first + det_pos[-1] + length
    return result


def parse_reaction_config(path):
    """Parse reactionConfig.txt into a named dict (single source of truth)."""
    from build_reaction import element_symbol
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
    if 'beam_A' in result and 'beam_Z' in result:
        result['beam_label']         = f"{int(result['beam_A'])}{element_symbol(int(result['beam_Z']))}"
        result['target_label']       = f"{int(result.get('target_A',2))}{element_symbol(int(result.get('target_Z',1)))}"
        result['light_label']        = f"{int(result.get('recoil_light_A',1))}{element_symbol(int(result.get('recoil_light_Z',1)))}"
    return result

# load_geometry() and CLI __main__ removed: viewer/server.py uses parse_detgeo directly
