/* =====================================================================
   graphview.js -- deterministic force layout + zoom/pan canvas renderer
   ===================================================================== */
(function (root) {
  'use strict';
  const GE = root.GE;

  /* ---------- layout ---------- */
  function forceLayout(nodes, edges, rng, iters, R) {
    // nodes: array of ids ; returns Map id -> [x,y] within radius ~R
    const n = nodes.length, idx = new Map(); nodes.forEach((v, i) => idx.set(v, i));
    const x = new Float32Array(n), y = new Float32Array(n);
    const k = R * 0.9 / Math.sqrt(Math.max(n, 1)) * 1.6; // ideal edge length
    for (let i = 0; i < n; i++) { const a = rng() * Math.PI * 2, r = Math.sqrt(rng()) * R; x[i] = Math.cos(a) * r; y[i] = Math.sin(a) * r; }
    const E = []; for (const [u, v] of edges) { if (idx.has(u) && idx.has(v)) E.push([idx.get(u), idx.get(v)]); }
    const dx = new Float32Array(n), dy = new Float32Array(n);
    let temp = R * 0.12;
    for (let it = 0; it < iters; it++) {
      dx.fill(0); dy.fill(0);
      // repulsion: exact all-pairs for moderate n, otherwise a wide grid cutoff
      if (n <= 1400) {
        for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
          let ddx = x[i] - x[j], ddy = y[i] - y[j]; let d2 = ddx * ddx + ddy * ddy; if (d2 < 0.01) { ddx = rng() - 0.5; ddy = rng() - 0.5; d2 = 0.5; }
          const rep = (k * k) / d2; dx[i] += ddx * rep; dy[i] += ddy * rep; dx[j] -= ddx * rep; dy[j] -= ddy * rep;
        }
      } else {
        const cell = k * 4, grid = new Map();
        for (let i = 0; i < n; i++) { const key = Math.floor(x[i] / cell) * 100003 + Math.floor(y[i] / cell); let b = grid.get(key); if (!b) grid.set(key, b = []); b.push(i); }
        for (let i = 0; i < n; i++) { const cx = Math.floor(x[i] / cell), cy = Math.floor(y[i] / cell);
          for (let gx = cx - 1; gx <= cx + 1; gx++) for (let gy = cy - 1; gy <= cy + 1; gy++) { const b = grid.get(gx * 100003 + gy); if (!b) continue;
            for (const j of b) { if (j <= i) continue; let ddx = x[i] - x[j], ddy = y[i] - y[j]; let d2 = ddx * ddx + ddy * ddy; if (d2 < 0.01) { ddx = rng() - 0.5; ddy = rng() - 0.5; d2 = 0.5; }
              const rep = (k * k) / d2; dx[i] += ddx * rep; dy[i] += ddy * rep; dx[j] -= ddx * rep; dy[j] -= ddy * rep; } } }
      }
      for (const [a, b] of E) { const ddx = x[a] - x[b], ddy = y[a] - y[b]; const d = Math.sqrt(ddx * ddx + ddy * ddy) || 0.01; const att = (d * d) / k * 0.35;
        dx[a] -= (ddx / d) * att; dy[a] -= (ddy / d) * att; dx[b] += (ddx / d) * att; dy[b] += (ddy / d) * att; }
      for (let i = 0; i < n; i++) { dx[i] -= x[i] * 0.06 * k * 0.1; dy[i] -= y[i] * 0.06 * k * 0.1; // gravity
        const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 0.01; const s = Math.min(d, temp) / d; x[i] += dx[i] * s; y[i] += dy[i] * s; }
      temp *= 0.965;
    }
    const out = new Map(); nodes.forEach((v, i) => out.set(v, [x[i], y[i]]));
    return out;
  }

  function layoutGraph(g, seed) {
    const rng = GE.makeRng((seed || 0) + 7);
    const { comps } = GE.weakComponents(g);
    comps.sort((a, b) => b.length - a.length);
    const pos = new Array(g.n);
    const giant = comps[0]; const edges = g.edges();
    const R = Math.max(80, Math.sqrt(giant.length) * 14);
    const iters = giant.length > 1500 ? 50 : giant.length > 600 ? 70 : 110;
    const gp = forceLayout(giant, edges, rng, iters, R);
    gp.forEach((p, v) => { pos[v] = p; });
    // other components: packed on concentric rings just outside the giant
    const others = comps.slice(1); let ringR = R * 1.12 + 40, ang = 0, ringH = 0;
    for (const c of others) {
      const rc = c.length === 1 ? 5 : Math.max(10, Math.sqrt(c.length) * 9);
      const loc = c.length === 1 ? new Map([[c[0], [0, 0]]]) : forceLayout(c, edges, rng, 60, rc);
      const step = (rc * 2 + 12) / ringR;
      if (ang + step > Math.PI * 2) { ang = 0; ringR += ringH * 2 + 14; ringH = 0; }
      const a2 = ang + step / 2, cx = Math.cos(a2) * ringR, cy = Math.sin(a2) * ringR;
      loc.forEach((p, v) => { pos[v] = [p[0] + cx, p[1] + cy]; });
      ang += step; ringH = Math.max(ringH, rc);
    }
    return pos;
  }

  /* ---------- renderer ---------- */
  const COLORS = {
    honest: '#9aa3b8', fanout: '#ff8a3d', fanin: '#9b7bff', ring: '#14c9a0', cluster: '#ff4f9a',
    tp: '#22c55e', fp: '#f59e0b', fn: '#ef4444', flag: '#facc15', dim: '#3a4156', edge: 'rgba(160,170,200,0.22)', edgeHi: 'rgba(250,204,21,0.9)'
  };
  const TYPE_COLOR = [COLORS.honest, COLORS.fanout, COLORS.fanin, COLORS.ring, COLORS.cluster];

  function GraphView(canvas, opts) {
    this.c = canvas; this.ctx = canvas.getContext('2d'); this.opts = opts || {};
    this.g = null; this.pos = null; this.mode = 'truth'; this.flag = null; this.truth = null; this.hiNodes = new Set(); this.hiEdges = []; this.pathFocus = null;
    this.view = { s: 1, ox: 0, oy: 0 }; this.hover = -1; this.sel = -1; this.dimBenign = false; this.sizeByDeg = true; this.nodeColor = null; this.showEdges = true; this.badge = null; this.dimOthers = true; this.rs = 1;
    (root.__views = root.__views || []).push(this);
    this._bind(); this._resize();
    const ro = new ResizeObserver(() => { this._resize(); this.draw(); }); ro.observe(canvas.parentElement || canvas);
  }
  GraphView.prototype._resize = function () {
    const dpr = Math.min(2, window.devicePixelRatio || 1); const r = (this.c.parentElement || this.c).getBoundingClientRect();
    const w = Math.max(200, r.width), h = Math.max(200, this.opts.height || r.height);
    this.c.width = w * dpr; this.c.height = h * dpr; this.c.style.width = w + 'px'; this.c.style.height = h + 'px'; this.dpr = dpr; this.W = w; this.H = h;
  };
  GraphView.prototype.setGraph = function (g, pos) { this.g = g; this.pos = pos; this.truth = GE.truthSet(g); this.hover = -1; this.sel = -1; this.fit(); this.draw(); };
  GraphView.prototype.fit = function () {
    if (!this.pos) return; let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const p of this.pos) { if (!p) continue; if (p[0] < x0) x0 = p[0]; if (p[0] > x1) x1 = p[0]; if (p[1] < y0) y0 = p[1]; if (p[1] > y1) y1 = p[1]; }
    const pad = 24; const s = Math.min((this.W - pad * 2) / (x1 - x0 || 1), (this.H - pad * 2) / (y1 - y0 || 1));
    this.view.s = s; this.view.ox = this.W / 2 - ((x0 + x1) / 2) * s; this.view.oy = this.H / 2 - ((y0 + y1) / 2) * s; this.base = Object.assign({}, this.view);
  };
  GraphView.prototype.toScreen = function (p) { return [p[0] * this.view.s + this.view.ox, p[1] * this.view.s + this.view.oy]; };
  GraphView.prototype.radius = function (v) { const d = this.g.deg(v); const base = this.sizeByDeg ? (2.2 + Math.sqrt(d) * 0.9) : 4; return this.rs * base * Math.min(2.2, Math.max(0.7, Math.pow(this.view.s / (this.base ? this.base.s : 1), 0.35))); };
  GraphView.prototype.colorOf = function (v) {
    if (this.nodeColor) { const c = this.nodeColor(v); if (c) return c; }
    const lab = this.g.label[v];
    if (this.mode === 'truth') return TYPE_COLOR[lab];
    if (this.mode === 'outcome') {
      const fl = this.flag && this.flag.has(v), t = lab !== 0;
      if (fl && t) return COLORS.tp; if (fl && !t) return COLORS.fp; if (!fl && t) return COLORS.fn; return COLORS.honest;
    }
    if (this.mode === 'flag') return this.flag && this.flag.has(v) ? COLORS.flag : COLORS.honest;
    return COLORS.honest;
  };
  GraphView.prototype.draw = function () {
    const ctx = this.ctx; if (!ctx) return; ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); ctx.clearRect(0, 0, this.W, this.H);
    if (!this.g) return; const g = this.g, pos = this.pos, V = this.view;
    const light = document.documentElement.dataset.theme === 'light'; const dimmed = this.dimBenign || (this.dimOthers && this.hiNodes.size > 0);
    if (this.showEdges) {
      ctx.lineWidth = 1; ctx.strokeStyle = light ? (dimmed ? 'rgba(60,70,100,0.10)' : 'rgba(60,70,100,0.24)') : (dimmed ? 'rgba(160,170,200,0.10)' : COLORS.edge); ctx.beginPath();
      for (let u = 0; u < g.n; u++) { const pu = pos[u]; if (!pu) continue; const a = this.toScreen(pu);
        for (const v of g.out[u]) { const pv = pos[v]; if (!pv) continue; const b = this.toScreen(pv); if ((a[0] < -50 && b[0] < -50) || (a[0] > this.W + 50 && b[0] > this.W + 50) || (a[1] < -50 && b[1] < -50) || (a[1] > this.H + 50 && b[1] > this.H + 50)) continue; ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); } }
      ctx.stroke();
    }
    // highlighted edges (directed, with arrow heads)
    if (this.hiEdges.length) {
      for (const e of this.hiEdges) { const a = this.toScreen(pos[e.u]), b = this.toScreen(pos[e.v]); ctx.strokeStyle = e.color || COLORS.edgeHi; ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = e.w || 2.4;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        const ang = Math.atan2(b[1] - a[1], b[0] - a[0]), rr = this.radius(e.v) + 2, tx = b[0] - Math.cos(ang) * rr, ty = b[1] - Math.sin(ang) * rr;
        ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(tx - Math.cos(ang - 0.45) * 9, ty - Math.sin(ang - 0.45) * 9); ctx.lineTo(tx - Math.cos(ang + 0.45) * 9, ty - Math.sin(ang + 0.45) * 9); ctx.closePath(); ctx.fill(); }
    }
    // nodes: honest first, then special on top
    const order = []; for (let v = 0; v < g.n; v++) order.push(v);
    order.sort((a, b) => (g.label[a] !== 0 ? 1 : 0) - (g.label[b] !== 0 ? 1 : 0) || g.deg(a) - g.deg(b));
    for (const v of order) {
      const p = pos[v]; if (!p) continue; const s = this.toScreen(p); if (s[0] < -20 || s[0] > this.W + 20 || s[1] < -20 || s[1] > this.H + 20) continue;
      const r = this.radius(v); let col = this.colorOf(v); let alpha = 1;
      if (this.dimOthers && this.hiNodes.size && !this.hiNodes.has(v)) alpha = 0.18; else if (this.dimBenign && this.g.label[v] === 0 && !(this.flag && this.flag.has(v))) alpha = 0.35;
      ctx.globalAlpha = alpha; ctx.beginPath(); ctx.arc(s[0], s[1], r, 0, 6.2832); ctx.fillStyle = col; ctx.fill();
      ctx.lineWidth = 1; ctx.strokeStyle = light ? 'rgba(255,255,255,0.9)' : 'rgba(10,14,26,0.85)'; ctx.stroke();
      if (this.hiNodes.has(v)) { ctx.lineWidth = 2.2; ctx.strokeStyle = light ? '#111' : '#fff'; ctx.stroke(); }
      ctx.globalAlpha = 1;
    }
    if (this.sel >= 0) this._ring(this.sel, light ? '#111' : '#fff', 3); if (this.hover >= 0) this._ring(this.hover, '#facc15', 2);
    if (this.opts.onDraw) this.opts.onDraw(ctx, this);
  };
  GraphView.prototype._ring = function (v, col, w) { const p = this.pos[v]; if (!p) return; const s = this.toScreen(p); this.ctx.beginPath(); this.ctx.arc(s[0], s[1], this.radius(v) + 4, 0, 6.2832); this.ctx.lineWidth = w; this.ctx.strokeStyle = col; this.ctx.stroke(); };
  GraphView.prototype.pick = function (mx, my) {
    if (!this.g) return -1; let best = -1, bd = 1e9;
    for (let v = 0; v < this.g.n; v++) { const p = this.pos[v]; if (!p) continue; const s = this.toScreen(p); const d = (s[0] - mx) ** 2 + (s[1] - my) ** 2; const r = this.radius(v) + 5; if (d < r * r && d < bd) { bd = d; best = v; } }
    return best;
  };
  GraphView.prototype._bind = function () {
    const c = this.c; let drag = null, moved = false;
    const rel = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    c.addEventListener('pointerdown', (e) => { drag = { p: rel(e), ox: this.view.ox, oy: this.view.oy }; moved = false; c.setPointerCapture(e.pointerId); });
    c.addEventListener('pointermove', (e) => { const p = rel(e);
      if (drag) { const dx = p[0] - drag.p[0], dy = p[1] - drag.p[1]; if (Math.abs(dx) + Math.abs(dy) > 3) moved = true; if (moved) { this.view.ox = drag.ox + dx; this.view.oy = drag.oy + dy; this.draw(); } }
      else { const h = this.pick(p[0], p[1]); if (h !== this.hover) { this.hover = h; this.draw(); if (this.opts.onHover) this.opts.onHover(h, e, p); } } });
    c.addEventListener('pointerup', (e) => { if (drag && !moved) { const p = rel(e); const h = this.pick(p[0], p[1]); this.sel = h; this.draw(); if (this.opts.onSelect) this.opts.onSelect(h); } drag = null; });
    c.addEventListener('pointerleave', () => { if (this.hover >= 0) { this.hover = -1; this.draw(); if (this.opts.onHover) this.opts.onHover(-1); } });
    c.addEventListener('wheel', (e) => { e.preventDefault(); const p = rel(e); const f = Math.exp(-e.deltaY * 0.0015); const V = this.view; const ns = Math.max(0.2, Math.min(40, V.s * f)); const k = ns / V.s; V.ox = p[0] - (p[0] - V.ox) * k; V.oy = p[1] - (p[1] - V.oy) * k; V.s = ns; this.draw(); }, { passive: false });
    c.addEventListener('dblclick', () => { this.fit(); this.draw(); });
  };
  GraphView.prototype.zoomTo = function (nodes, pad) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; for (const v of nodes) { const p = this.pos[v]; if (!p) continue; x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
    pad = pad || 60; const s = Math.min((this.W - pad * 2) / Math.max(x1 - x0, 30), (this.H - pad * 2) / Math.max(y1 - y0, 30), 14);
    this.view.s = s; this.view.ox = this.W / 2 - ((x0 + x1) / 2) * s; this.view.oy = this.H / 2 - ((y0 + y1) / 2) * s; this.draw();
  };

  root.GV = { GraphView, layoutGraph, COLORS, TYPE_COLOR };
})(window);
