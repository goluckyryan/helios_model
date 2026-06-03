#!/usr/bin/env python3
"""
build_reaction.py — HELIOS reaction kinematics calculator

Reads helios_reaction.json, looks up nuclear masses from AME2020 (mass20.txt),
computes reaction kinematics, and writes/updates:
  - helios_reaction.json  (adds computed fields)
  - reaction.dat          (digios-compatible format)

Usage:
  python3 build_reaction.py [helios_reaction.json] [mass_table.txt]

Default mass table search order:
  1. ./mass20.txt
  2. ./mass16.txt
  3. ~/digios/analysis/Cleopatra/mass20.txt
"""

import json, math, os, sys, re

AMU   = 931.494102  # MeV/c^2
M_e   = 0.510998950 # MeV/c^2 (electron mass)
M_p   = 938.272046  # MeV/c^2
M_n   = 939.565379  # MeV/c^2

def find_mass_table():
    candidates = [
        os.path.join(os.path.dirname(__file__), 'mass20.txt'),
        os.path.join(os.path.dirname(__file__), 'mass16.txt'),
        os.path.expanduser('~/digios/analysis/Cleopatra/mass20.txt'),
        os.path.expanduser('~/digios/analysis/Cleopatra/mass16.txt'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def parse_mass_table(path):
    """Parse AME mass table. Returns dict {(Z,A): mass_MeV} nuclear mass (no electrons)."""
    masses = {}
    with open(path, encoding='latin-1') as f:
        for line in f:
            if len(line) < 60:
                continue
            # Skip header lines (non-data)
            # Data lines: cols 1-4 = N-Z, 5-9 = N, 10-14 = Z, 15-19 = A, 20-23 = EL
            # Mass excess at col 30-41 (keV), may contain '#' for extrapolated
            try:
                # Use fixed-width parsing matching mass20.txt format
                # Detect data lines: they have integer N,Z,A in first columns
                nz_str = line[1:5].strip()
                n_str  = line[5:9].strip()
                z_str  = line[9:14].strip()
                a_str  = line[14:19].strip()
                if not (nz_str and a_str and z_str and n_str):
                    continue
                A = int(a_str)
                Z = int(z_str)
                N = int(n_str)
                # Mass excess field (keV), cols 29-41 approximately
                # Actually: mass excess starts around col 29 in mass20.txt
                me_str = line[29:41].strip().replace('#','').replace('*','')
                if not me_str:
                    continue
                mass_excess_keV = float(me_str)
                # Nuclear mass (no electrons) = Z*M_p + N*M_n + mass_excess/1000 - Z*M_e
                # (mass excess defined for atomic mass: M_atom = A*u + Δ/c²)
                # Atomic mass [MeV] = A*AMU + mass_excess_keV/1000
                # Nuclear mass = Atomic mass - Z * M_e  (ignore electron binding energies)
                atomic_mass_MeV = A * AMU + mass_excess_keV / 1000.0
                nuclear_mass_MeV = atomic_mass_MeV - Z * M_e
                masses[(Z, A)] = nuclear_mass_MeV
            except (ValueError, IndexError):
                continue
    return masses

def get_mass(masses, Z, A, label='?'):
    """Get nuclear mass in MeV. Falls back to simple formula if not in table."""
    key = (Z, A)
    if key in masses:
        return masses[key]
    # Fallback: simple estimate
    N = A - Z
    mass = Z * M_p + N * M_n - 15.56*A + 17.23*A**(2/3) + 0.697*Z*(Z-1)/A**(1/3) + 23.285*(N-Z)**2/A
    print(f"  WARNING: ({Z},{A}) [{label}] not in mass table, using rough estimate: {mass:.4f} MeV")
    return mass

# Single source of truth for element symbols (Z=0..118)
ELEMENT_SYMBOLS = [
    'n','H','He','Li','Be','B','C','N','O','F','Ne',
    'Na','Mg','Al','Si','P','S','Cl','Ar','K','Ca',
    'Sc','Ti','V','Cr','Mn','Fe','Co','Ni','Cu','Zn',
    'Ga','Ge','As','Se','Br','Kr','Rb','Sr','Y','Zr',
    'Nb','Mo','Tc','Ru','Rh','Pd','Ag','Cd','In','Sn',
    'Sb','Te','I','Xe','Cs','Ba','La','Ce','Pr','Nd',
    'Pm','Sm','Eu','Gd','Tb','Dy','Ho','Er','Tm','Yb',
    'Lu','Hf','Ta','W','Re','Os','Ir','Pt','Au','Hg',
    'Tl','Pb','Bi','Po','At','Rn','Fr','Ra','Ac','Th',
    'Pa','U','Np','Pu','Am','Cm','Bk','Cf','Es','Fm',
    'Md','No','Lr','Rf','Db','Sg','Bh','Hs','Mt','Ds',
    'Rg','Cn','Nh','Fl','Mc','Lv','Ts','Og'
]

def element_symbol(Z):
    return ELEMENT_SYMBOLS[Z] if 0 <= Z < len(ELEMENT_SYMBOLS) else f'Z{Z}'

def compute_kinematics(reaction, masses):
    """
    reaction: dict with beam_A, beam_Z, target_A, target_Z,
              recoil_light_A, recoil_light_Z, beam_energy_MeVu
    Returns: dict with all kinematic quantities for reaction.dat
    """
    Aa, Za = int(reaction['beam_A']),         int(reaction['beam_Z'])
    At, Zt = int(reaction['target_A']),       int(reaction['target_Z'])
    Ab, Zb = int(reaction['recoil_light_A']), int(reaction['recoil_light_Z'])

    # Heavy recoil by conservation
    AB = Aa + At - Ab
    ZB = Za + Zt - Zb

    # Labels
    sym_a = element_symbol(Za); sym_t = element_symbol(Zt)
    sym_b = element_symbol(Zb); sym_B = element_symbol(ZB)
    # Inverse kinematics convention: beam(target, light recoil) heavy recoil
    reaction_str = f"{Aa}{sym_a}({At}{sym_t},{Ab}{sym_b}){AB}{sym_B}"

    # Masses (MeV)
    Ma = get_mass(masses, Za, Aa, f'{Aa}{sym_a}')  # beam
    Mt = get_mass(masses, Zt, At, f'{At}{sym_t}')  # target
    Mb = get_mass(masses, Zb, Ab, f'{Ab}{sym_b}')  # light recoil
    MB = get_mass(masses, ZB, AB, f'{AB}{sym_B}')  # heavy recoil

    Elab_MeVu = float(reaction['beam_energy_MeVu'])
    Elab = Elab_MeVu * Aa  # total lab KE of beam

    # Total CM energy
    Ea_total = Elab + Ma   # beam total energy in lab
    Et_total = Mt          # target at rest
    # Ecm^2 = (Ea+Et)^2 - pa^2 = (Ea+Mt)^2 - (Ea^2-Ma^2)
    pa = math.sqrt(Ea_total**2 - Ma**2)
    Ecm_sq = (Ea_total + Mt)**2 - pa**2
    Ecm = math.sqrt(Ecm_sq)

    # CM beta and gamma
    Ptot = pa  # total lab momentum = beam momentum (target at rest)
    Etot = Ea_total + Mt
    betaCM = Ptot / Etot
    gammaCM = 1.0 / math.sqrt(1 - betaCM**2)

    # Q-value (ground state)
    Q = Ma + Mt - Mb - MB

    # alpha = slope / betaRect (from HELIOS_LIB)
    # alpha = q_b * B * c / (2*pi) * (1/betaRect)
    # but reaction.dat stores alpha = slope/betaRect where
    # slope = q_b * |B| * c / (2*pi) in MeV/mm/T ... simplified as ratio
    # In digios: alpha = 299.792458 * Zb * |B| / (2*pi) * (1/1000) / betaRect
    # where betaRect = p_b_cm / (gamma_cm * M_b) ... complex. Store betaCM and Ecm instead.
    # For compatibility, set alpha=0 (not used in viewer)
    alpha = 0.0

    print(f"\nReaction: {reaction_str}")
    print(f"  Ma={Ma:.4f}  Mt={Mt:.4f}  Mb={Mb:.4f}  MB={MB:.4f} MeV")
    print(f"  Elab={Elab:.2f} MeV  Ecm={Ecm:.4f} MeV")
    print(f"  betaCM={betaCM:.8f}  gammaCM={gammaCM:.6f}")
    print(f"  Q={Q:.4f} MeV  {'exothermic' if Q>0 else 'endothermic'}")
    print(f"  Heavy recoil: {AB}{sym_B} (Z={ZB})")

    return {
        'mass_b':    Mb,
        'charge_b':  Zb,
        'betaCM':    betaCM,
        'Ecm':       Ecm,
        'mass_B':    MB,
        'charge_B':  ZB,
        'alpha':     alpha,
        'Q':         Q,
        'beam_label':   f'{Aa}{sym_a}',
        'target_label': f'{At}{sym_t}',
        'recoil_light_label': f'{Ab}{sym_b}',
        'recoil_heavy_label': f'{AB}{sym_B}',
        'reaction_str': reaction_str,
        'Ma': Ma, 'Mt': Mt, 'Mb': Mb, 'MB': MB,
    }

def write_reaction_dat(result, path):
    with open(path, 'w') as f:
        f.write(f"{result['mass_b']:.4f}         //mass_b\n")
        f.write(f"{int(result['charge_b'])}                //charge_b\n")
        f.write(f"{result['betaCM']:.8f}       //betaCM\n")
        f.write(f"{result['Ecm']:.4f}       //Ecm\n")
        f.write(f"{result['mass_B']:.4f}       //mass_B\n")
        f.write(f"{int(result['charge_B'])}                //charge_B\n")
        f.write(f"{result['alpha']:.4f}       //alpha=slope/betaRect\n")
    print(f"  Written: {path}")

if __name__ == '__main__':
    # Locate files
    reaction_json = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), 'helios_reaction.json')
    mass_table_arg = sys.argv[2] if len(sys.argv) > 2 else None

    mass_path = mass_table_arg or find_mass_table()
    if not mass_path:
        print("ERROR: No mass table found. Place mass20.txt in the helios_model folder.")
        sys.exit(1)

    if not os.path.exists(reaction_json):
        print(f"ERROR: {reaction_json} not found.")
        sys.exit(1)

    print(f"Mass table: {mass_path}")
    print(f"Reaction:   {reaction_json}")

    masses = parse_mass_table(mass_path)
    print(f"Loaded {len(masses)} nuclides from mass table.")

    with open(reaction_json) as f:
        reaction = json.load(f)

    result = compute_kinematics(reaction, masses)

    # Update helios_reaction.json with computed values
    reaction.update({
        'mass_b':    result['mass_b'],
        'charge_b':  result['charge_b'],
        'betaCM':    result['betaCM'],
        'Ecm':       result['Ecm'],
        'mass_B':    result['mass_B'],
        'charge_B':  result['charge_B'],
        'Ma':        result['Ma'],
        'Mt':        result['Mt'],
        'Q':         result['Q'],
        'beam_label':   result['beam_label'],
        'target_label': result['target_label'],
        'recoil_light_label': result['recoil_light_label'],
        'recoil_heavy_label': result['recoil_heavy_label'],
        'reaction_str': result['reaction_str'],
    })
    with open(reaction_json, 'w') as f:
        json.dump(reaction, f, indent=2)
    print(f"  Updated: {reaction_json}")

    # Write reaction.dat in same folder as reaction_json
    dat_path = os.path.join(os.path.dirname(reaction_json), 'reaction.dat')
    write_reaction_dat(result, dat_path)
    print("\nDone.")
