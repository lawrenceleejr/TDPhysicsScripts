#!/usr/bin/env node
/* Export a Feynman field for the TouchDesigner scene.
 *
 *   node tools/export_field.mjs                          # 1920x1080, seed 7
 *   node tools/export_field.mjs --w 1080 --h 1920        # portrait
 *   node tools/export_field.mjs --seed 12 --scale 2.8
 *   node tools/export_field.mjs --all                    # the set in fields/
 *
 * The generator is not reimplemented in Python. tools/network.js is a copy of
 * the one behind the poster and the website, and it is what runs here: blue
 * noise at a set spacing, Delaunay so the mesh is planar with no crossings,
 * thinned so no vertex is left with fewer than three legs, legs capped and
 * tight angles opened out, then line types assigned so every vertex is a legal
 * Standard Model interaction. Porting that to Python would have meant keeping
 * two copies of some careful rules in step; exporting from the original means
 * TouchDesigner gets exactly the audited article.
 *
 * What Python does at run time is the part that is simple and has to be live:
 * the flood, and turning lines into ribbons.
 *
 * The wave along a boson is baked in here too — it is the generator's own
 * pathPoints — so nothing downstream has to know how a boson is drawn. Fermions
 * and scalars are straight, so they ship as their two endpoints and the shader
 * dashes the scalars by arc length.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';

const ROOT = resolve(new URL('..', import.meta.url).pathname);
const a = process.argv.slice(2);
const num = (k, d) => { const i = a.indexOf('--' + k); return i < 0 ? d : Number(a[i + 1]); };
const has = k => a.indexOf('--' + k) >= 0;

const win = {};
new Function('window', readFileSync(resolve(ROOT, 'tools/network.js'), 'utf8'))(win);
const N = win.SMMNet;

function exportOne(W, H, seed, scale, spacing) {
  const net = N.build({
    w: W, h: H, zones: [], seed,
    seeds: [{ x: W * 0.5, y: H * 0.5 }],
    spacing, keep: 0.72, speed: 120, darts: 120000,
    pad: -scale * 26,                       // let the mesh run off the edges
    clearance: 0, clearanceAt: () => 0,
    scaleAt: () => scale,
    cornerR: 0,
    maxLegs: 4, minSep: 0.42, minSepHard: 0.25, minComponent: 4,
    fermionShare: 0.5, higgsShare: 0.18, splitQuads: false,
    higgsPure: 3, higgsQuartics: 1,
  });

  // Which vertices end a line in the vacuum, and so take an × rather than a dot.
  const isX = new Array(net.verts.length).fill(0);
  net.edges.forEach(e => { if (e.xa) isX[e.a] = 1; if (e.xb) isX[e.b] = 1; });

  const edges = net.edges.map(e => {
    const row = { a: e.a, b: e.b, t: e.type, s: +(e.s || scale).toFixed(4),
                  len: +e.len.toFixed(3) };
    if (e.type === 'b') {
      // The drawn wave, from the generator itself, thinned. pathPoints asks for
      // 28 points per cycle, which is what a phone needs when the whole field
      // is scaled down; here a cycle is 42px across and every other point is
      // 3px apart, which is smoother than a projector can show. It also halves
      // the geometry a Script SOP has to be handed one point at a time.
      const full = N.pathPoints(net, e);
      const step = Math.max(1, num('wavestep', 2));
      const pts = [];
      for (let i = 0; i < full.length; i += step) pts.push(full[i]);
      if (pts[pts.length - 1] !== full[full.length - 1]) pts.push(full[full.length - 1]);
      row.pts = pts.map(p => [+p[0].toFixed(2), +p[1].toFixed(2)]);
    }
    return row;
  });

  const tally = {};
  const legs = net.verts.map(() => []);
  net.edges.forEach(e => { legs[e.a].push(e.type); legs[e.b].push(e.type); });
  legs.forEach(l => {
    if (l.length < 2) return;
    const k = l.slice().sort().join('');
    tally[k] = (tally[k] || 0) + 1;
  });
  const LEGAL = new Set(['bff', 'bbff', 'ffh', 'bbb', 'bbbb', 'bbh', 'bbhh', 'hhh', 'hhhh']);
  const illegal = Object.entries(tally).filter(([k]) => !LEGAL.has(k));
  if (illegal.length) {
    console.error(`  VERTEX FAULT: ${illegal.map(([k, n]) => k + ' x' + n).join(', ')}`);
    process.exitCode = 1;
  }

  return {
    w: W, h: H, seed, scale,
    verts: net.verts.map(p => [+p[0].toFixed(2), +p[1].toFixed(2)]),
    isX, edges, vertices: tally,
  };
}

const CASES = has('all')
  ? [
      ['16x9', 1920, 1080, 7], ['16x9-b', 1920, 1080, 21], ['16x9-c', 1920, 1080, 34],
      ['9x16', 1080, 1920, 7], ['1x1', 1440, 1440, 7], ['21x9', 2560, 1080, 7],
    ]
  : [[`${num('w', 1920)}x${num('h', 1080)}-${num('seed', 7)}`,
      num('w', 1920), num('h', 1080), num('seed', 7)]];

mkdirSync(resolve(ROOT, 'fields'), { recursive: true });
for (const [name, W, H, seed] of CASES) {
  // The website runs SCALE 2.8 and SPACING 24, so its points sit 67px apart at
  // 1080p and its waves and dashes are sized off 2.8. Matching both is what
  // makes this the same field rather than a denser cousin.
  const scale = num('scale', 2.8);
  // Spacing is asked for as a share of the short side and divided back out of
  // the scale, because build() multiplies the two together — otherwise a
  // portrait field comes out at a different density from a landscape one.
  const spacing = num('spacing', Math.min(W, H) * 0.062) / scale;
  const field = exportOne(W, H, seed, scale, spacing);
  const out = resolve(ROOT, `fields/${name}.json`);
  writeFileSync(out, JSON.stringify(field));
  const kb = (JSON.stringify(field).length / 1024).toFixed(0);
  console.log(`  ${name.padEnd(8)} ${W}x${H} seed ${String(seed).padEnd(3)} `
    + `${String(field.edges.length).padStart(4)} lines, ${String(field.verts.length).padStart(4)} vertices, `
    + `${kb} KB  -> fields/${name}.json`);
  console.log(`           vertices: ${Object.entries(field.vertices)
    .sort((x, y) => y[1] - x[1]).map(([k, n]) => `${k} ${n}`).join(', ')}`);
}
