#!/usr/bin/env python3
"""
HELIOS Silicon Array 3D Geometry Builder
Generates helios_geometry.json from detectorGeo.txt

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
        result['recoil_light_label'] = f"{int(result.get('recoil_light_A',1))}{element_symbol(int(result.get('recoil_light_Z',1)))}"
    return result

def load_geometry(geo_file="detectorGeo.txt"):
    v = parse_detgeo(geo_file)  # now returns a named dict

    Bfield       = float(v['Bfield'])
    Bfield_theta = float(v.get('Bfield_theta', 0))
    bore         = float(v['bore'])
    perpDist     = float(v['perpDist'])
    width        = float(v['width'])
    length       = float(v['length'])
    recoilPos    = float(v['recoilPos'])
    recoilInner  = float(v.get('recoilInner', 10.0))
    recoilOuter  = float(v.get('recoilOuter', 40.2))
    isCoincident = v.get('isCoincident', False)
    recoilPos1   = float(v.get('recoilPos1', 0))
    recoilPos2   = float(v.get('recoilPos2', 0))
    elumPos1     = float(v.get('elumPos1', 0))
    elumPos2     = float(v.get('elumPos2', 0))
    blocker      = float(v.get('blocker', 0))
    firstPos     = float(v['firstPos'])
    eSigma       = float(v.get('eSigma', 0))
    zSigma       = float(v.get('zSigma', 0))
    facing       = str(v.get('facing', 'Out'))
    mDet         = int(float(v.get('mDet', 4)))

    pos  = v['detPos']
    nDet = v['nDet']

    # Compute absolute detector positions (near edge) — mirrors AnalysisLibrary.h
    detPos = []
    for det_id in range(nDet):
        if firstPos > 0:
            detPos.append(firstPos + pos[nDet - 1 - det_id])
        else:
            detPos.append(firstPos - pos[nDet - 1 - det_id])

    # detPos[id] is the "near" edge of detector id
    # For firstPos < 0 (upstream): near edge is the downstream end of each det
    #   → far (upstream) end = detPos[id] - length
    # For firstPos > 0 (downstream): near edge is upstream end
    #   → far end = detPos[id] + length

    zMin = min(detPos) - (length if firstPos < 0 else 0)
    zMax = max(detPos) + (length if firstPos > 0 else 0)

    detectors = []
    det_id = 0
    for i, z_near in enumerate(detPos):
        for j in range(mDet):
            phi = 2 * math.pi / mDet * j  # azimuthal angle (rad)
            # Center Z
            if firstPos < 0:
                z_center = z_near - length / 2
            else:
                z_center = z_near + length / 2

            x = perpDist * math.cos(phi)
            y = perpDist * math.sin(phi)

            detectors.append({
                "id": -1,   # assigned below
                "row": j,   # row = side (0=+X, 1=+Y, 2=-X, 3=-Y) — 4 rows
                "col": i,   # col = detPos index; remapped to dist-from-target below — 6 cols
                "phi_deg": round(math.degrees(phi), 2),
                "z_near": round(z_near, 2),
                "z_center": round(z_center, 2),
                "x": round(x, 4),
                "y": round(y, 4),
                "z": round(z_center, 4),
                "length": length,
                "width": width,
            })
            det_id += 1

    # Remap col so col 0 = nearest to target (physical array orientation)
    # upstream (firstPos<0): detPos[nDet-1] has highest z (nearest target) → flip col
    # downstream (firstPos>0): detPos[nDet-1] has highest z (furthest from target=0) → flip col
    # Both cases: detPos index nDet-1 is furthest from target → always flip
    for d in detectors:
        d['col'] = nDet - 1 - d['col']  # flip: col 0 = nearest target always

    # Re-ID: side order depends on array orientation
    # Upstream (firstPos<0): array faces target from upstream — side order -X,-Y,+X,+Y
    # Downstream (firstPos>0): array rotated 180° around Y — side order +X,+Y,-X,-Y
    if firstPos < 0:
        side_order = [2, 3, 0, 1]  # -X(row2), -Y(row3), +X(row0), +Y(row1)
    else:
        side_order = [0, 1, 2, 3]  # +X(row0), +Y(row1), -X(row2), -Y(row3)
    new_id = 0
    # col 0 = nearest target always; iterate col 0 first so detID%nDet==0 = nearest
    col_order = range(0, nDet)
    for side_row in side_order:
        for col_idx in col_order:
            for d in detectors:
                if d['row'] == side_row and d['col'] == col_idx:
                    d['id'] = new_id
                    new_id += 1
                    break

    geometry = {
        "Bfield": Bfield,
        "bore": bore,
        "perpDist": perpDist,
        "width": width,
        "length": length,
        "recoilPos": recoilPos,
        "recoilInner": recoilInner,
        "recoilOuter": recoilOuter,
        "recoilPos1": recoilPos1,
        "recoilPos2": recoilPos2,
        "elumPos1": elumPos1,
        "elumPos2": elumPos2,
        "blocker": blocker,
        "firstPos": firstPos,
        "facing": facing,
        "nDet": nDet,
        "mDet": mDet,
        "zMin": zMin,
        "zMax": zMax,
        "detectors": detectors,
    }
    return geometry

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('geo_path', nargs='?', default='detectorGeo.txt')
    parser.add_argument('out_path', nargs='?', default='helios_geometry.json')
    parser.add_argument('--firstPos', type=float, default=None, help='Override firstPos (mm)')
    parser.add_argument('--recoilPos', type=float, default=None, help='Override recoilPos (mm)')
    args = parser.parse_args()

    if not os.path.exists(args.geo_path):
        print(f"ERROR: {args.geo_path} not found")
        sys.exit(1)

    geo = load_geometry(args.geo_path)
    # Apply overrides
    if args.firstPos is not None:
        old_fp  = geo['firstPos']
        new_fp  = args.firstPos
        length  = geo['length']
        same_side = (old_fp < 0) == (new_fp < 0)
        if same_side:
            # Simple shift — sign didn't change, near/center relationship unchanged
            delta = new_fp - old_fp
            geo['firstPos'] = new_fp
            geo['zMin'] += delta
            geo['zMax'] += delta
            for d in geo['detectors']:
                d['z_near']   += delta
                d['z_center'] += delta
                d['z']        += delta
        else:
            # Sign crossed zero: downstream↔upstream flip.
            # Reconstruct z_near from each detector's offset from old firstPos,
            # then recompute z_near and z_center under new sign convention.
            geo['firstPos'] = new_fp
            for d in geo['detectors']:
                # Recover the original offset magnitude
                offset = abs(d['z_near'] - old_fp)
                if new_fp < 0:
                    # Now upstream: z_near = firstPos - offset (downstream edge)
                    # z_center = z_near - length/2
                    d['z_near']   = round(new_fp - offset, 4)
                    d['z_center'] = round(d['z_near'] - length / 2, 4)
                else:
                    # Now downstream: z_near = firstPos + offset (upstream edge)
                    # z_center = z_near + length/2
                    d['z_near']   = round(new_fp + offset, 4)
                    d['z_center'] = round(d['z_near'] + length / 2, 4)
                d['z'] = d['z_center']
            znears = [d['z_near'] for d in geo['detectors']]
            geo['zMin'] = min(znears) - (length if new_fp < 0 else 0)
            geo['zMax'] = max(znears) + (length if new_fp > 0 else 0)
        print(f"  Overriding firstPos to {args.firstPos} mm")
    if args.recoilPos is not None:
        geo['recoilPos'] = args.recoilPos
        print(f"  Overriding recoilPos to {args.recoilPos} mm")
    geo_path, out_path = args.geo_path, args.out_path
    with open(out_path, "w") as f:
        json.dump(geo, f, indent=2)

    n = geo['nDet'] * geo['mDet']
    print(f"Written {n} detectors ({geo['nDet']} rows × {geo['mDet']} phi) to {out_path}")
    print(f"  B-field: {geo['Bfield']} T  |  bore: {geo['bore']} mm")
    print(f"  perpDist: {geo['perpDist']} mm  |  det: {geo['length']}×{geo['width']} mm")
    print(f"  firstPos: {geo['firstPos']} mm  |  facing: {geo['facing']}")
    print(f"  Z range: {geo['zMin']:.1f} to {geo['zMax']:.1f} mm")
    print(f"  recoil at z={geo['recoilPos']} mm")
    print("  Detector rows (z_near = downstream edge):")
    seen = set()
    for d in geo['detectors']:
        if d['row'] not in seen:
            seen.add(d['row'])
            print(f"    row {d['row']}: z_near={d['z_near']} mm  z_center={d['z_center']} mm")
