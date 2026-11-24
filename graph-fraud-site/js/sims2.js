/* =====================================================================
   sims2.js -- Sim 4 (detector bench), Sim 5 (cycle hunt), Sim 6 (the net)
   ===================================================================== */
(function (root) {
  'use strict';
  const GE = root.GE, GV = root.GV, CH = root.CH, UI = root.UI, PAPER = root.PAPER;
  const { $, f2, f3, css, getWorld, later, slider, legendHTML, liveBadge } = UI;
  const DET = { D1: 'Degree z-score', D2: 'Degree imbalance', D3: 'Clustering', D4: 'Short cycles', D5: 'Small components' };
  const TYPE_NAMES = GE.TYPE_NAMES;

  /* ======================================================================
     SIM 4 : Detector bench
     ====================================================================== */
  function simBench(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>The detective bench</h3><span class="tag">Sim 4 · Experiment 0 · five detectors, live</span></header>
      <div class="split"><div><div class="canvaswrap"><canvas id="bCanvas" style="height:540px"></canvas><div id="bBusy" class="small" style="position:absolute;left:12px;top:10px;display:none">building network…</div></div>
        <div class="legend" id="bLegend"></div></div>
      <div class="controls">${liveBadge('default parameters from Table 3')}
        <div><div class="small" style="font-weight:700;margin-bottom:6px">Switch detectors on</div><div class="btns" id="bDets">${Object.keys(DET).map((d) => `<button class="btn" data-d="${d}">${d} · ${DET[d]}</button>`).join('')}</div></div>
        <div><div class="small" style="font-weight:700;margin-bottom:6px">When several are on, flag an account if…</div><div class="seg" id="bComb"><button data-c="1" class="on">any one fires (Union)</button><button data-c="2">two or more agree (Vote ≥ 2)</button></div></div>
        <div><div class="small" style="font-weight:700;margin-bottom:6px">Colour by</div><div class="seg" id="bMode"><button data-m="outcome" class="on">Outcome</button><button data-m="truth">Hidden truth</button></div></div>
        <div class="metrics"><div class="metric"><span>Precision</span><b id="bP">–</b></div><div class="metric"><span>Recall</span><b id="bR">–</b></div><div class="metric"><span>F1</span><b id="bF">–</b></div><div class="metric"><span>Flagged</span><b id="bFl">–</b></div></div>
        <div class="small" id="bCounts"></div>
        <div class="btns"><button class="btn primary" id="bDemo">▶ Guided tour</button><button class="btn" id="bRnd">🎲 New network</button></div>
        <div class="small" id="bTour" style="min-height:1.4em"></div><div class="card" style="padding:12px"><h4 style="margin-bottom:4px">Why was this account flagged?</h4><div class="small" id="bWhy">Hover any account.</div></div>
        <details class="d"><summary>Network size &amp; fraud share</summary><div class="controls">${slider('bN', 'Accounts N', 300, 1500, 100, 1000, (v) => (+v).toLocaleString())}${slider('bFs', 'Fraud share f', 0.01, 0.15, 0.01, 0.05, (v) => (v * 100).toFixed(0) + '%')}${slider('bS', 'Seed', 0, 99, 1, 0)}<button class="btn" id="bPaper">Paper setup (N=1000, 5%)</button></div></details>
      </div></div>
      <div class="grid2" style="margin-top:12px"><div><h4>Who catches what — <span class="pill live">● live</span></h4><div id="bHeat"></div><p class="small">recall per anomaly type for this network</p></div><div><h4>Who catches what — <span class="pill paper">▣ paper (Fig. 4)</span></h4><div id="bHeatP"></div><p class="small">per-type recall stated in the paper's text (mean over 30 instances)</p></div></div></div>`;
    const view = new GV.GraphView($('#bCanvas', mount), { height: 540, onHover: (v) => showWhy(v), onSelect: (v) => showWhy(v) });
    let active = new Set(['D4']), comb = 1, W = null, busy = false; view.mode = 'outcome';
    function setLegend() {
      $('#bLegend', mount).innerHTML = view.mode === 'outcome' ? `<span><i style="background:${GV.COLORS.tp}"></i>Caught (true positive)</span><span><i style="background:${GV.COLORS.fp}"></i>False alarm</span><span><i style="background:${GV.COLORS.fn}"></i>Missed fraud</span><span><i style="background:var(--honest)"></i>Honest, not flagged</span>` : legendHTML().replace(/^<div class="legend">|<\/div>$/g, '');
    }
    function flagged() {
      const g = W.g, r = W.run; if (!active.size) return new Set(); const votes = new Uint8Array(g.n);
      for (const d of active) for (const v of r[d].flag) votes[v]++; const need = active.size === 1 ? 1 : comb; const s = new Set(); for (let v = 0; v < g.n; v++) if (votes[v] >= need) s.add(v); return s;
    }
    function refresh() {
      if (!W) return; const fl = flagged(); view.flag = fl; view.mode = view.mode === 'truth' ? 'truth' : 'outcome'; view.draw(); setLegend();
      const m = GE.prf(fl, GE.truthSet(W.g)); $('#bP', mount).textContent = f3(m.p); $('#bR', mount).textContent = f3(m.r); $('#bF', mount).textContent = f3(m.f1); $('#bFl', mount).textContent = m.flagged;
      $('#bCounts', mount).innerHTML = `<b style="color:${GV.COLORS.tp}">${m.tp}</b> caught · <b style="color:${GV.COLORS.fp}">${m.fp}</b> false alarms · <b style="color:${GV.COLORS.fn}">${m.fn}</b> missed &nbsp;<span class="small">(${active.size ? [...active].join(' + ') : 'nothing switched on'}${active.size > 1 ? (comb === 1 ? ', Union' : ', Vote ≥ 2') : ''})</span>`;
      mount.querySelectorAll('#bDets button').forEach((b) => b.classList.toggle('on', active.has(b.dataset.d)));
      mount.querySelectorAll('#bComb button').forEach((b) => b.classList.toggle('on', +b.dataset.c === comb));
      mount.querySelectorAll('#bMode button').forEach((b) => b.classList.toggle('on', b.dataset.m === view.mode));
      heat();
    }
    function heat() {
      const g = W.g, r = W.run; const rows = [['D1', r.D1.flag], ['D2', r.D2.flag], ['D3', r.D3.flag], ['D4', r.D4.flag], ['D5', r.D5.flag], ['Union', r.union], ['Vote ≥ 2', r.vote2]];
      CH.heatmap({ el: $('#bHeat', mount), rows: rows.map((x) => x[0]), cols: ['Fan-out', 'Fan-in', 'Ring', 'Cluster'], v: rows.map((x) => GE.recallByType(g, x[1]).slice(1).map((v) => (v == null ? 0 : v))) });
      const P = PAPER.recallByType; const pr = ['D2', 'D3', 'D4', 'D5'];
      CH.heatmap({ el: $('#bHeatP', mount), rows: pr, cols: ['Fan-out', 'Fan-in', 'Ring', 'Cluster'], v: pr.map((k) => P[k]) });
    }
    function showWhy(v) {
      const el = $('#bWhy', mount); if (v < 0 || !W) { el.textContent = 'Hover any account.'; return; } const g = W.g; const why = GE.why(g, W.run, v);
      el.innerHTML = `<b>Account #${v}</b> · truth: <b>${TYPE_NAMES[g.label[v]]}</b> · sends to ${g.out[v].size}, receives from ${g.inn[v].size}<br>` + (why.length ? why.map((w) => `<div><span class="pill live" style="background:color-mix(in srgb,var(--accent2) 25%,transparent);color:var(--accent2)">${w[0]}</span> ${w[1]}</div>`).join('') : '<span style="color:var(--ink3)">not flagged by any detector</span>');
    }
    const build = UI.serial(async () => {
      const busyEl = $('#bBusy', mount); busyEl.style.display = 'block';
      try { await later(() => 0); const opts = { N: +$('#bN', mount).value, f: +$('#bFs', mount).value, seed: +$('#bS', mount).value }; W = getWorld(opts, true); if (!W.run) W.run = GE.runAll(W.g); view.setGraph(W.g, W.pos); refresh(); }
      finally { busyEl.style.display = 'none'; }
    }, UI.errBox(mount));
    mount.querySelectorAll('#bDets button').forEach((b) => b.addEventListener('click', () => { const d = b.dataset.d; active.has(d) ? active.delete(d) : active.add(d); refresh(); }));
    mount.querySelectorAll('#bComb button').forEach((b) => b.addEventListener('click', () => { comb = +b.dataset.c; refresh(); }));
    mount.querySelectorAll('#bMode button').forEach((b) => b.addEventListener('click', () => { view.mode = b.dataset.m; refresh(); }));
    ['bN', 'bFs', 'bS'].forEach((id) => { const el = $('#' + id, mount); el.addEventListener('input', () => { $('#' + id + 'V', mount).textContent = id === 'bN' ? (+el.value).toLocaleString() : id === 'bFs' ? (el.value * 100).toFixed(0) + '%' : el.value; }); el.addEventListener('change', build); });
    $('#bRnd', mount).addEventListener('click', () => { $('#bS', mount).value = Math.floor(Math.random() * 100); $('#bS', mount).dispatchEvent(new Event('input')); build(); });
    $('#bPaper', mount).addEventListener('click', () => { $('#bN', mount).value = 1000; $('#bFs', mount).value = 0.05; $('#bS', mount).value = 0; ['bN', 'bFs', 'bS'].forEach((id) => $('#' + id, mount).dispatchEvent(new Event('input'))); build(); });
    let tour = 0; const demoBtn = $('#bDemo', mount);
    const stopTour = () => { tour++; demoBtn.dataset.on = '0'; demoBtn.textContent = '▶ Guided tour'; $('#bTour', mount).textContent = ''; };
    demoBtn.addEventListener('click', async () => {
      if (demoBtn.dataset.on === '1') { stopTour(); return; }
      const my = ++tour; demoBtn.dataset.on = '1'; demoBtn.textContent = '■ Stop tour'; view.mode = 'outcome';
      const steps = [[['D1'], 1, 'D1 · only the biggest hubs.'], [['D2'], 1, 'D2 · fan-out and fan-in hubs.'], [['D3'], 1, 'D3 · dense neighbourhoods, many false alarms.'], [['D4'], 1, 'D4 · rings and clusters, very precise.'], [['D5'], 1, 'D5 · small cut-off groups.'], [['D1', 'D2', 'D3', 'D4', 'D5'], 1, 'Union · catches nearly everything, but amber false alarms appear.'], [['D1', 'D2', 'D3', 'D4', 'D5'], 2, 'Vote ≥ 2 · fewer false alarms, more misses.']];
      for (const [a, c, txt] of steps) { if (my !== tour) return; active = new Set(a); comb = c; refresh(); $('#bTour', mount).innerHTML = '<b>' + txt + '</b>'; await new Promise((r) => setTimeout(r, 2800)); }
      if (my === tour) stopTour();
    });
    mount.querySelectorAll('#bDets button, #bComb button, #bMode button').forEach((x) => x.addEventListener('click', () => { if (demoBtn.dataset.on === '1') stopTour(); }));
    UI.onTheme(() => W && refresh());
    build(); root.__lazy = root.__lazy || {}; root.__lazy.bench = build;
  }

  /* ======================================================================
     SIM 5 : Cycle hunt (Algorithm 1 stepper)
     ====================================================================== */
  function demoGraph() {
    // a honest "exchange" hub (0), a planted ring (1-4) and ordinary users (5-25).
    const g = new GE.Graph(); for (let i = 0; i < 26; i++) g.addNode(0); const E = 0;
    [1, 2, 3, 4].forEach((v) => (g.label[v] = GE.RING)); g.addEdge(1, 2); g.addEdge(2, 3); g.addEdge(3, 4); g.addEdge(4, 1); g.addEdge(1, 3); g.addEdge(2, 5);
    for (let u = 5; u <= 20; u++) { if (u % 2) g.addEdge(u, E); else g.addEdge(E, u); } // users deposit to / withdraw from the exchange
    // innocent loops that run THROUGH the exchange: u -> E -> v -> w -> u  (w is an ordinary user who never uses the exchange)
    [[5, 6, 21], [9, 10, 22], [13, 14, 23]].forEach(([a, b, c]) => { g.addEdge(b, c); g.addEdge(c, a); });
    g.addEdge(19, 24); g.addEdge(24, 25);
    return g;
  }
  function simCycles(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>Cycle hunt: watch Algorithm 1 think</h3><span class="tag">Sim 5 · D4 · reverse-BFS + pruned DFS</span></header>
      <div class="split"><div><div class="canvaswrap"><canvas id="cCanvas" style="height:470px"></canvas></div>
        <div class="legend"><span><i style="background:var(--ring)"></i>Planted ring</span><span><i style="background:#c9d1e6;border:1px solid #888"></i>Honest</span><span><i style="background:#555"></i>Excluded hub (degree &gt; Q)</span><span><i style="background:#facc15"></i>start vertex</span><span style="color:var(--bad)">━ path being tried / cycle found</span></div>
        <p class="small" id="cLog" style="margin-top:8px;min-height:3em"></p></div>
      <div class="controls">${liveBadge('tiny demo network — the same code as the full detector')}
        <p class="small" style="margin:0">Click an account to choose the start vertex. The big grey hub is an honest <b>exchange</b>: huge degree, and it connects almost everything to everything.</p>
        ${slider('cL', 'Max cycle length L', 3, 7, 1, 5)}${slider('cQ', 'Hub exclusion: drop accounts above the q-th degree percentile', 80, 100, 5, 95, (v) => 'q = ' + v)}
        <div class="btns"><button class="btn primary" id="cStep">Step ▸</button><button class="btn" id="cPlay">▶ Play</button><button class="btn" id="cReset">⟲ Reset</button><button class="btn" id="cAll">Run on every account</button><button class="btn" id="cTry5">Try #5 with q = 100</button></div><p class="small" style="margin:0">q = 85, 90 and 95 give the same result here. Set q = 100 and start at <b>#5</b> to watch an honest account be dragged into a false cycle through the exchange (5 → 0 → 6 → 21 → 5).</p>
        <div class="metrics" style="grid-template-columns:repeat(2,1fr)"><div class="metric"><span>DFS steps (with pruning)</span><b id="cSteps">–</b></div><div class="metric"><span>DFS steps (no pruning)</span><b id="cSteps2">–</b></div></div>
        <div class="card" style="padding:12px"><h4 style="margin-bottom:4px">Result on the whole demo network</h4><div class="small" id="cAllRes">–</div></div>
        <div class="note paper"><span class="pill paper">▣ paper</span> Without hub exclusion (q = 100) precision collapses from <b>0.900</b> to <b>0.285</b>; at q = 80 so many accounts are removed that recall falls to <b>0.153</b>. Cycles that pass through an exchange connect almost any account to any other.</div>
      </div></div></div>`;
    const g = demoGraph(); const pos = GV.layoutGraph(g, 4); const view = new GV.GraphView($('#cCanvas', mount), { height: 470, labels: true, onSelect: (v) => { if (v >= 0) { start = v; reset(); } } });
    view.dimOthers = false; view.rs = 2.4; view.setGraph(g, pos); view.sizeByDeg = true; view.mode = 'truth';
    let start = 1, ev = [], idx = 0, inH = null, Q = 0, dist = null, playing = null;
    const PSEUDO = ['1  Q ← percentile of degrees at level q', '2  H ← subgraph of vertices with degree ≤ Q', '3  flagged ← ∅', '4  for each vertex v in H:', '5      dist ← reverse BFS from v in H, depth L−1', '6      if DFS(v, [v], 0) succeeds: flagged ∪= {v}', '  DFS(x, path, depth):', '8    for each out-neighbour y of x in H:', '9      if y == v and depth+1 ≥ 3: return True', '10     if y in path or y not in dist: continue', '11     if depth+1+dist[y] > L: continue', '12     if DFS(y, path+[y], depth+1): return True', '13   return False'];
    const lineOf = { bfs: 4, skiphub: 7, skipseen: 9, prune: 10, step: 11, back: 12, found: 8, none: 12 };
    function params() { return { L: +$('#cL', mount).value, q: +$('#cQ', mount).value }; }
    function setup() {
      const { L, q } = params(); $('#cLV', mount).textContent = L; $('#cQV', mount).textContent = 'q = ' + q; const h = GE.hubSubgraph(g, q); inH = h.inH; Q = h.Q;
      view.nodeColor = (v) => { if (v === start) return '#facc15'; if (!inH[v]) return '#555b6e'; if (dist && dist.has(v)) { const d = dist.get(v); return ['#facc15', '#ffe28a', '#ffeebb', '#fff4d6', '#fff9ea'][Math.min(d, 4)] && g.label[v] === 0 ? ['#facc15', '#9fd0ff', '#b9dcff', '#d3e8ff', '#e6f2ff'][Math.min(d, 4)] : null; } return null; };
    }
    function stepsCount(prune) { // number of DFS expansions from `start` without/with pruning
      const { L } = params(); const d = GE.hubSubgraph(g, params().q); const dm = prune ? (function () { const m = new Map(); m.set(start, 0); let fr = [start]; for (let k = 1; k <= L - 1; k++) { const nx = []; for (const x of fr) for (const y of g.inn[x]) if (d.inH[y] && !m.has(y)) { m.set(y, k); nx.push(y); } fr = nx; } return m; })() : null; let cnt = 0; const path = [start];
      (function dfs(x, depth) { for (const y of g.out[x]) { if (!d.inH[y]) continue; if (y === start && depth + 1 >= 3) return true; if (path.indexOf(y) >= 0) continue; if (prune && (!dm.has(y) || depth + 1 + dm.get(y) > L)) continue; if (depth + 1 >= L) continue; cnt++; path.push(y); if (dfs(y, depth + 1)) return true; path.pop(); } return false; })(start, 0); return cnt;
    }
    function reset() {
      stop(); setup(); const { L } = params(); if (!inH[start]) { $('#cLog', mount).innerHTML = `<b>Account #${start} was removed by hub exclusion</b> (degree ${g.deg(start)} &gt; Q = ${Q.toFixed(1)}) — it cannot start a search. Pick another account, or raise q.`; ev = []; idx = 0; dist = null; view.hiEdges = []; view.hiNodes = new Set(); view.draw(); $('#cSteps', mount).textContent = '–'; $('#cSteps2', mount).textContent = '–'; renderAll(); return; }
      ev = GE.traceCycleSearch(g, inH, start, L, 600); dist = new Map(ev[0].dist); idx = 0; view.hiEdges = []; view.hiNodes = new Set([start]); setup(); view.draw();
      $('#cLog', mount).innerHTML = `Start at <b>#${start}</b>. Press <b>Step</b>: first a reverse BFS measures every account's distance to #${start} (shown as shades of blue).`; $('#cSteps', mount).textContent = '–'; $('#cSteps2', mount).textContent = '–'; renderAll();
    }
    function pathAt(i) { let path = [start]; for (let k = 1; k <= i && k < ev.length; k++) { const e = ev[k]; if (e.t === 'step') path = e.path.slice(); else if (e.t === 'back') path = path.slice(0, -1); else if (e.t === 'found') path = e.path.slice(); } return path; }
    function show() {
      if (!ev.length) return; const e = ev[Math.min(idx, ev.length - 1)]; const path = pathAt(idx); view.hiEdges = []; for (let i = 0; i + 1 < path.length; i++) view.hiEdges.push({ u: path[i], v: path[i + 1], color: '#ef4444', w: 3 });
      if (e.t === 'found') view.hiEdges.push({ u: path[path.length - 1], v: path[0], color: '#ef4444', w: 3 }); view.hiNodes = new Set(path); setup(); view.draw();
      let txt = ''; if (e.t === 'bfs') txt = `Reverse BFS done: ${dist.size} accounts can reach #${start} within ${params().L - 1} hops.`; else if (e.t === 'step') txt = `Walk <b>#${e.from} → #${e.to}</b> (path length ${e.path.length - 1}).`; else if (e.t === 'prune') txt = `<span style="color:var(--warn)">Pruned #${e.from} → #${e.to}:</span> it can no longer get back to #${start} within ${params().L} steps.`; else if (e.t === 'skiphub') txt = `<span style="color:var(--ink3)">Skip #${e.to}: removed by hub exclusion.</span>`; else if (e.t === 'skipseen') txt = `Skip #${e.to}: already on the path (cycles must be simple).`; else if (e.t === 'back') txt = `Dead end at #${e.from}: backtrack to #${e.at}.`; else if (e.t === 'found') txt = `<b style="color:var(--good)">Cycle found!</b> ${e.path.map((x) => '#' + x).join(' → ')} → #${e.path[0]} (length ${e.path.length}). Account #${start} is flagged.`; else if (e.t === 'none') txt = `<b>No cycle of length 3–${params().L}</b> through #${start}: not flagged.`;
      $('#cLog', mount).innerHTML = txt; $('#cSteps', mount).textContent = ev.slice(0, idx + 1).filter((x) => x.t === 'step').length;
      if (idx >= ev.length - 1) { $('#cSteps', mount).textContent = stepsCount(true); $('#cSteps2', mount).textContent = stepsCount(false); }
      pseudo(lineOf[e.t]);
    }
    function pseudo(line) { const el = $('#algoText'); if (!el) return; el.innerHTML = PSEUDO.map((l, i) => (i === line ? `<span class="hl">${l}</span>` : l)).join('\n'); }
    function step() { if (!ev.length) return false; if (idx < ev.length - 1) { idx++; show(); return true; } return false; }
    function stop() { if (playing) { clearInterval(playing); playing = null; $('#cPlay', mount).textContent = '▶ Play'; } }
    $('#cStep', mount).addEventListener('click', () => { stop(); step(); });
    $('#cPlay', mount).addEventListener('click', () => { if (playing) { stop(); return; } if (idx >= ev.length - 1) reset(); $('#cPlay', mount).textContent = '⏸ Pause'; playing = setInterval(() => { if (!step()) stop(); }, 550); });
    $('#cReset', mount).addEventListener('click', reset);
    $('#cTry5', mount).addEventListener('click', () => { start = 5; const q = $('#cQ', mount); q.value = 100; reset(); renderAll(); });
    ['cL', 'cQ'].forEach((id) => $('#' + id, mount).addEventListener('input', () => { reset(); renderAll(); }));
    function renderAll() {
      const { L, q } = params(); const r = GE.d4(g, { L, q }); let tp = 0, fp = 0, fn = 0; for (let v = 0; v < g.n; v++) { const t = g.label[v] !== 0, f = r.flag.has(v); if (t && f) tp++; else if (!t && f) fp++; else if (t && !f) fn++; }
      $('#cAllRes', mount).innerHTML = `Q = ${r.Q.toFixed(1)} · flagged <b>${r.flag.size}</b> accounts: <b style="color:var(--good)">${tp}</b> true ring members, <b style="color:var(--warn)">${fp}</b> honest accounts wrongly accused, <b style="color:var(--bad)">${fn}</b> ring members missed.<br>Precision = ${r.flag.size ? (tp / r.flag.size).toFixed(2) : '–'}, recall = ${(tp / (tp + fn)).toFixed(2)}`;
    }
    reset(); pseudo(-1);
  }

  /* ======================================================================
     SIM 6 : The net (precision / recall intuition)
     ====================================================================== */
  function simNet(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>Throw the net: precision, recall and F1</h3><span class="tag">Sim 6 · §2.6</span></header>
      <div class="split"><div><div class="canvaswrap"><canvas id="nCanvas" style="height:330px"></canvas></div>
        <div class="legend"><span><i style="background:var(--good)"></i>Fraud caught</span><span><i style="background:var(--warn)"></i>Innocent accused</span><span><i style="background:var(--bad)"></i>Fraud missed</span><span><i style="background:var(--line)"></i>Innocent, left alone</span></div>
        <p class="small">1,000 accounts, 50 of them fraudulent (the 5%). Each square is an account.</p></div>
      <div class="controls">${liveBadge('real scores from a generated network')}
        <div><div class="small" style="font-weight:700;margin-bottom:6px">Rank accounts by…</div><div class="seg" id="nSrc"><button data-s="D1" class="on">degree z-score</button><button data-s="D2">imbalance</button><button data-s="D3">clustering</button><button data-s="rnd">pure luck 🎲</button></div></div>
        ${slider('nK', 'Flag the top-k most suspicious accounts', 0, 300, 1, 30)}
        <div class="metrics" style="grid-template-columns:repeat(3,1fr)"><div class="metric"><span>Precision</span><b id="nP">–</b></div><div class="metric"><span>Recall</span><b id="nR">–</b></div><div class="metric"><span>F1</span><b id="nF">–</b></div></div>
        <div id="nBars"></div><div class="small" id="nMsg"></div></div></div></div>`;
    const cv = $('#nCanvas', mount), ctx = cv.getContext('2d'); const g = getWorld({ N: 1000, f: 0.05, seed: 0 }, false).g, truth = GE.truthSet(g); const r = GE.runAll(g);
    let src = 'D1', order = [], rnd = GE.makeRng(77); const rndScore = Array.from({ length: g.n }, () => rnd());
    function rank() { const sc = src === 'rnd' ? rndScore : src === 'D1' ? r.D1.score : src === 'D2' ? r.D2.score.map((b, v) => b * Math.log(1 + g.deg(v))) : r.D3.score.map((c, v) => (g.deg(v) >= 3 ? c : 0)); order = Array.from({ length: g.n }, (_, i) => i).sort((a, b) => sc[b] - sc[a]); }
    function draw() {
      const k = +$('#nK', mount).value; $('#nKV', mount).textContent = k; const flag = new Set(order.slice(0, k)); const m = GE.prf(flag, truth);
      const dpr = Math.min(2, devicePixelRatio || 1); const W = Math.max(200, cv.parentElement.clientWidth), H = 330; cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); cv.style.width = W + 'px'; cv.style.height = H + 'px'; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
      const cols = 50, rows = 20, cw = (W - 12) / cols, ch = (H - 12) / rows; const arr = Array.from({ length: g.n }, (_, i) => i).sort((a, b) => (truth.has(b) ? 1 : 0) - (truth.has(a) ? 1 : 0) || a - b); // fraud first for visibility? keep ids but spread
      const place = Array.from({ length: g.n }, (_, i) => i); const rr = GE.makeRng(3); rr.shuffle(place); // fixed scatter of accounts over the grid
      for (let v = 0; v < g.n; v++) { const cell = place[v], x = 6 + (cell % cols) * cw, y = 6 + Math.floor(cell / cols) * ch; const fl = flag.has(v), t = truth.has(v);
        ctx.fillStyle = fl && t ? css('--good') : fl ? css('--warn') : t ? css('--bad') : css('--line'); ctx.globalAlpha = !fl && !t ? 0.55 : 1; ctx.fillRect(x + 1, y + 1, cw - 2, ch - 2); }
      ctx.globalAlpha = 1;
      $('#nP', mount).textContent = k ? f3(m.p) : '–'; $('#nR', mount).textContent = f3(m.r); $('#nF', mount).textContent = f3(m.f1);
      const bar = (lab, v, col) => `<div style="display:flex;align-items:center;gap:8px;margin:4px 0"><span class="small" style="width:70px">${lab}</span><div style="flex:1;height:12px;background:var(--bg2);border-radius:6px;overflow:hidden"><div style="width:${(v * 100).toFixed(1)}%;height:100%;background:${col}"></div></div><span class="num small" style="width:44px;text-align:right">${v.toFixed(2)}</span></div>`;
      $('#nBars', mount).innerHTML = bar('precision', k ? m.p : 0, 'var(--accent2)') + bar('recall', m.r, 'var(--good)') + bar('F1', m.f1, 'var(--accent)') + bar('random 0.05', 0.05, 'var(--ink3)');
      $('#nMsg', mount).innerHTML = src === 'rnd' ? `Pure luck gets precision ≈ <b>0.05</b> — the fraud share. <span class="techonly">That is the paper's random baseline; every detector must beat it.</span>` : k < 20 ? 'A tiny net: almost everything you flag is guilty (high precision), but you miss most of the fraud (low recall).' : k > 120 ? 'A huge net: you catch more fraud, but most accused accounts are innocent — precision falls.' : 'Move the slider: F1 punishes both missing fraud and accusing the innocent.';
    }
    mount.querySelectorAll('#nSrc button').forEach((b) => b.addEventListener('click', () => { src = b.dataset.s; mount.querySelectorAll('#nSrc button').forEach((x) => x.classList.toggle('on', x === b)); rank(); draw(); }));
    $('#nK', mount).addEventListener('input', draw); new ResizeObserver(draw).observe(cv.parentElement); UI.onTheme(draw); rank(); draw();
  }

  root.SIMS2 = { bench: simBench, cycles: simCycles, net: simNet };
})(window);
