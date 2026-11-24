/* =====================================================================
   engine.js  --  in-browser re-implementation of the paper's pipeline
   (generator per Table 1, detectors D1-D5 per Table 3, metrics per Eq 6-8)
   Pure JS, no dependencies. Works in the browser (window.GE) and in Node.
   NOTE: uses its own RNG, so numbers are statistically close to, but not
   identical with, the paper's NetworkX-based results.
   ===================================================================== */
(function (root) {
  'use strict';

  /* ---------- seeded RNG (mulberry32) ---------- */
  function makeRng(seed) {
    let a = (seed * 2654435761 + 1013904223) >>> 0;
    const r = function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    r.int = (lo, hi) => lo + Math.floor(r() * (hi - lo + 1)); // inclusive
    r.pick = (arr) => arr[Math.floor(r() * arr.length)];
    r.shuffle = (arr) => { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; };
    return r;
  }

  /* label ids */
  const BENIGN = 0, FANOUT = 1, FANIN = 2, RING = 3, CLUSTER = 4;
  const TYPE_NAMES = ['Honest account', 'Fan-out hub', 'Fan-in hub', 'Cyclic ring', 'Isolated cluster'];

  /* ---------- graph container ---------- */
  function Graph() {
    this.n = 0; this.out = []; this.inn = []; this.label = []; this.edgeSet = new Set(); this.m = 0;
  }
  Graph.prototype.addNode = function (lab) {
    this.out.push(new Set()); this.inn.push(new Set()); this.label.push(lab || 0); return this.n++;
  };
  Graph.prototype.addEdge = function (u, v) {
    if (u === v) return false;
    const key = u * 1048576 + v;
    if (this.edgeSet.has(key)) return false;
    this.edgeSet.add(key); this.out[u].add(v); this.inn[v].add(u); this.m++; return true;
  };
  Graph.prototype.hasEdge = function (u, v) { return this.edgeSet.has(u * 1048576 + v); };
  Graph.prototype.deg = function (v) { return this.out[v].size + this.inn[v].size; };
  Graph.prototype.edges = function () {
    const e = []; for (let u = 0; u < this.n; u++) for (const v of this.out[u]) e.push([u, v]); return e;
  };
  /* undirected simple neighbourhood */
  Graph.prototype.und = function (v) {
    const s = new Set(this.out[v]); for (const w of this.inn[v]) s.add(w); return s;
  };
  Graph.prototype.hasUnd = function (u, v) { return this.hasEdge(u, v) || this.hasEdge(v, u); };

  /* ---------- directed scale-free backbone (Bollobas et al.) ---------- */
  function scaleFree(g, nTarget, rng, P) {
    const alpha = P.alpha, beta = P.beta, dIn = P.deltaIn, dOut = P.deltaOut;
    const ids = [];
    const outStubs = [], inStubs = [];
    function add(u, v) { // always adds a stub (like multigraph), but stores edge once
      g.addEdge(u, v); outStubs.push(u); inStubs.push(v);
    }
    function pickIn() {
      const tot = inStubs.length + dIn * ids.length;
      if (rng() * tot < inStubs.length) return inStubs[Math.floor(rng() * inStubs.length)];
      return ids[Math.floor(rng() * ids.length)];
    }
    function pickOut() {
      const tot = outStubs.length + dOut * ids.length;
      if (dOut === 0 || rng() * tot < outStubs.length) return outStubs[Math.floor(rng() * outStubs.length)];
      return ids[Math.floor(rng() * ids.length)];
    }
    for (let i = 0; i < 3; i++) ids.push(g.addNode(BENIGN));
    add(ids[0], ids[1]); add(ids[1], ids[2]); add(ids[2], ids[0]);
    while (ids.length < nTarget) {
      const r = rng();
      if (r < alpha) { const v = g.addNode(BENIGN); const w = pickIn(); ids.push(v); add(v, w); }
      else if (r < alpha + beta) { const v = pickOut(); const w = pickIn(); if (v !== w) add(v, w); }
      else { const v = g.addNode(BENIGN); const w = pickOut(); ids.push(v); add(w, v); }
    }
    return ids;
  }

  /* ---------- the generator (Table 1) ---------- */
  const DEFAULTS = { alpha: 0.41, beta: 0.54, gamma: 0.05, deltaIn: 0.2, deltaOut: 0, smallFrac: 0.06 };

  function generate(opts) {
    const N = opts.N || 1000, f = opts.f == null ? 0.05 : opts.f, seed = opts.seed || 0;
    const rng = makeRng(seed);
    const g = new Graph();
    const rnd = (x) => { const fl = Math.floor(x); const d = x - fl; return d > 0.5 + 1e-9 ? fl + 1 : d < 0.5 - 1e-9 ? fl : (fl % 2 === 0 ? fl : fl + 1); }; // round-half-even like Python
    const A = rnd(f * N);
    let nOut = rnd(0.25 * A), nIn = rnd(0.25 * A);
    const ringBudget = rnd(0.30 * A);
    let clusterBudget = A - nOut - nIn - ringBudget;
    // split budgets into structures
    function split(budget, lo, hi) {
      const sizes = []; let rem = budget;
      while (rem >= lo) {
        const s = rng.int(lo, Math.min(hi, rem)); // leftover < lo is handed to fan-out hubs (Table 1)
        sizes.push(s); rem -= s;
      }
      return { sizes, left: rem };
    }
    const rs = split(ringBudget, 3, 6), cs = split(Math.max(0, clusterBudget), 5, 8);
    nOut += rs.left + cs.left; // leftovers become fan-out hubs
    const nAnom = nOut + nIn + rs.sizes.reduce((a, b) => a + b, 0) + cs.sizes.reduce((a, b) => a + b, 0);
    const B = Math.max(10, N - nAnom);

    // benign: small components + backbone
    const smallTarget = Math.round(DEFAULTS.smallFrac * B);
    const smallSizes = []; let used = 0;
    while (used < smallTarget) {
      const r = rng(); const s = r < 0.35 ? 2 : r < 0.65 ? 3 : r < 0.85 ? 4 : 5;
      if (used + s > smallTarget + 2) break; smallSizes.push(s); used += s;
    }
    const backboneN = Math.max(20, B - used);
    const backbone = scaleFree(g, backboneN, rng, DEFAULTS);
    const smalls = [];
    for (const s of smallSizes) {
      const members = []; for (let i = 0; i < s; i++) members.push(g.addNode(BENIGN));
      for (let i = 1; i < s; i++) { const j = rng.int(0, i - 1); rng() < 0.5 ? g.addEdge(members[i], members[j]) : g.addEdge(members[j], members[i]); }
      for (let i = 0; i < s; i++) for (let j = i + 1; j < s; j++) if (rng() < 0.12) { rng() < 0.5 ? g.addEdge(members[i], members[j]) : g.addEdge(members[j], members[i]); }
      smalls.push(members);
    }
    const benignAll = g.n; // all benign so far are ids [0, benignAll)

    // planted anomalies
    const hubs = [], rings = [], clusters = [];
    function hub(type) {
      const h = g.addNode(type); const d = rng.int(25, 70);
      const targets = new Set(); while (targets.size < Math.min(d, backbone.length)) targets.add(rng.pick(backbone));
      for (const t of targets) type === FANOUT ? g.addEdge(h, t) : g.addEdge(t, h);
      const extra = rng.int(0, 2);
      for (let i = 0; i < extra; i++) { const t = rng.pick(backbone); type === FANOUT ? g.addEdge(t, h) : g.addEdge(h, t); }
      hubs.push(h);
    }
    for (let i = 0; i < nOut; i++) hub(FANOUT);
    for (let i = 0; i < nIn; i++) hub(FANIN);
    for (const s of rs.sizes) {
      const m = []; for (let i = 0; i < s; i++) m.push(g.addNode(RING));
      for (let i = 0; i < s; i++) g.addEdge(m[i], m[(i + 1) % s]);
      for (let i = 0; i < s; i++) { if (rng() < 0.4) g.addEdge(m[(i + 1) % s], m[i]); }
      for (let i = 0; i < s; i++) for (let j = i + 2; j < s; j++) { if (i === 0 && j === s - 1) continue; if (rng() < 0.3) g.addEdge(m[i], m[j]); }
      for (const x of m) if (rng() < 0.7) { const k = rng.int(1, 2); for (let q = 0; q < k; q++) { const t = rng.pick(backbone); rng() < 0.5 ? g.addEdge(x, t) : g.addEdge(t, x); } }
      rings.push(m);
    }
    for (const s of cs.sizes) {
      const m = []; for (let i = 0; i < s; i++) m.push(g.addNode(CLUSTER));
      for (let i = 1; i < s; i++) g.addEdge(m[i - 1], m[i]);
      for (let i = 0; i < s; i++) for (let j = i + 1; j < s; j++) if (!g.hasUnd(m[i], m[j]) && rng() < 0.8) {
        rng() < 0.5 ? g.addEdge(m[i], m[j]) : g.addEdge(m[j], m[i]);
      }
      for (let i = 0; i < s; i++) for (const j of Array.from(g.out[m[i]])) if (rng() < 0.4) g.addEdge(j, m[i]);
      clusters.push(m);
    }
    // optional what-if (beyond the paper): legitimate loops among ordinary accounts, e.g. exchange deposit/withdrawal flows
    const loopNodes = [];
    for (let i = 0; i < (opts.loops || 0); i++) {
      const size = rng.int(3, 4); const pool = backbone.filter((v) => g.deg(v) <= 8); if (pool.length < size) break;
      const m = []; while (m.length < size) { const v = rng.pick(pool); if (m.indexOf(v) < 0) m.push(v); }
      for (let j = 0; j < size; j++) g.addEdge(m[j], m[(j + 1) % size]);
      loopNodes.push(m);
    }
    // pad with benign if rounding left N short
    while (g.n < N) { const v = g.addNode(BENIGN); g.addEdge(v, rng.pick(backbone)); }
    g.meta = { N: g.n, f, seed, A: nAnom, loopNodes, nOut, nIn, rings, clusters, hubs, smalls, backbone };
    return g;
  }

  /* ---------- statistics (Table 2) ---------- */
  function percentile(arr, q) { // numpy-style linear interpolation
    const a = arr.slice().sort((x, y) => x - y); const idx = (q / 100) * (a.length - 1);
    const lo = Math.floor(idx), hi = Math.ceil(idx); return a[lo] + (a[hi] - a[lo]) * (idx - lo);
  }
  function degreeStats(g) {
    let sum = 0; const d = new Array(g.n);
    for (let v = 0; v < g.n; v++) { d[v] = g.deg(v); sum += d[v]; }
    const mu = sum / g.n; let ss = 0; for (const x of d) ss += (x - mu) * (x - mu);
    const sigma = Math.sqrt(ss / g.n); // population sd (numpy default)
    const sorted = d.slice().sort((a, b) => a - b);
    return { deg: d, mu, sigma, max: sorted[sorted.length - 1], median: sorted[Math.floor((g.n - 1) / 2)] };
  }
  function weakComponents(g) {
    const comp = new Array(g.n).fill(-1); const comps = [];
    for (let s = 0; s < g.n; s++) {
      if (comp[s] >= 0) continue; const id = comps.length; const stack = [s]; comp[s] = id; const members = [];
      while (stack.length) { const v = stack.pop(); members.push(v); for (const w of g.out[v]) if (comp[w] < 0) { comp[w] = id; stack.push(w); } for (const w of g.inn[v]) if (comp[w] < 0) { comp[w] = id; stack.push(w); } }
      comps.push(members);
    }
    return { comp, comps };
  }

  /* ---------- detectors ---------- */
  const DEF = { tau: 3, dmin: 10, betaStar: 0.9, kmin: 3, cStar: 0.5, L: 5, q: 95, smin: 5, smax: 15 };

  function d1(g, P) {
    P = Object.assign({}, DEF, P); const st = degreeStats(g); const flag = new Set(), score = new Array(g.n);
    for (let v = 0; v < g.n; v++) { const z = (st.deg[v] - st.mu) / (st.sigma || 1); score[v] = z; if (z > P.tau) flag.add(v); }
    return { flag, score, cutoff: st.mu + P.tau * st.sigma, st };
  }
  function imbalance(g, v) { const o = g.out[v].size, i = g.inn[v].size; return o + i === 0 ? 0 : Math.abs(o - i) / (o + i); }
  function d2(g, P) {
    P = Object.assign({}, DEF, P); const flag = new Set(), score = new Array(g.n);
    for (let v = 0; v < g.n; v++) { const b = imbalance(g, v); score[v] = b; if (g.deg(v) >= P.dmin && b >= P.betaStar) flag.add(v); }
    return { flag, score };
  }
  function clustering(g, v) {
    const nb = Array.from(g.und(v)); const k = nb.length; if (k < 2) return { k, C: 0, t: 0 };
    let t = 0; for (let i = 0; i < k; i++) for (let j = i + 1; j < k; j++) if (g.hasUnd(nb[i], nb[j])) t++;
    return { k, C: (2 * t) / (k * (k - 1)), t };
  }
  function d3(g, P) {
    P = Object.assign({}, DEF, P); const flag = new Set(), score = new Array(g.n);
    for (let v = 0; v < g.n; v++) { const c = clustering(g, v); score[v] = c.C; if (c.k >= P.kmin && c.C >= P.cStar) flag.add(v); }
    return { flag, score };
  }
  function hubSubgraph(g, q) {
    const degs = []; for (let v = 0; v < g.n; v++) degs.push(g.deg(v));
    const Q = percentile(degs, q); const inH = new Uint8Array(g.n); for (let v = 0; v < g.n; v++) if (g.deg(v) <= Q) inH[v] = 1;
    return { Q, inH };
  }
  /* reverse BFS: dist[u] = shortest u -> v within H, to depth L-1 */
  function reverseBfs(g, inH, v, maxD) {
    const dist = new Map(); dist.set(v, 0); let frontier = [v];
    for (let d = 1; d <= maxD; d++) {
      const nxt = []; for (const x of frontier) for (const y of g.inn[x]) if (inH[y] && !dist.has(y)) { dist.set(y, d); nxt.push(y); }
      frontier = nxt; if (!frontier.length) break;
    }
    return dist;
  }
  function findCycle(g, inH, v, L) {
    const dist = reverseBfs(g, inH, v, L - 1); const path = [v];
    function dfs(x, depth) {
      for (const y of g.out[x]) {
        if (!inH[y]) continue;
        if (y === v && depth + 1 >= 3) return true;
        if (path.indexOf(y) >= 0 || !dist.has(y)) continue;
        if (depth + 1 + dist.get(y) > L) continue;
        path.push(y); if (dfs(y, depth + 1)) return true; path.pop();
      }
      return false;
    }
    return dfs(v, 0) ? path.slice() : null;
  }
  function d4(g, P) {
    P = Object.assign({}, DEF, P); const { Q, inH } = hubSubgraph(g, P.q); const flag = new Set(); const cycles = new Map();
    for (let v = 0; v < g.n; v++) { if (!inH[v]) continue; const c = findCycle(g, inH, v, P.L); if (c) { flag.add(v); cycles.set(v, c); } }
    return { flag, Q, inH, cycles };
  }
  /* animated trace of Algorithm 1 for one start vertex -> list of events */
  function traceCycleSearch(g, inH, v, L, maxEvents) {
    maxEvents = maxEvents || 400; const ev = []; const dist = reverseBfs(g, inH, v, L - 1); const path = [v];
    ev.push({ t: 'bfs', dist: Array.from(dist.entries()) });
    let found = false;
    function dfs(x, depth) {
      for (const y of g.out[x]) {
        if (ev.length > maxEvents) return false;
        if (!inH[y]) { ev.push({ t: 'skiphub', from: x, to: y }); continue; }
        if (y === v && depth + 1 >= 3) { ev.push({ t: 'found', path: path.slice() }); found = true; return true; }
        if (path.indexOf(y) >= 0) { ev.push({ t: 'skipseen', from: x, to: y }); continue; }
        if (!dist.has(y) || depth + 1 + dist.get(y) > L) { ev.push({ t: 'prune', from: x, to: y }); continue; }
        path.push(y); ev.push({ t: 'step', from: x, to: y, path: path.slice() });
        if (dfs(y, depth + 1)) return true; path.pop(); ev.push({ t: 'back', at: x, from: y });
      }
      return false;
    }
    dfs(v, 0); if (!found) ev.push({ t: 'none' }); return ev;
  }
  function d5(g, P) {
    P = Object.assign({}, DEF, P); const { comps } = weakComponents(g); const flag = new Set();
    for (const c of comps) if (c.length >= P.smin && c.length <= P.smax) for (const v of c) flag.add(v);
    return { flag, comps };
  }

  /* ---------- combine + metrics ---------- */
  function runAll(g, P) {
    const r1 = d1(g, P), r2 = d2(g, P), r3 = d3(g, P), r4 = d4(g, P), r5 = d5(g, P);
    const votes = new Uint8Array(g.n);
    for (const r of [r1, r2, r3, r4, r5]) for (const v of r.flag) votes[v]++;
    const union = new Set(), vote2 = new Set();
    for (let v = 0; v < g.n; v++) { if (votes[v] >= 1) union.add(v); if (votes[v] >= 2) vote2.add(v); }
    return { D1: r1, D2: r2, D3: r3, D4: r4, D5: r5, votes, union, vote2 };
  }
  function truthSet(g) { const s = new Set(); for (let v = 0; v < g.n; v++) if (g.label[v] !== BENIGN) s.add(v); return s; }
  function prf(flag, truth) {
    let tp = 0; for (const v of flag) if (truth.has(v)) tp++;
    const fp = flag.size - tp, fn = truth.size - tp;
    const p = flag.size ? tp / flag.size : 0, r = truth.size ? tp / truth.size : 0;
    return { tp, fp, fn, p, r, f1: p + r ? (2 * p * r) / (p + r) : 0, flagged: flag.size };
  }
  function recallByType(g, flag) {
    const tot = [0, 0, 0, 0, 0], hit = [0, 0, 0, 0, 0];
    for (let v = 0; v < g.n; v++) { const l = g.label[v]; if (l === BENIGN) continue; tot[l]++; if (flag.has(v)) hit[l]++; }
    return tot.map((t, i) => (t ? hit[i] / t : null));
  }
  function auroc(score, truth) { // rank-based (Mann-Whitney), ties averaged
    const n = score.length; const idx = Array.from({ length: n }, (_, i) => i).sort((a, b) => score[a] - score[b]);
    const rank = new Array(n); let i = 0;
    while (i < n) { let j = i; while (j + 1 < n && score[idx[j + 1]] === score[idx[i]]) j++; const r = (i + j) / 2 + 1; for (let k = i; k <= j; k++) rank[idx[k]] = r; i = j + 1; }
    let sumPos = 0, nPos = 0; for (const v of truth) { sumPos += rank[v]; nPos++; }
    const nNeg = n - nPos; if (!nPos || !nNeg) return NaN; return (sumPos - (nPos * (nPos + 1)) / 2) / (nPos * nNeg);
  }


  /* ---------- extra tools used by the playground / explanations ---------- */
  function scc(g) { // iterative Tarjan -> array of components (arrays of vertex ids)
    const n = g.n, index = new Array(n).fill(-1), low = new Array(n).fill(0), on = new Array(n).fill(false), st = [], comps = []; let idx = 0;
    for (let s = 0; s < n; s++) {
      if (index[s] >= 0) continue; const work = [[s, Array.from(g.out[s]), 0]]; index[s] = low[s] = idx++; st.push(s); on[s] = true;
      while (work.length) {
        const fr = work[work.length - 1], v = fr[0];
        if (fr[2] < fr[1].length) { const w = fr[1][fr[2]++];
          if (index[w] < 0) { index[w] = low[w] = idx++; st.push(w); on[w] = true; work.push([w, Array.from(g.out[w]), 0]); }
          else if (on[w]) low[v] = Math.min(low[v], index[w]);
        } else {
          if (low[v] === index[v]) { const c = []; let w; do { w = st.pop(); on[w] = false; c.push(w); } while (w !== v); comps.push(c); }
          work.pop(); if (work.length) { const u = work[work.length - 1][0]; low[u] = Math.min(low[u], low[v]); }
        }
      }
    }
    return comps;
  }
  function simpleCycles(g, minL, maxL, cap) { // brute force, for small graphs only
    const res = []; cap = cap || 200;
    for (let s = 0; s < g.n && res.length < cap; s++) {
      const path = [s]; (function dfs(x) {
        if (res.length >= cap) return;
        for (const y of g.out[x]) {
          if (y === s && path.length >= minL && path.length <= maxL) res.push(path.slice());
          else if (y > s && path.indexOf(y) < 0 && path.length < maxL) { path.push(y); dfs(y); path.pop(); }
        }
      })(s);
    }
    return res;
  }
  function why(g, r, v, P) { // plain-language reasons why account v was flagged, per detector
    P = Object.assign({}, DEF, P); const out = [];
    if (r.D1 && r.D1.flag.has(v)) out.push(['D1', `has ${g.deg(v)} links, which is ${r.D1.score[v].toFixed(1)}σ above the average (cut-off ${r.D1.cutoff.toFixed(0)})`]);
    if (r.D2 && r.D2.flag.has(v)) out.push(['D2', `sends to ${g.out[v].size} accounts and receives from ${g.inn[v].size} (imbalance β = ${r.D2.score[v].toFixed(2)} ≥ ${P.betaStar})`]);
    if (r.D3 && r.D3.flag.has(v)) { const c = clustering(g, v); out.push(['D3', `${(c.C * 100).toFixed(0)}% of its ${c.k} neighbours are linked to each other (C = ${c.C.toFixed(2)} ≥ ${P.cStar})`]); }
    if (r.D4 && r.D4.flag.has(v)) { const c = r.D4.cycles.get(v); out.push(['D4', `lies on a directed ${c.length}-cycle: ${c.map((x) => '#' + x).join(' → ')} → #${c[0]}`]); }
    if (r.D5 && r.D5.flag.has(v)) { const comp = r.D5.comps.find((c) => c.indexOf(v) >= 0); out.push(['D5', `sits in a cut-off group of only ${comp ? comp.length : '?'} accounts`]); }
    return out;
  }

  root.GE = {
    scc, simpleCycles, why,
    makeRng, generate, Graph, degreeStats, weakComponents, percentile, clustering, imbalance,
    d1, d2, d3, d4, d5, runAll, truthSet, prf, recallByType, auroc, traceCycleSearch, hubSubgraph, findCycle,
    BENIGN, FANOUT, FANIN, RING, CLUSTER, TYPE_NAMES, DEF
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.GE;
})(typeof window !== 'undefined' ? window : globalThis);
