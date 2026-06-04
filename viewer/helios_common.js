/**
 * helios_common.js — Shared utilities for HELIOS 3D viewer and Kinematics page
 *
 * ELEMENT_SYMBOLS, HELIOS_SHORTHAND, and spectrometer presets are loaded from
 * /helios_config.json at runtime — no hardcoded data here.
 */

// ── Runtime config (populated by initHeliosConfig) ────────────────────────
let ELEMENT_SYMBOLS = [];   // filled from helios_config.json
let HELIOS_SHORTHAND = {};  // filled from helios_config.json
let HELIOS_SS_PRESETS = {}; // filled from helios_config.json

// Promise that resolves when config is loaded — pages await this before using
// ELEMENT_SYMBOLS or HELIOS_SHORTHAND.
let _configResolve;
const heliosConfigReady = new Promise(res => { _configResolve = res; });

async function initHeliosConfig() {
  // helios_config.json is committed to the repo and served by viewer/server.py.
  // If this fetch fails the deployment is broken — fail loud, don't paper over it.
  const r = await fetch('/helios_config.json');
  if (!r.ok) throw new Error('helios_config.json unreachable: HTTP ' + r.status);
  const cfg = await r.json();
  ELEMENT_SYMBOLS   = cfg.element_symbols || [];
  HELIOS_SHORTHAND  = cfg.shorthands      || {};
  HELIOS_SS_PRESETS = cfg.spectrometers   || {};
  _configResolve();
}

// Kick off immediately — pages can await heliosConfigReady
initHeliosConfig();

/**
 * Resolve a nuclide symbol string (with shorthand support) to {A, Z, sym, name}.
 * Returns null if the string cannot be parsed.
 * Case-sensitive: 'n'=neutron(Z=0), 'N'=nitrogen(Z=7).
 */
function heliosParseNuclide(raw) {
  const resolved = HELIOS_SHORTHAND[raw] || HELIOS_SHORTHAND[raw?.toLowerCase()] || raw;
  if (!resolved) return null;
  const m = resolved.match(/^(\d+)([A-Za-z]+)$/);
  if (!m) return null;
  const A = parseInt(m[1]);
  const sym = m[2];
  // Title-case canonicalization: single-char kept as-is, multi-char → first upper rest lower
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
    const url = Z !== undefined
      ? `/api/mass?A=${AZ_or_A}&Z=${Z}`
      : `/api/mass?AZ=${encodeURIComponent(AZ_or_A)}`;
    const r = await fetch(url);
    const d = await r.json();
    return d.ok ? d : null;
  } catch(e) {
    return null;
  }
}

/**
 * Format nuclide label as superscript-A + symbol on a canvas.
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
