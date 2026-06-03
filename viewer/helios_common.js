/**
 * helios_common.js — Shared utilities for HELIOS 3D viewer and Kinematics page
 */

// ── Element symbols (Z=0..49) ──────────────────────────────────────────────
const ELEMENT_SYMBOLS = [
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
];

// Shorthand aliases → canonical nuclide symbols
const HELIOS_SHORTHAND = {
  'n':'1n', 'p':'1H',
  'd':'2H', 'D':'2H',
  't':'3H', 'T':'3H',
  'h':'3He', 'H':'3He', '3he':'3He',
  'a':'4He', 'A':'4He', 'alpha':'4He', 'Alpha':'4He',
};

/**
 * Resolve a nuclide symbol string (with shorthand support) to {A, Z, sym, name}.
 * Returns null if the string cannot be parsed.
 */
function heliosParseNuclide(raw) {
  const resolved = HELIOS_SHORTHAND[raw] || HELIOS_SHORTHAND[raw?.toLowerCase()] || raw;
  if (!resolved) return null;
  const m = resolved.match(/^(\d+)([A-Za-z]+)$/);
  if (!m) return null;
  const A = parseInt(m[1]);
  const sym = m[2];
  // Case-sensitive lookup: canonicalize to Title-case (e.g. "n"->"n", "N"->"N", "he"->"He")
  // ELEMENT_SYMBOLS[0]="n" (neutron), ELEMENT_SYMBOLS[7]="N" (nitrogen) -- must not conflate them.
  const symCanon = sym.length === 1 ? sym : sym[0].toUpperCase() + sym.slice(1).toLowerCase();
  const Z = ELEMENT_SYMBOLS.findIndex(s => s === symCanon);
  if (Z < 0) return null;
  return { A, Z, sym: ELEMENT_SYMBOLS[Z], name: A + ELEMENT_SYMBOLS[Z], resolved };
}

/**
 * Format nuclide as "AEl" string.
 */
function heliosNuclideName(A, Z) {
  return A + (ELEMENT_SYMBOLS[Z] || ('Z' + Z));
}

/**
 * Look up nuclear mass from /api/mass endpoint.
 * Returns: {ok, A, Z, mass, name, Sn, Sp, Sa} or null on failure.
 */
async function heliosMassLookup(AZ_or_A, Z) {
  try {
    let url;
    if (Z !== undefined) {
      url = `/api/mass?A=${AZ_or_A}&Z=${Z}`;
    } else {
      url = `/api/mass?AZ=${encodeURIComponent(AZ_or_A)}`;
    }
    const r = await fetch(url);
    const d = await r.json();
    return d.ok ? d : null;
  } catch(e) {
    return null;
  }
}

/**
 * Load geometry + reaction config from digios via server API.
 * Returns { detGeo, reactionConfig, reaction, errors } or null on failure.
 */
async function heliosLoadConfig() {
  try {
    const r = await fetch('/api/config');
    return await r.json();
  } catch(e) {
    return null;
  }
}

/**
 * Run build_reaction.py via server API.
 * Returns { ok, reaction, output } or null on failure.
 */
async function heliosBuildReaction(rxData) {
  // Single POST to /api/build_reaction — saves rxData + runs build_reaction.py
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 15000);
    const r = await fetch('/api/build_reaction', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rxData),
      signal: ctrl.signal
    });
    clearTimeout(timeout);
    return await r.json();
  } catch(e) {
    return { ok: false, error: String(e) };
  }
}

/**
 * Format nuclide label as superscript-A + symbol on a canvas.
 * Returns a canvas element (no THREE dependency).
 */
function heliosMakeNuclideCanvas(rawLabel, colorHex, w=256, h=96) {
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = colorHex;
  const m = rawLabel.match(/^(\d+)([A-Za-z]+)$/);
  if (m) {
    const A = m[1], sym = m[2];
    ctx.font = 'Bold 60px Consolas, monospace';
    ctx.textAlign = 'left';
    const symW = ctx.measureText(sym).width;
    const totalW = 36 + symW;
    const xOff = (w - totalW) / 2;
    ctx.font = 'Bold 36px Consolas, monospace';
    ctx.fillText(A, xOff, 44);
    ctx.font = 'Bold 60px Consolas, monospace';
    ctx.fillText(sym, xOff + 36, 80);
  } else {
    ctx.font = 'Bold 56px Consolas, monospace';
    ctx.textAlign = 'center';
    ctx.fillText(rawLabel, w/2, 72);
  }
  return canvas;
}
