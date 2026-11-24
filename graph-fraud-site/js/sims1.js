/* =====================================================================
   sims1.js -- shared UI helpers + Sim 1 (toolkit), Sim 2 (world), Sim 3 (heavy tail)
   ===================================================================== */
(function (root) {
  'use strict';
  const GE = root.GE, GV = root.GV, CH = root.CH;
  const $ = (s, r) => (r || document).querySelector(s);
  const f2 = (x) => (x == null || isNaN(x) ? '–' : x.toFixed(2)), f3 = (x) => (x == null || isNaN(x) ? '–' : x.toFixed(3));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const TYPE_NAMES = GE.TYPE_NAMES, TC = () => [css('--honest'), css('--fanout'), css('--fanin'), css('--ring'), css('--cluster')];

  /* ---------- shared helpers ---------- */
  const worldCache = new Map();
  function getWorld(opts, needLayout) {
    const key = [opts.N, opts.f, opts.seed, opts.loops || 0].join('|'); let w = worldCache.get(key);
    if (!w) { w = { g: GE.generate(opts), pos: null }; worldCache.set(key, w); if (worldCache.size > 24) worldCache.delete(worldCache.keys().next().value); }
    if (needLayout && !w.pos) w.pos = GV.layoutGraph(w.g, opts.seed);
    return w;
  }
  const later = (fn) => new Promise((res) => setTimeout(() => res(fn()), 16));
  function mean(a) { return a.reduce((x, y) => x + y, 0) / (a.length || 1); }
  function sd(a) { const m = mean(a); return Math.sqrt(mean(a.map((x) => (x - m) * (x - m)))); }
  function slider(id, label, min, max, step, val, fmt) {
    return `<div class="ctl"><label for="${id}">${label}<b id="${id}V">${fmt ? fmt(val) : val}</b></label><input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"></div>`;
  }
  function legendHTML() { return `<div class="legend"><span><i style="background:var(--honest)"></i>Honest</span><span><i style="background:var(--fanout)"></i>Fan-out hub</span><span><i style="background:var(--fanin)"></i>Fan-in hub</span><span><i style="background:var(--ring)"></i>Ring</span><span><i style="background:var(--cluster)"></i>Cluster</span></div>`; }
  function liveBadge(txt) { return `<div><span class="pill live">● live · your browser</span> <span class="small">${txt || ''}</span></div>`; }
  function tipFor(view, e, html) { CH.showTip(html, e); }
  root.UI = { $, f2, f3, css, getWorld, later, mean, sd, slider, legendHTML, liveBadge, TC };

  /* ======================================================================
     SIM 1 : Toolkit playground
     ====================================================================== */
  function simToolkit(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>Playground: build a network, read its numbers</h3><span class="tag">Sim 1 · degree · cycles · components · clustering</span></header>
      <div class="split">
        <div><div class="btns" style="margin-bottom:8px"><div class="seg" id="tkMode"><button data-m="add" class="on">＋ Account</button><button data-m="link">→ Transfer</button><button data-m="move">✥ Move</button><button data-m="subset">◎ Subgraph</button><button data-m="del">✕ Remove</button></div>
          <button class="btn" data-pre="demo">Preset: all four shapes</button><button class="btn" data-pre="tri">Triangle + tail</button><button class="btn" data-pre="clear">Clear</button></div>
          <div class="canvaswrap"><canvas id="tkCanvas" style="height:430px"></canvas></div>
          <p class="small" id="tkHint" style="margin-top:6px"></p></div>
        <div class="controls"><div class="metrics" style="grid-template-columns:repeat(2,1fr)"><div class="metric"><span>Accounts |V|</span><b id="tkN">0</b></div><div class="metric"><span>Transfers |E|</span><b id="tkM">0</b></div><div class="metric"><span>Σ out = Σ in</span><b id="tkH">–</b></div><div class="metric"><span>Mean degree μ = 2|E|/|V|</span><b id="tkMu">–</b></div></div>
          <div class="card" style="padding:12px"><h4 style="margin-bottom:4px">Selected account</h4><div id="tkSel" class="small">Hover or click an account.</div></div>
          <div class="card" style="padding:12px"><h4 style="margin-bottom:4px">Structure</h4><div id="tkStruct" class="small"></div></div>
          ${slider('tkTau', 'z-score threshold τ', 0.5, 3, 0.5, 2, (v) => (+v).toFixed(1))}<div class="small" id="tkCant"></div>
        </div></div></div>`;
    const cv = $('#tkCanvas', mount), ctx = cv.getContext('2d'); let W = 600, H = 430, dpr = 1;
    let nodes = [], edges = [], mode = 'add', sel = -1, hov = -1, drag = null, subset = new Set(), hiCycle = null, linkFrom = -1, mouse = [0, 0];
    function resize() { const r = cv.parentElement.getBoundingClientRect(); dpr = Math.min(2, devicePixelRatio || 1); W = r.width; H = 430; cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px'; draw(); }
    new ResizeObserver(resize).observe(cv.parentElement);
    const hints = { add: 'Click empty space to add an account.', link: 'Press on one account and drag to another to add a transfer (an arrow).', move: 'Drag accounts to rearrange them.', subset: 'Click accounts to pick a set S — the induced subgraph G[S] is highlighted.', del: 'Click an account to remove it.' };
    const setMode = (m) => { mode = m; linkFrom = -1; mount.querySelectorAll('#tkMode button').forEach((b) => b.classList.toggle('on', b.dataset.m === m)); $('#tkHint', mount).textContent = hints[m]; };
    mount.querySelectorAll('#tkMode button').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.m))); setMode('add');
    function build() { const g = new GE.Graph(); nodes.forEach(() => g.addNode(0)); edges.forEach(([u, v]) => g.addEdge(u, v)); return g; }
    function hasEdge(u, v) { return edges.some((e) => e[0] === u && e[1] === v); }
    function preset(name) {
      nodes = []; edges = []; sel = -1; subset.clear(); hiCycle = null;
      const P = (x, y) => { nodes.push([x * W, y * H]); return nodes.length - 1; };
      if (name === 'tri') { const a = P(.3, .5), b = P(.45, .3), c = P(.45, .7), d = P(.65, .5), e = P(.82, .5); [[a, b], [b, c], [c, a], [a, d], [d, e]].forEach((x) => edges.push(x)); }
      if (name === 'demo') {
        const h = P(.16, .25); for (let i = 0; i < 5; i++) { const l = P(.04 + i * .065, .08 + (i % 2) * .08); edges.push([h, l]); } // fan-out
        const k = P(.84, .22); for (let i = 0; i < 5; i++) { const l = P(.7 + i * .06, .06 + (i % 2) * .09); edges.push([l, k]); } // fan-in
        const r = [P(.12, .62), P(.26, .62), P(.26, .86), P(.12, .86)]; for (let i = 0; i < 4; i++) edges.push([r[i], r[(i + 1) % 4]]); edges.push([r[0], r[2]]); // ring + chord
        const c = [P(.62, .62), P(.76, .58), P(.84, .72), P(.74, .88), P(.6, .8)]; for (let i = 0; i < 5; i++) for (let j = i + 1; j < 5; j++) edges.push(Math.random() < 2 ? [c[i], c[j]] : [c[j], c[i]]);
        const o = P(.46, .5), p = P(.52, .4); edges.push([o, p]); edges.push([r[1], o]);
      }
      update();
    }
    mount.querySelectorAll('[data-pre]').forEach((b) => b.addEventListener('click', () => preset(b.dataset.pre === 'clear' ? 'none' : b.dataset.pre)));
    function pickNode(x, y) { let b = -1, bd = 400; nodes.forEach((n, i) => { const d = (n[0] - x) ** 2 + (n[1] - y) ** 2; if (d < bd) { bd = d; b = i; } }); return b; }
    const rel = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    cv.addEventListener('pointerdown', (e) => { const [x, y] = rel(e), i = pickNode(x, y); cv.setPointerCapture(e.pointerId);
      if (mode === 'add' && i < 0) { nodes.push([x, y]); update(); }
      else if (mode === 'link' && i >= 0) { linkFrom = i; }
      else if (mode === 'move' && i >= 0) { drag = i; }
      else if (mode === 'subset' && i >= 0) { subset.has(i) ? subset.delete(i) : subset.add(i); update(); }
      else if (mode === 'del' && i >= 0) { nodes.splice(i, 1); edges = edges.filter((e2) => e2[0] !== i && e2[1] !== i).map(([u, v]) => [u > i ? u - 1 : u, v > i ? v - 1 : v]); sel = -1; subset.clear(); update(); }
      if (i >= 0 && mode !== 'del') sel = i; draw(); showSel(); });
    cv.addEventListener('pointermove', (e) => { mouse = rel(e); const i = pickNode(mouse[0], mouse[1]); if (drag != null) { nodes[drag] = [mouse[0], mouse[1]]; draw(); } else { if (i !== hov) { hov = i; showSel(); } draw(); } });
    cv.addEventListener('pointerup', (e) => { const [x, y] = rel(e), i = pickNode(x, y);
      if (mode === 'link' && linkFrom >= 0 && i >= 0 && i !== linkFrom && !hasEdge(linkFrom, i)) { edges.push([linkFrom, i]); update(); }
      linkFrom = -1; drag = null; draw(); });
    cv.addEventListener('pointerleave', () => { hov = -1; showSel(); draw(); });
    function arrow(a, b, col, w) {
      const ang = Math.atan2(b[1] - a[1], b[0] - a[0]), r = 11; const sx = a[0] + Math.cos(ang) * r, sy = a[1] + Math.sin(ang) * r, tx = b[0] - Math.cos(ang) * (r + 1), ty = b[1] - Math.sin(ang) * (r + 1);
      ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = w || 1.6; ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(tx, ty); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(tx - Math.cos(ang - .4) * 9, ty - Math.sin(ang - .4) * 9); ctx.lineTo(tx - Math.cos(ang + .4) * 9, ty - Math.sin(ang + .4) * 9); ctx.closePath(); ctx.fill();
    }
    let sccId = []; let wccId = [];
    const PAL = ['#14c9a0', '#9b7bff', '#ff8a3d', '#ff4f9a', '#4da3ff', '#facc15', '#22c55e', '#f472b6'];
    function draw() {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H); const ink = css('--ink2'), dark = css('--ink');
      const nb = sel >= 0 ? new Set([...edges.filter((e) => e[0] === sel || e[1] === sel).flat()]) : new Set();
      // component hulls (strongly connected with >=2 vertices)
      const g = build(); const comps = GE.scc(g).filter((c) => c.length >= 2);
      comps.forEach((c, k) => { ctx.fillStyle = PAL[k % PAL.length] + '26'; ctx.strokeStyle = PAL[k % PAL.length] + '88'; ctx.lineWidth = 1.5; c.forEach((v) => { ctx.beginPath(); ctx.arc(nodes[v][0], nodes[v][1], 22, 0, 6.3); ctx.fill(); }); });
      edges.forEach(([u, v]) => { let col = ink + '99', w = 1.6; if (sel >= 0 && (u === sel || v === sel)) { col = css('--accent'); w = 2.6; } if (hiCycle) { for (let i = 0; i < hiCycle.length; i++) if (hiCycle[i] === u && hiCycle[(i + 1) % hiCycle.length] === v) { col = '#ef4444'; w = 3.4; } }
        if (subset.size && subset.has(u) && subset.has(v)) { col = css('--accent2'); w = 3.2; } arrow(nodes[u], nodes[v], col, w); });
      if (mode === 'link' && linkFrom >= 0) { ctx.setLineDash([5, 4]); ctx.strokeStyle = css('--accent'); ctx.beginPath(); ctx.moveTo(nodes[linkFrom][0], nodes[linkFrom][1]); ctx.lineTo(mouse[0], mouse[1]); ctx.stroke(); ctx.setLineDash([]); }
      nodes.forEach((n, i) => { ctx.beginPath(); ctx.arc(n[0], n[1], 10, 0, 6.3); ctx.fillStyle = subset.has(i) ? css('--accent2') : i === sel ? css('--accent') : nb.has(i) ? css('--accent') + 'aa' : css('--honest'); ctx.fill(); ctx.lineWidth = i === hov ? 3 : 1.4; ctx.strokeStyle = i === hov ? dark : css('--bg'); ctx.stroke(); ctx.fillStyle = '#10141f'; ctx.font = '700 10px ' + css('--mono'); ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(i, n[0], n[1] + .5); });
      if (!nodes.length) { ctx.fillStyle = ink; ctx.font = '600 15px ' + css('--sans'); ctx.textAlign = 'center'; ctx.fillText('Empty canvas — click to add accounts, or load a preset', W / 2, H / 2); }
    }
    function showSel() {
      const v = hov >= 0 ? hov : sel; const el = $('#tkSel', mount); if (v < 0 || v >= nodes.length) { el.innerHTML = 'Hover or click an account.'; return; }
      const g = build(), o = g.out[v].size, i = g.inn[v].size, c = GE.clustering(g, v), st = GE.degreeStats(g), z = st.sigma ? (g.deg(v) - st.mu) / st.sigma : 0;
      el.innerHTML = `<b>Account #${v}</b><br>sends to <b>${o}</b> · receives from <b>${i}</b> · degree <b>${o + i}</b><br><span class="techonly">z-score = (${o + i} − ${st.mu.toFixed(2)}) / ${st.sigma.toFixed(2)} = <b>${z.toFixed(2)}</b><br></span>${c.k >= 2 ? `neighbours: ${c.k}, links among them: ${c.t} → clustering C = <b>${c.C.toFixed(2)}</b><span class="techonly"> = 2·${c.t} / (${c.k}·${c.k - 1})</span>` : 'needs ≥ 2 neighbours for a clustering value'}`;
    }
    function update() {
      const g = build(), n = g.n, m = g.m, st = n ? GE.degreeStats(g) : null;
      $('#tkN', mount).textContent = n; $('#tkM', mount).textContent = m;
      let so = 0, si = 0; for (let v = 0; v < n; v++) { so += g.out[v].size; si += g.inn[v].size; }
      $('#tkH', mount).textContent = n ? `${so} = ${si}` : '–'; $('#tkMu', mount).textContent = n ? st.mu.toFixed(2) : '–';
      const wc = n ? GE.weakComponents(g).comps : [], sc = n ? GE.scc(g) : [], cyc = n && n <= 30 ? GE.simpleCycles(g, 3, 5, 40) : [];
      const sub = subset.size ? (() => { const S = [...subset]; let ie = 0; edges.forEach(([u, v]) => { if (subset.has(u) && subset.has(v)) ie++; }); return `<br><b>G[S]</b>: ${S.length} accounts, ${ie} edges inside S`; })() : '';
      $('#tkStruct', mount).innerHTML = `<b>${wc.length}</b> weakly connected component${wc.length === 1 ? '' : 's'}${wc.length ? ` (sizes ${wc.map((c) => c.length).sort((a, b) => b - a).join(', ')})` : ''}<br><b>${sc.filter((c) => c.length >= 2).length}</b> strongly connected group${sc.filter((c) => c.length >= 2).length === 1 ? '' : 's'} with ≥ 2 accounts <span class="techonly">(tinted discs)</span><br><b>${cyc.length}</b> directed cycle${cyc.length === 1 ? '' : 's'} of length 3–5${cyc.length ? ': ' + cyc.slice(0, 6).map((c, i) => `<a href="#" data-cy="${i}">${c.join('→')}→${c[0]}</a>`).join(' · ') : ''}${sub}`;
      $('#tkStruct', mount).querySelectorAll('[data-cy]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); hiCycle = cyc[+a.dataset.cy]; draw(); }));
      hiCycle = null; const tau = +$('#tkTau', mount).value; $('#tkTauV', mount).textContent = tau.toFixed(1);
      if (n) { const cnt = st.deg.filter((d) => (d - st.mu) / (st.sigma || 1) >= tau).length; $('#tkCant', mount).innerHTML = `Cantelli ceiling: at most <b>${(100 / (1 + tau * tau)).toFixed(0)}%</b> of accounts can have z ≥ ${tau.toFixed(1)}. Here: <b>${(100 * cnt / n).toFixed(0)}%</b> (${cnt} of ${n}).`; } else $('#tkCant', mount).textContent = '';
      draw(); showSel();
    }
    $('#tkTau', mount).addEventListener('input', update);
    resize(); preset('demo');
  }

  /* ======================================================================
     SIM 2 : World builder
     ====================================================================== */
  function simWorld(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>World builder: plant fraud in a honest network</h3><span class="tag">Sim 2 · generator (Table 1)</span></header>
      <div class="split"><div><div class="canvaswrap"><canvas id="wCanvas" style="height:520px"></canvas><div id="wBusy" class="small" style="position:absolute;left:12px;top:10px;display:none">laying out graph…</div></div>${legendHTML()}<p class="small">Scroll to zoom, drag to pan, double-click to reset. Node size grows with degree. The cut-off clusters on the right are isolated components.</p></div>
      <div class="controls">${liveBadge('JS port of the paper’s generator')}
        ${slider('wN', 'Accounts N', 300, 2000, 100, 1000, (v) => (+v).toLocaleString())}${slider('wF', 'Fraud share f', 0.01, 0.15, 0.005, 0.05, (v) => (v * 100).toFixed(1) + '%')}${slider('wSeed', 'Random seed', 0, 99, 1, 0)}
        <div class="btns"><button class="btn primary" id="wGen">Generate</button><button class="btn" id="wRnd">🎲 New seed</button><button class="btn" id="wP0">Paper setup (N=1000, 5%, seed 0)</button></div>
        <div class="tablewrap"><table class="t" id="wStats"></table></div>
        <div class="small" id="wHover">Hover an account for details.</div></div></div></div>`;
    const view = new GV.GraphView($('#wCanvas', mount), { height: 520, onHover: (v, e) => { const d = $('#wHover', mount); if (v < 0) { d.textContent = 'Hover an account for details.'; CH.hideTip(); return; } const g = view.g; const o = g.out[v].size, i = g.inn[v].size; d.innerHTML = `<b>Account #${v}</b> — ${TYPE_NAMES[g.label[v]]}: sends to ${o}, receives from ${i}`; } });
    const wN = $('#wN', mount), wF = $('#wF', mount), wS = $('#wSeed', mount);
    [['wN', (v) => (+v).toLocaleString()], ['wF', (v) => (v * 100).toFixed(1) + '%'], ['wSeed', (v) => v]].forEach(([id, fm]) => $('#' + id, mount).addEventListener('input', (e) => { $('#' + id + 'V', mount).textContent = fm(+e.target.value); }));
    let busy = false;
    async function gen() {
      if (busy) return; busy = true; $('#wBusy', mount).style.display = 'block'; await later(() => 0);
      const opts = { N: +wN.value, f: +wF.value, seed: +wS.value }; const w = getWorld(opts, true); view.setGraph(w.g, w.pos); view.mode = 'truth'; view.draw();
      stats(w.g); $('#wBusy', mount).style.display = 'none'; busy = false;
    }
    function stats(g) {
      const st = GE.degreeStats(g), { comps } = GE.weakComponents(g), sc = GE.scc(g); let rec = 0; for (const [u, v] of g.edges()) if (g.hasEdge(v, u)) rec++;
      let cc = 0; for (let v = 0; v < g.n; v++) cc += GE.clustering(g, v).C; const m = g.meta; const giant = Math.max(...comps.map((c) => c.length)), big = Math.max(...sc.map((c) => c.length));
      const cnt = [0, 0, 0, 0, 0]; g.label.forEach((l) => cnt[l]++);
      const rows = [['Accounts |V|', g.n.toLocaleString(), '1,000'], ['Transactions |E|', g.m.toLocaleString(), '2,750'], ['Mean / median degree', `${st.mu.toFixed(2)} / ${st.median}`, '5.50 / 3'], ['Max degree / σ', `${st.max} / ${st.sigma.toFixed(2)}`, '471 / 18.26'],
        ['Planted anomalies', `${m.A} (${(100 * m.A / g.n).toFixed(1)}%)`, '50 (5%)'], ['fan-out / fan-in', `${cnt[1]} / ${cnt[2]}`, '12 / 12'], ['ring / cluster accounts', `${cnt[3]} / ${cnt[4]}`, '15 / 11'],
        ['Density', (g.m / (g.n * (g.n - 1))).toFixed(5), '0.00275'], ['Reciprocity', (rec / Math.max(1, g.m)).toFixed(3), '0.020'], ['Avg clustering', (cc / g.n).toFixed(3), '0.118'], ['Weak components', `${comps.length} (giant ${giant})`, '20 (giant 932)'], ['Strong components', `${sc.length} (largest ${big})`, '933 (largest 44)']];
      $('#wStats', mount).innerHTML = `<tr><th>Property</th><th class="num"><span class="pill live">live</span></th><th class="num"><span class="pill paper">paper T2</span></th></tr>` + rows.map((r) => `<tr><td>${r[0]}</td><td class="num">${r[1]}</td><td class="num" style="color:var(--ink3)">${r[2]}</td></tr>`).join('');
    }
    $('#wGen', mount).addEventListener('click', gen); $('#wRnd', mount).addEventListener('click', () => { wS.value = Math.floor(Math.random() * 100); wS.dispatchEvent(new Event('input')); gen(); });
    $('#wP0', mount).addEventListener('click', () => { wN.value = 1000; wF.value = 0.05; wS.value = 0; [wN, wF, wS].forEach((x) => x.dispatchEvent(new Event('input'))); gen(); });
    [wN, wF, wS].forEach((x) => x.addEventListener('change', gen));
    root.__lazy = root.__lazy || {}; root.__lazy.world = gen;
  }

  /* ======================================================================
     SIM 3 : Heavy tail vs 3 sigma
     ====================================================================== */
  function simHeavy(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>Heavy tail vs the 3σ rule</h3><span class="tag">Sim 3 · Fig. 2 · finding (iv)</span></header>
      <div class="grid3" style="align-items:end;margin-bottom:12px">${liveBadge('fresh graph every time you move a slider')}<span></span><span></span>
        ${slider('hN', 'Accounts N', 300, 4000, 100, 1000, (v) => (+v).toLocaleString())}${slider('hTau', 'Threshold τ (σ above mean)', 1, 5, 0.5, 3, (v) => (+v).toFixed(1))}
        <div class="btns"><div class="seg" id="hRule"><button data-r="sigma" class="on">3σ rule (paper)</button><button data-r="rank">Top 3% by degree (beyond paper)</button></div><button class="btn primary" id="hDemo">▶ Guided demo: 300 → 4000</button></div></div>
      <div class="metrics" style="margin-bottom:12px"><div class="metric"><span>Cut-off μ+τσ</span><b id="hCut">–</b></div><div class="metric"><span>Biggest honest hub</span><b id="hMax">–</b></div><div class="metric"><span>Fraud hubs caught</span><b id="hRec">–</b></div><div class="metric"><span>F1 of D1</span><b id="hF1">–</b></div></div>
      <div class="grid2"><div><div class="small" style="font-weight:700">How many accounts have at least this many links?</div><div id="hCcdf"></div></div><div><div class="small" style="font-weight:700">Money sent vs money received</div><div id="hScat"></div></div></div>
      <div class="small" id="hMsg" style="margin:8px 0"></div>
      <div class="note paper"><span class="pill paper">▣ paper</span> mean cut-off ≈ <b>45 / 56 / 85</b> and D1 F1 = <b>0.545 / 0.298 / 0.000</b> at N = 300 / 1000 / 4000 (Table 6). Once the cut-off passes 70 — the largest planted hub — nothing can be flagged.</div>
      <div class="legend"><span><i style="background:var(--fanout)"></i>Fan-out hub</span><span><i style="background:var(--fanin)"></i>Fan-in hub</span><span><i style="background:var(--ring)"></i>Ring</span><span><i style="background:var(--cluster)"></i>Cluster</span><span><i style="background:var(--honest)"></i>Honest</span><span style="color:var(--bad)">┅ cut-off</span><span style="color:var(--warn)">▒ planted-hub degrees 25–70</span></div></div>`;
    let rule = 'sigma';
    mount.querySelectorAll('#hRule button').forEach((b) => b.addEventListener('click', () => { rule = b.dataset.r; mount.querySelectorAll('#hRule button').forEach((x) => x.classList.toggle('on', x === b)); run(); }));
    const NS = 'http://www.w3.org/2000/svg';
    function plot(host, cfg) {
      host.innerHTML = ''; const W = 520, H = 320, m = { l: 58, r: 12, t: 10, b: 40 }; const svg = CH.el('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart' }, host);
      const X = (v) => m.l + (Math.log10(Math.max(v, 0.9)) / cfg.xl) * (W - m.l - m.r), Y = (v) => H - m.b - (Math.log10(Math.max(v, cfg.ymin || 0.9)) - Math.log10(cfg.ymin || 0.9)) / (cfg.yl - Math.log10(cfg.ymin || 0.9)) * (H - m.t - m.b);
      for (let e = 0; e <= cfg.xl; e++) { const v = Math.pow(10, e); CH.el('line', { x1: X(v), x2: X(v), y1: m.t, y2: H - m.b, class: 'grid' }, svg); CH.el('text', { x: X(v), y: H - m.b + 14, class: 'tick', 'text-anchor': 'middle' }, svg, cfg.xt(v)); }
      cfg.yticks.forEach((v) => { CH.el('line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: 'grid' }, svg); CH.el('text', { x: m.l - 5, y: Y(v) + 3, class: 'tick', 'text-anchor': 'end' }, svg, cfg.yt(v)); });
      CH.el('text', { x: (m.l + W - m.r) / 2, y: H - 4, class: 'axlabel', 'text-anchor': 'middle' }, svg, cfg.xlab); CH.el('text', { x: 12, y: H / 2, class: 'axlabel', 'text-anchor': 'middle', transform: `rotate(-90 12 ${H / 2})` }, svg, cfg.ylab);
      cfg.draw(svg, X, Y, m, W, H); return svg;
    }
    function run() {
      const N = +$('#hN', mount).value, tau = +$('#hTau', mount).value; $('#hNV', mount).textContent = N.toLocaleString(); $('#hTauV', mount).textContent = tau.toFixed(1);
      const g = getWorld({ N, f: 0.05, seed: 3 }, false).g, st = GE.degreeStats(g), truth = GE.truthSet(g); const cut = st.mu + tau * st.sigma;
      let flag = new Set(); if (rule === 'sigma') { for (let v = 0; v < g.n; v++) if (st.deg[v] > cut) flag.add(v); } else { const idx = Array.from({ length: g.n }, (_, i) => i).sort((a, b) => st.deg[b] - st.deg[a]).slice(0, Math.round(0.03 * g.n)); flag = new Set(idx); }
      const hubs = []; for (let v = 0; v < g.n; v++) if (g.label[v] === 1 || g.label[v] === 2) hubs.push(v);
      const caught = hubs.filter((v) => flag.has(v)).length, m = GE.prf(flag, truth);
      let maxH = 0; for (let v = 0; v < g.n; v++) if (g.label[v] === 0) maxH = Math.max(maxH, st.deg[v]);
      $('#hCut', mount).textContent = rule === 'sigma' ? cut.toFixed(0) : 'top 3%'; $('#hMax', mount).textContent = maxH; $('#hRec', mount).textContent = `${caught}/${hubs.length}`; $('#hF1', mount).textContent = f3(m.f1);
      $('#hMsg', mount).innerHTML = rule === 'sigma' ? (cut > 70 ? `<b style="color:var(--bad)">The cut-off (${cut.toFixed(0)}) is above every planted hub (max degree 70) — the rule can flag no fraud hub at all.</b>` : `The cut-off (${cut.toFixed(0)}) sits ${cut > 25 ? 'inside' : 'below'} the planted-hub range 25–70, so only hubs above it are caught.`) : `A rank rule doesn't care how fat the tail is — it keeps working as N grows, but it assumes you know roughly how many hubs to expect.`;
      // CCDF
      const sorted = st.deg.slice().sort((a, b) => a - b), n = g.n; const uniq = [...new Set(sorted)]; const ccdf = new Map(); let i = 0; uniq.forEach((d) => { while (sorted[i] < d) i++; ccdf.set(d, (n - i) / n); });
      const xmax = Math.ceil(Math.log10(Math.max(st.max, 100))); const tc = TC();
      plot($('#hCcdf', mount), { xl: Math.max(2, xmax), yl: 0, ymin: 1 / n, yticks: [1, 0.1, 0.01, 0.001].filter((y) => y >= 1 / n), xt: (v) => v, yt: (v) => (v >= 1 ? '1' : v), xlab: 'degree', ylab: 'fraction with ≥ this degree',
        draw(svg, X, Y, mm, W, H) {
          CH.el('rect', { x: X(25), y: mm.t, width: Math.max(2, X(70) - X(25)), height: H - mm.t - mm.b, fill: css('--warn'), opacity: 0.16 }, svg);
          let d = ''; uniq.forEach((u, k) => { d += (k ? 'L' : 'M') + X(u).toFixed(1) + ' ' + Y(ccdf.get(u)).toFixed(1); }); CH.el('path', { d, fill: 'none', stroke: css('--honest'), 'stroke-width': 2 }, svg);
          if (rule === 'sigma' && cut < Math.pow(10, Math.max(2, xmax))) { CH.el('line', { x1: X(cut), x2: X(cut), y1: mm.t, y2: H - mm.b, stroke: css('--bad'), 'stroke-width': 2, 'stroke-dasharray': '5 4' }, svg); CH.el('text', { x: Math.min(X(cut) + 4, W - 70), y: mm.t + 12, class: 'tick', fill: css('--bad') }, svg, 'μ+' + tau.toFixed(1) + 'σ≈' + cut.toFixed(0)); }
          for (let v = 0; v < g.n; v++) if (g.label[v] !== 0 && st.deg[v] >= 10) { CH.el('circle', { cx: X(st.deg[v]), cy: Y(ccdf.get(st.deg[v])), r: 3.2, fill: tc[g.label[v]], stroke: css('--bg'), 'stroke-width': 0.8 }, svg); }
        } });
      // scatter
      const sm = Math.ceil(Math.log10(Math.max(st.max, 100)));
      plot($('#hScat', mount), { xl: sm, yl: sm, ymin: 1, yticks: [1, 10, 100, 1000].filter((v) => Math.log10(v) <= sm), xt: (v) => v, yt: (v) => v, xlab: 'out-degree (sends to)', ylab: 'in-degree (receives from)',
        draw(svg, X, Y) { const rj = GE.makeRng(5);
          const order = Array.from({ length: g.n }, (_, i) => i).sort((a, b) => (g.label[a] !== 0) - (g.label[b] !== 0));
          for (const v of order) { const jx = 1 + rj() * 0.35, jy = 1 + rj() * 0.35; const o = g.out[v].size + 1 * jx, ii = g.inn[v].size + 1 * jy; if (g.label[v] === 0 && g.deg(v) < 4 && rj() < 0.6) continue;
            CH.el('circle', { cx: X(o), cy: Y(ii), r: g.label[v] ? 3.4 : 2.2, fill: tc[g.label[v]], opacity: g.label[v] ? 1 : 0.55 }, svg); }
        } });
    }
    mount.querySelectorAll('input').forEach((x) => x.addEventListener('input', run));
    $('#hDemo', mount).addEventListener('click', async () => { const sl = $('#hN', mount); for (const n of [300, 600, 1000, 2000, 3000, 4000]) { sl.value = n; run(); await new Promise((r) => setTimeout(r, 1500)); } });
    run();
  }

  root.SIMS1 = { toolkit: simToolkit, world: simWorld, heavy: simHeavy };
})(window);
