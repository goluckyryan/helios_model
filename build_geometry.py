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
    values = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            tok = line.split()[0]
            try:
                values.append(float(tok))
            except ValueError:
                values.append(tok)
    return values

def load_geometry(geo_file="detectorGeo.txt"):
    v = parse_detgeo(geo_file)
    idx = 0
    def nxt(): nonlocal idx; val = v[idx]; idx += 1; return val

    Bfield       = float(nxt())
    Bfield_theta = float(nxt())
    bore         = float(nxt())
    perpDist     = float(nxt())
    width        = float(nxt())
    length       = float(nxt())
    recoilPos    = float(nxt())
    recoilInner  = float(nxt())
    recoilOuter  = float(nxt())
    isCoincident = nxt()
    recoilPos1   = float(nxt())
    recoilPos2   = float(nxt())
    elumPos1     = float(nxt())
    elumPos2     = float(nxt())
    blocker      = float(nxt())
    firstPos     = float(nxt())
    eSigma       = float(nxt())
    zSigma       = float(nxt())
    facing       = str(nxt())
    mDet         = int(float(nxt()))

    # Remaining: pos[] offsets from firstPos (mm), nDet values
    pos = []
    while idx < len(v):
        try:
            pos.append(float(v[idx])); idx += 1
        except (ValueError, TypeError):
            idx += 1
    nDet = len(pos)

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
    # upstream (firstPos<0): detPos index nDet-1 is nearest → remap col = nDet-1-i
    # downstream (firstPos>0): detPos index 0 is nearest → col = i (already correct)
    for d in detectors:
        if firstPos < 0:
            d['col'] = nDet - 1 - d['col']  # flip: 0=nearest target
        # firstPos>0: col already 0=nearest

    # Re-ID: side order = -X(row2), -Y(row3), +X(row0), +Y(row1)
    # Within each side: det col 0 always nearest to target (physical array orientation)
    # firstPos < 0 (upstream): nearest target = highest detPos index = col nDet-1
    # firstPos > 0 (downstream): nearest target = lowest detPos index = col 0
    side_order = [2, 3, 0, 1]  # row values in ID order
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
    geo_path = sys.argv[1] if len(sys.argv) > 1 else "detectorGeo.txt"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "viewer/helios_geometry.json"

    if not os.path.exists(geo_path):
        print(f"ERROR: {geo_path} not found")
        sys.exit(1)

    geo = load_geometry(geo_path)
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
