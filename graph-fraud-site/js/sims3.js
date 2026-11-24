/* =====================================================================
   sims3.js -- Sim 7 (game), Sim 8 (tuning), Sim 9 (scale), Sim 10 (crowd), what-if loops
   ===================================================================== */
(function (root) {
  'use strict';
  const GE = root.GE, GV = root.GV, CH = root.CH, UI = root.UI, PAPER = root.PAPER;
  const { $, f2, f3, css, getWorld, later, mean, slider, legendHTML, liveBadge } = UI;
  const COL = { D1: '#4da3ff', D2: '#ff8a3d', D3: '#facc15', D4: '#14c9a0', D5: '#ff4f9a', U: '#a78bfa', V2: '#94a3b8' };

  /* ======================================================================
     SIM 7 : You vs the algorithm
     ====================================================================== */
  function simGame(mount) {
    mount.innerHTML = `${liveBadge('N = 220, 15% fraud — same setup as paper Fig. 3')}
      <div class="canvaswrap" style="margin-top:8px"><canvas id="gCanvas" style="height:520px"></canvas></div>
      <div class="metrics" style="margin-top:8px"><div class="metric"><span>Your flags</span><b id="gCount">0 / 33</b></div><div class="metric"><span>Time</span><b id="gTime">–</b></div></div>
      <div class="btns" style="margin-top:8px"><button class="btn primary" id="gReveal">Reveal score</button><button class="btn" id="gReset">Reset</button><button class="btn" id="gAlgo">Show the algorithm's answer</button></div>
      <div class="small" id="gInfo" style="margin:6px 0">Click accounts to flag them (click again to un-flag). Ctrl/⌘ + scroll to zoom into the dense core, drag to pan. Hover for sends/receives — nothing else is known to the detectors either.</div><div id="gRes"></div>`;
    const w = getWorld({ N: 220, f: 0.15, seed: 4 }, true), g = w.g; const truth = GE.truthSet(g), K = truth.size; const run = GE.runAll(g);
    const view = new GV.GraphView($('#gCanvas', mount), { height: 520, onHover: (v) => { if (v >= 0) $('#gInfo', mount).innerHTML = `Account #${v}: sends to <b>${g.out[v].size}</b>, receives from <b>${g.inn[v].size}</b>`; }, onSelect: (v) => { if (v < 0 || revealed) return; if (!t0) { t0 = Date.now(); tick = setInterval(upd, 500); } mine.has(v) ? mine.delete(v) : mine.add(v); view.sel = -1; view.flag = mine; view.draw(); upd(); } });
    view.setGraph(g, w.pos); let mine = new Set(), revealed = false, t0 = 0, tick = null; K && ($('#gCount', mount).textContent = `0 / ${K}`);
    function upd() { $('#gCount', mount).textContent = `${mine.size} / ${K}`; if (t0 && !revealed) $('#gTime', mount).textContent = Math.round((Date.now() - t0) / 1000) + ' s'; }
    view.mode = 'flag'; view.flag = mine; view.draw();
    function row(name, fl) { const m = GE.prf(fl, truth); const rt = GE.recallByType(g, fl); return `<tr><td><b>${name}</b></td><td class="num">${m.tp}</td><td class="num">${m.fp}</td><td class="num">${m.fn}</td><td class="num">${f2(m.p)}</td><td class="num">${f2(m.r)}</td><td class="num"><b>${f2(m.f1)}</b></td><td class="num">${[1, 2, 3, 4].map((i) => (rt[i] == null ? '–' : Math.round(rt[i] * 100) + '%')).join(' · ')}</td></tr>`; }
    $('#gReveal', mount).addEventListener('click', () => { revealed = true; clearInterval(tick); view.mode = 'outcome'; view.flag = mine; view.draw();
      $('#gRes', mount).innerHTML = `<div class="tablewrap"><table class="t"><tr><th></th><th class="num">caught</th><th class="num">false alarms</th><th class="num">missed</th><th class="num">P</th><th class="num">R</th><th class="num">F1</th><th class="num">recall by type (out · in · ring · cluster)</th></tr>${row('You', mine)}${row('Union rule', run.union)}${row('Short cycles (D4)', run.D4.flag)}</table></div><p class="small">People spot the big hubs quickly, but rings hidden in the giant component are easy to miss — a cycle search finds them mechanically. Paper (Fig. 3): Union found all 33 planted accounts, at the cost of 20 honest accounts flagged.</p>`; });
    $('#gReset', mount).addEventListener('click', () => { mine = new Set(); revealed = false; t0 = 0; clearInterval(tick); $('#gTime', mount).textContent = '–'; view.mode = 'flag'; view.flag = mine; view.draw(); upd(); $('#gRes', mount).innerHTML = ''; });
    $('#gAlgo', mount).addEventListener('click', () => { view.mode = 'outcome'; view.flag = run.union; view.draw(); $('#gRes', mount).innerHTML = `<p class="small">Showing the <b>Union rule</b>: green = caught, amber = false alarm, red = missed.</p>`; });
  }

  /* ======================================================================
     SIM 8 : Tuning lab
     ====================================================================== */
  const SWEEPS = {
    D1tau: { det: 'D1', name: 'D1 · threshold τ', grid: [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5], def: 3, key: 'tau', fmt: (v) => v.toFixed(1),
      paper: { P: [.671, null, null, null, .661, null, null, null, null], R: [.523, null, null, null, .200, null, null, null, null], F: [.586, null, null, null, .303, null, null, null, 0] }, note: 'Paper: F1 falls from 0.586 at τ = 1 to 0.303 at τ = 3 and 0 at τ = 5, because μ+τσ moves above the planted hubs.' },
    D2b: { det: 'D2', name: 'D2 · threshold β*', grid: [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0], def: 0.9, key: 'betaStar', fmt: (v) => v.toFixed(2),
      paper: { P: [null, null, null, null, .699, null, null], R: [.52, .52, .52, .52, .463, null, null], F: [null, null, null, null, .555, null, null] }, note: 'Paper: plateau for β* ≤ 0.8 (recall 0.52, all hubs found) then a cliff — at β* = 1 only perfect sources and sinks (about one third of hubs) qualify.' },
    D3c: { det: 'D3', name: 'D3 · threshold c*', grid: [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], def: 0.5, key: 'cStar', fmt: (v) => v.toFixed(1),
      paper: { P: [null, null, null, .328, .431, null, null, null], R: [null, null, null, .192, .184, null, null, null], F: [null, null, null, .239, .256, null, null, null] }, note: 'Paper: D3 never becomes good — F1 stays below 0.26 for every c*; only clusters have dense neighbourhoods.' },
    D4L: { det: 'D4', name: 'D4 · max cycle length L', grid: [3, 4, 5, 6, 7], def: 5, key: 'L', fmt: (v) => v,
      paper: { P: [null, null, .900, .901, null], R: [.323, null, .467, .476, null], F: [null, null, .613, .621, null] }, note: 'Paper: recall grows 0.323 → 0.467 → 0.476 and saturates for L ≥ 6 because rings have at most six members.' },
    D4q: { det: 'D4', name: 'D4 · hub percentile q', grid: [80, 90, 95, 98, 100], def: 95, key: 'q', fmt: (v) => v,
      paper: { P: [null, null, .900, null, .285], R: [.153, null, .467, null, null], F: [null, null, .613, null, null] }, note: 'Paper: without hub exclusion (q = 100) precision collapses 0.900 → 0.285; with q = 80 too many ring members are removed and recall falls to 0.153.' },
    D5s: { det: 'D5', name: 'D5 · min component size s_min', grid: [3, 4, 5, 6, 7, 8], def: 5, key: 'smin', fmt: (v) => v,
      paper: { P: [null, null, .452, 1.0, null, null], R: [null, null, .193, .143, null, null], F: [null, null, .266, null, null, null] }, note: 'Paper: at s_min = 6 precision is 1.000 but recall 0.143 — honest components have ≤ 5 accounts, planted clusters 5–8. It works only because of that size gap.' }
  };
  function simTuning(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>Tuning lab: turn one knob, watch three numbers</h3><span class="tag">Sim 8 · Experiment 1 · Table 5 · Fig. 5</span></header>
      <div class="seg" id="tSweep" style="flex-wrap:wrap;margin-bottom:12px">${Object.keys(SWEEPS).map((k, i) => `<button data-k="${k}" class="${i ? '' : 'on'}">${SWEEPS[k].name}</button>`).join('')}</div>
      <div class="split" style="grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)"><div class="card"><div id="tChart"></div><div class="legend" style="justify-content:center"><span class="pill live">● live: lines, 5 networks, N=1000</span><span class="pill paper">▣ paper: dots, values stated in the report</span></div></div>
      <div class="controls"><div id="tSlWrap"></div><div class="metrics" style="grid-template-columns:repeat(3,1fr)"><div class="metric"><span>Precision</span><b id="tP">–</b></div><div class="metric"><span>Recall</span><b id="tR">–</b></div><div class="metric"><span>F1</span><b id="tF">–</b></div></div>
        <div class="note paper" id="tNote"></div><div class="small techonly" id="tTech"></div></div></div></div>`;
    const seeds = [0, 1, 2, 3, 4]; let cur = 'D1tau', cache = {}, graphs = null, sclust = null;
    function prep() { if (graphs) return; graphs = seeds.map((s) => getWorld({ N: 1000, f: 0.05, seed: s }, false).g); sclust = graphs.map((g) => { const C = new Array(g.n), K = new Array(g.n); for (let v = 0; v < g.n; v++) { const c = GE.clustering(g, v); C[v] = c.C; K[v] = c.k; } return { C, K }; }); }
    function evalAt(k, val) {
      const S = SWEEPS[k]; const ps = [], rs = [], fs = []; graphs.forEach((g, i) => {
        const truth = GE.truthSet(g); let flag;
        if (S.det === 'D1') flag = GE.d1(g, { tau: val }).flag; else if (S.det === 'D2') flag = GE.d2(g, { betaStar: val }).flag; else if (S.det === 'D3') { flag = new Set(); for (let v = 0; v < g.n; v++) if (sclust[i].K[v] >= 3 && sclust[i].C[v] >= val) flag.add(v); }
        else if (S.det === 'D4') flag = GE.d4(g, k === 'D4L' ? { L: val } : { q: val }).flag; else flag = GE.d5(g, { smin: val }).flag;
        const m = GE.prf(flag, truth); ps.push(m.flagged ? m.p : 0); rs.push(m.r); fs.push(m.f1); });
      return { P: mean(ps), R: mean(rs), F: mean(fs) };
    }
    async function sweep(k) { if (cache[k]) return cache[k]; prep(); const out = { P: [], R: [], F: [] }; for (const v of SWEEPS[k].grid) { await later(() => 0); const e = evalAt(k, v); out.P.push(e.P); out.R.push(e.R); out.F.push(e.F); } cache[k] = out; return out; }
    async function show(k) {
      cur = k; const S = SWEEPS[k]; mount.querySelectorAll('#tSweep button').forEach((b) => b.classList.toggle('on', b.dataset.k === k));
      $('#tSlWrap', mount).innerHTML = `<div class="ctl"><label for="tSl">${S.name}<b id="tSlV"></b></label><input type="range" id="tSl" min="0" max="${S.grid.length - 1}" step="1" value="${S.grid.indexOf(S.def)}"></div><p class="small">dotted default = ${S.fmt(S.def)}</p>`;
      $('#tNote', mount).innerHTML = `<span class="pill paper">▣ paper</span> ` + S.note; $('#tChart', mount).innerHTML = '<p class="small">computing on 5 generated networks…</p>';
      const live = await sweep(k); if (cur !== k) return; const labels = S.grid.map(S.fmt);
      const ser = [{ name: 'precision (live)', color: '#4da3ff', y: live.P }, { name: 'recall (live)', color: '#22c55e', y: live.R }, { name: 'F1 (live)', color: '#f59e0b', y: live.F, hi: true }, { name: 'precision ▣', color: '#1d4ed8', y: S.paper.P, dash: '1 6' }, { name: 'recall ▣', color: '#15803d', y: S.paper.R, dash: '1 6' }, { name: 'F1 ▣', color: '#b45309', y: S.paper.F, dash: '1 6', hi: true }];
      CH.lineChart({ el: $('#tChart', mount), x: S.grid, xIndex: true, xFmt: S.fmt, xLabel: S.name.split('·')[1].trim(), yLabel: 'score', yMax: 1, series: ser, legend: false, h: 300 });
      // restyle paper series as square markers: replace circles for dotted series with squares
      $('#tChart', mount).querySelectorAll('path.ln').forEach((p) => { if ((p.getAttribute('data-series') || '').includes('▣')) p.style.display = 'none'; });
      const upd = () => { const i = +$('#tSl', mount).value; $('#tSlV', mount).textContent = S.fmt(S.grid[i]); $('#tP', mount).textContent = f3(live.P[i]); $('#tR', mount).textContent = f3(live.R[i]); $('#tF', mount).textContent = f3(live.F[i]); mark(i); };
      function mark(i) { const svg = $('#tChart svg', mount); if (!svg) return; svg.querySelectorAll('.mk').forEach((x) => x.remove()); const W = 560, Hh = 300, ml = 48, mr = 14, mt = 14, mb = 44; const x = ml + (i / (S.grid.length - 1)) * (W - ml - mr); const l = CH.el('line', { x1: x, x2: x, y1: mt, y2: Hh - mb, stroke: css('--accent'), 'stroke-width': 2, 'stroke-dasharray': '3 3', class: 'mk' }, svg); [live.P[i], live.R[i], live.F[i]].forEach((v, j) => CH.el('circle', { cx: x, cy: Hh - mb - v * (Hh - mt - mb), r: 6, fill: 'none', stroke: ['#4da3ff', '#22c55e', '#f59e0b'][j], 'stroke-width': 2.4, class: 'mk' }, svg)); }
      $('#tSl', mount).addEventListener('input', upd); upd();
      // paper dots as filled squares
      const svg = $('#tChart svg', mount); const W = 560, Hh = 300, ml = 48, mr = 14, mt = 14, mb = 44; ['P', 'R', 'F'].forEach((key, j) => S.paper[key].forEach((v, i) => { if (v == null) return; const x = ml + (i / (S.grid.length - 1)) * (W - ml - mr), y = Hh - mb - v * (Hh - mt - mb); CH.el('rect', { x: x - 5, y: y - 5, width: 10, height: 10, fill: ['#1d4ed8', '#15803d', '#b45309'][j], stroke: '#fff', 'stroke-width': 1 }, svg).addEventListener('pointermove', (e) => CH.showTip(`<div class="tt">▣ paper · ${['precision', 'recall', 'F1'][j]}</div>${S.name.split('·')[1].trim()} = ${S.fmt(S.grid[i])}<b>${v.toFixed(3)}</b>`, e)); }));
      svg.querySelectorAll('rect[fill="#1d4ed8"],rect[fill="#15803d"],rect[fill="#b45309"]').forEach((r) => r.addEventListener('pointerleave', CH.hideTip));
      $('#tTech', mount).innerHTML = k === 'D2b' ? `Analytic check: a hub with 2 opposite edges has β = (d − 2)/(d + 2); this reaches 0.9 only when d ≥ 38. With d uniform on 25…70 and 0–2 reverse edges, the expected miss rate at β* = 0.9 is ≈ 9%, predicting hub recall ≈ 0.91 (paper: 0.89).` : '';
    }
    mount.querySelectorAll('#tSweep button').forEach((b) => b.addEventListener('click', () => show(b.dataset.k)));
    root.__lazy = root.__lazy || {}; root.__lazy.tuning = () => show('D1tau');
  }

  /* ======================================================================
     SIM 9 : Scale lab
     ====================================================================== */
  function simScale(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>Scale lab: re-run the size experiment</h3><span class="tag">Sim 9 · Experiment 2 · Tables 6–7</span></header>
      <div class="grid2" style="align-items:start"><div class="controls">${liveBadge('real generation + all five detectors in your browser')}${slider('sK', 'Networks per size (paper: 15)', 1, 15, 1, 3)}
        <div class="btns"><button class="btn primary" id="sRun">▶ Run size experiment</button></div><div style="height:10px;background:var(--bg2);border-radius:6px;overflow:hidden"><div id="sBar" style="height:100%;width:0;background:var(--accent);transition:width .2s"></div></div><div class="small" id="sMsg">N ∈ {300, 600, 1,000, 2,000, 4,000} at 5% fraud. Solid lines = paper, dashed = your run.</div></div>
        <div class="card" style="padding:12px"><h4 style="margin-bottom:4px">Do the paper's trends reproduce?</h4><div class="small" id="sChecks">Run the experiment to find out.</div></div></div>
      <div class="grid2" style="margin-top:14px"><div class="card"><h4>Mean F1 vs accounts</h4><div id="sF"></div></div><div class="card"><h4>Run time (ms, <i>your device</i>)</h4><div id="sT"></div></div></div></div>`;
    const Ns = [300, 600, 1000, 2000, 4000]; const T6 = PAPER.table6, T7 = PAPER.table7; let res = null;
    function chart() {
      const ser = [{ name: 'D1 paper', color: COL.D1, y: T6.D1 }, { name: 'D4 paper', color: COL.D4, y: T6.D4 }, { name: 'Union paper', color: COL.U, y: T6.U }];
      if (res) ser.push({ name: 'D1 live', color: COL.D1, y: res.F.D1, dash: '4 4' }, { name: 'D4 live', color: COL.D4, y: res.F.D4, dash: '4 4' }, { name: 'Union live', color: COL.U, y: res.F.U, dash: '4 4' });
      CH.lineChart({ el: $('#sF', mount), x: Ns, xIndex: true, xFmt: (v) => v.toLocaleString(), xLabel: 'accounts N', yLabel: 'mean F1', yMax: 1, series: ser, h: 300 });
      const ts = [{ name: 'all five — paper', color: COL.U, y: T7.all }, { name: 'D3 — paper', color: COL.D3, y: T7.D3 }, { name: 'D4 — paper', color: COL.D4, y: T7.D4 }];
      if (res) ts.push({ name: 'all five — yours', color: COL.U, y: res.T.all, dash: '4 4' }, { name: 'D3 — yours', color: COL.D3, y: res.T.D3, dash: '4 4' }, { name: 'D4 — yours', color: COL.D4, y: res.T.D4, dash: '4 4' });
      CH.lineChart({ el: $('#sT', mount), x: Ns, xIndex: true, xFmt: (v) => v.toLocaleString(), xLabel: 'accounts N', yLabel: 'ms', yMax: Math.max(350, res ? Math.max(...res.T.all) * 1.1 : 0), yFmt: (v) => Math.round(v), series: ts, h: 300 });
    }
    $('#sRun', mount).addEventListener('click', async () => {
      const K = +$('#sK', mount).value; const F = { D1: [], D2: [], D3: [], D4: [], D5: [], U: [], V2: [] }, T = { D1: [], D3: [], D4: [], all: [] }; const total = Ns.length * K; let done = 0; $('#sRun', mount).disabled = true;
      for (const N of Ns) {
        const f = { D1: [], D2: [], D3: [], D4: [], D5: [], U: [], V2: [] }, tm = { D1: [], D3: [], D4: [], all: [] };
        for (let s = 0; s < K; s++) { await later(() => 0); const g = GE.generate({ N, f: 0.05, seed: 100 + s }); const truth = GE.truthSet(g); const t0 = performance.now(); const r = {}; const ti = {};
          for (const d of ['D1', 'D2', 'D3', 'D4', 'D5']) { const a = performance.now(); r[d] = GE[d.toLowerCase()](g); ti[d] = performance.now() - a; }
          const votes = new Uint8Array(g.n); for (const d of Object.keys(r)) for (const v of r[d].flag) votes[v]++; const U = new Set(), V2 = new Set(); votes.forEach((c, v) => { if (c >= 1) U.add(v); if (c >= 2) V2.add(v); });
          for (const d of ['D1', 'D2', 'D3', 'D4', 'D5']) f[d].push(GE.prf(r[d].flag, truth).f1); f.U.push(GE.prf(U, truth).f1); f.V2.push(GE.prf(V2, truth).f1);
          tm.D1.push(ti.D1); tm.D3.push(ti.D3); tm.D4.push(ti.D4); tm.all.push(performance.now() - t0); done++; $('#sBar', mount).style.width = (100 * done / total) + '%'; $('#sMsg', mount).textContent = `N = ${N.toLocaleString()}, network ${s + 1}/${K}…`; }
        for (const k in F) F[k].push(mean(f[k])); for (const k in T) T[k].push(mean(tm[k]));
      }
      res = { F, T }; chart(); $('#sRun', mount).disabled = false; $('#sMsg', mount).textContent = 'Done. Solid lines = paper, dashed = your run.';
      const d4 = F.D4.slice(1), spread = Math.max(...d4) - Math.min(...d4); const chk = (ok, t) => `<div>${ok ? '✅' : '⚠️'} ${t}</div>`;
      $('#sChecks', mount).innerHTML = chk(F.D1[4] < 0.05, `D1 (3σ) falls to ≈ 0 at N = 4,000: <b>${f3(F.D1[4])}</b> (paper 0.000)`) + chk(F.D1[0] > F.D1[4] + 0.15, `D1 declines with N: ${f3(F.D1[0])} → ${f3(F.D1[4])}`) + chk(spread < 0.12, `D4 stays steady for N ≥ 600 (spread ${spread.toFixed(3)}; paper 0.611–0.625)`) + chk(F.U[2] >= F.D4[2] - 0.02, `Union ≳ best single detector at N=1000 (${f3(F.U[2])} vs ${f3(F.D4[2])})`) + chk(T.D3[4] / T.D3[0] > T.D4[4] / T.D4[0] * 0.9, `D3 cost grows faster than D4 (${(T.D3[4] / T.D3[0]).toFixed(1)}× vs ${(T.D4[4] / T.D4[0]).toFixed(1)}×; paper 18.5× vs 9.3×)`) + '<div class="small" style="margin-top:4px">Timings are for your device — not comparable with the paper\'s cloud core.</div>';
    });
    chart();
  }

  /* ======================================================================
     SIM 10 : Rare-fraud crowd
     ====================================================================== */
  function simCrowd(mount) {
    const SH = [0.01, 0.025, 0.05, 0.10, 0.15];
    mount.innerHTML = `<div class="sim"><header><h3>The crowd: when fraud is rare, alarms mislead</h3><span class="tag">Sim 10 · Experiment 3 · Table 8</span></header>
      <div class="split"><div><div class="canvaswrap"><canvas id="rCanvas" style="height:300px"></canvas></div><div class="legend"><span><i style="background:var(--good)"></i>Fraud caught</span><span><i style="background:var(--warn)"></i>False alarm</span><span><i style="background:var(--bad)"></i>Fraud missed</span><span><i style="background:var(--line)"></i>Innocent, left alone</span></div></div>
      <div class="controls">${liveBadge('Union rule (any of D1–D5), 1,000 accounts, mean of 5 networks')}${slider('rS', 'Share of accounts that are fraudulent', 0, 4, 1, 2, (i) => (SH[i] * 100).toFixed(1).replace('.0', '') + '%')}
        <div class="metrics"><div class="metric"><span>Caught</span><b id="rTP">–</b></div><div class="metric"><span>False alarms</span><b id="rFP">–</b></div><div class="metric"><span>Precision</span><b id="rP">–</b></div><div class="metric"><span>F1</span><b id="rF">–</b></div></div>
        <div class="small" id="rMsg"></div><div class="note paper"><span class="pill paper">▣ paper</span> Union at <b>1%</b>: precision 0.123 (≈ 9.5 hits vs ≈ 70 false alarms), F1 0.217. At <b>15%</b>: precision 0.812 (≈ 143 vs ≈ 34), F1 0.875. Recall stays 0.94–0.95 throughout.</div></div></div></div>`;
    const cv = $('#rCanvas', mount), ctx = cv.getContext('2d'); const cache = {};
    function compute(f) { if (cache[f]) return cache[f]; const stats = []; let first = null; for (let s = 0; s < 5; s++) { const g = getWorld({ N: 1000, f, seed: s }, false).g, r = GE.runAll(g), truth = GE.truthSet(g), m = GE.prf(r.union, truth); stats.push(m); if (!s) first = { g, flag: r.union, truth }; } return (cache[f] = { stats, first }); }
    function draw() {
      const i = +$('#rS', mount).value; $('#rSV', mount).textContent = (SH[i] * 100).toFixed(1).replace('.0', '') + '%'; const c = compute(SH[i]); const st = c.stats;
      const tp = mean(st.map((x) => x.tp)), fp = mean(st.map((x) => x.fp)), p = mean(st.map((x) => x.p)), f1 = mean(st.map((x) => x.f1)), rc = mean(st.map((x) => x.r));
      $('#rTP', mount).textContent = tp.toFixed(1); $('#rFP', mount).textContent = fp.toFixed(1); $('#rP', mount).textContent = f3(p); $('#rF', mount).textContent = f3(f1);
      $('#rMsg', mount).innerHTML = `Recall is ≈ <b>${rc.toFixed(2)}</b> — almost every fraudster is caught — but out of <b>${(tp + fp).toFixed(0)}</b> alarms, only <b>${(100 * p).toFixed(0)}%</b> are real. ${p < 0.5 ? '<b style="color:var(--bad)">More than half the alarms are false.</b>' : 'Alarms are mostly real now: fraud is common enough that false alarms are a smaller share.'}`;
      const r2 = cv.parentElement.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1), W = r2.width, H = 300; cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px'; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
      const { g, flag, truth } = c.first; const cols = 50, rows = 20, cw = (W - 12) / cols, chh = (H - 12) / rows; const place = Array.from({ length: g.n }, (_, k) => k); GE.makeRng(3).shuffle(place);
      for (let v = 0; v < g.n; v++) { const cell = place[v], x = 6 + (cell % cols) * cw, y = 6 + Math.floor(cell / cols) * chh; const fl = flag.has(v), t = truth.has(v); ctx.fillStyle = fl && t ? css('--good') : fl ? css('--warn') : t ? css('--bad') : css('--line'); ctx.globalAlpha = !fl && !t ? 0.55 : 1; ctx.fillRect(x + 1, y + 1, cw - 2, chh - 2); } ctx.globalAlpha = 1;
    }
    $('#rS', mount).addEventListener('input', draw); new ResizeObserver(draw).observe(cv.parentElement);
    root.__lazy = root.__lazy || {}; root.__lazy.crowd = draw; draw();
  }

  /* ======================================================================
     What-if : legitimate loops (beyond the paper)
     ====================================================================== */
  function simWhatIf(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>What if honest accounts also form loops?</h3><span class="tag">Beyond the paper · limitation (2)</span></header>
      <div class="split"><div><div class="canvaswrap"><canvas id="wiCanvas" style="height:440px"></canvas><div id="wiBusy" class="small" style="position:absolute;left:12px;top:10px;display:none">building…</div></div><div class="legend"><span><i style="background:${GV.COLORS.tp}"></i>Fraud caught by D4</span><span><i style="background:${GV.COLORS.fp}"></i>Honest account wrongly flagged</span><span><i style="background:${GV.COLORS.fn}"></i>Fraud missed</span></div></div>
      <div class="controls"><span class="pill live">● live · beyond the paper · what-if</span>${slider('wiK', 'Legitimate loops added (e.g. exchange deposit / withdrawal flows)', 0, 30, 1, 12)}
        <div class="metrics" style="grid-template-columns:repeat(2,1fr)"><div class="metric"><span>D4 precision — no loops</span><b id="wiP0">–</b></div><div class="metric"><span>D4 precision — with loops</span><b id="wiP1">–</b></div><div class="metric"><span>D4 recall — no loops</span><b id="wiR0">–</b></div><div class="metric"><span>D4 recall — with loops</span><b id="wiR1">–</b></div></div>
        <div class="note warn">The paper's honest graph is almost acyclic (largest strongly connected component 44 of 1,000). Real ledgers are not: legitimate loops make short cycles a much noisier signal, so D4's precision falls. This is the paper's own limitation (2), made visible.</div></div></div></div>`;
    const view = new GV.GraphView($('#wiCanvas', mount), { height: 440 }); let busy = false;
    async function go() { if (busy) return; busy = true; $('#wiBusy', mount).style.display = 'block'; await later(() => 0); const k = +$('#wiK', mount).value; $('#wiKV', mount).textContent = k;
      const a = getWorld({ N: 500, f: 0.05, seed: 6, loops: 0 }, false).g, b = getWorld({ N: 500, f: 0.05, seed: 6, loops: k }, true); const A = GE.prf(GE.d4(a).flag, GE.truthSet(a)), B = GE.prf(GE.d4(b.g).flag, GE.truthSet(b.g));
      $('#wiP0', mount).textContent = f3(A.p); $('#wiP1', mount).textContent = f3(B.p); $('#wiR0', mount).textContent = f3(A.r); $('#wiR1', mount).textContent = f3(B.r);
      view.setGraph(b.g, b.pos); view.mode = 'outcome'; view.flag = GE.d4(b.g).flag; view.draw(); $('#wiBusy', mount).style.display = 'none'; busy = false; }
    $('#wiK', mount).addEventListener('input', () => { $('#wiKV', mount).textContent = $('#wiK', mount).value; }); $('#wiK', mount).addEventListener('change', go);
    root.__lazy = root.__lazy || {}; root.__lazy.whatif = go;
  }


  /* ======================================================================
     Reproduce Table 4 (30 networks, seeds 0-29)
     ====================================================================== */
  function simRepro(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>Reproduce Table 4 in your browser</h3><span class="tag">30 networks · N = 1,000 · f = 5% · seeds 0–29</span></header>
      <p class="small">Runs the paper's baseline experiment with this site's JavaScript port. Same recipe, different random numbers — so expect the same <b>ranking and rough size</b> of every number, not identical digits.</p>
      <div class="btns"><button class="btn primary" id="pRun">▶ Run 30 networks</button><span class="small" id="pMsg"></span></div><div class="tablewrap" style="margin-top:10px"><table class="t" id="pTbl"></table></div><div class="small" id="pChk" style="margin-top:8px"></div></div>`;
    $('#pRun', mount).addEventListener('click', async () => {
      const ids = ['D1', 'D2', 'D3', 'D4', 'D5', 'U', 'V2'], acc = {}; ids.forEach((d) => (acc[d] = { p: [], r: [], f: [] })); $('#pRun', mount).disabled = true;
      for (let s = 0; s < 30; s++) { await later(() => 0); $('#pMsg', mount).textContent = `network ${s + 1}/30…`; const g = GE.generate({ N: 1000, f: 0.05, seed: s }), truth = GE.truthSet(g), r = GE.runAll(g);
        const sets = { D1: r.D1.flag, D2: r.D2.flag, D3: r.D3.flag, D4: r.D4.flag, D5: r.D5.flag, U: r.union, V2: r.vote2 }; for (const d of ids) { const m = GE.prf(sets[d], truth); acc[d].p.push(m.flagged ? m.p : 0); acc[d].r.push(m.r); acc[d].f.push(m.f1); } }
      const rows = PAPER.table4.map((t) => { const a = acc[t.id], P = mean(a.p), R = mean(a.r), F = mean(a.f); return { t, P, R, F }; });
      $('#pTbl', mount).innerHTML = '<tr><th>Detector</th><th class="num"><span class="pill live">live</span> P</th><th class="num"><span class="pill paper">paper</span> P</th><th class="num"><span class="pill live">live</span> R</th><th class="num"><span class="pill paper">paper</span> R</th><th class="num"><span class="pill live">live</span> F1</th><th class="num"><span class="pill paper">paper</span> F1</th></tr>' + rows.map((r) => `<tr><td><b>${r.t.name}</b></td><td class="num">${f3(r.P)}</td><td class="num" style="color:var(--ink3)">${f3(r.t.P)}</td><td class="num">${f3(r.R)}</td><td class="num" style="color:var(--ink3)">${f3(r.t.R)}</td><td class="num"><b>${f3(r.F)}</b></td><td class="num" style="color:var(--ink3)">${f3(r.t.F1)}</td></tr>`).join('');
      const byF = rows.slice().sort((a, b) => b.F - a.F).map((r) => r.t.id), single = rows.filter((r) => /^D/.test(r.t.id)).sort((a, b) => b.F - a.F)[0].t.id, bestP = rows.filter((r) => /^D/.test(r.t.id)).sort((a, b) => b.P - a.P)[0].t.id, U = rows.find((r) => r.t.id === 'U'), V = rows.find((r) => r.t.id === 'V2');
      const chk = (ok, t) => `<div>${ok ? '✅' : '⚠️'} ${t}</div>`;
      $('#pChk', mount).innerHTML = chk(single === 'D4', 'Short cycles (D4) is the best single detector') + chk(bestP === 'D4', 'D4 has the highest precision of the single detectors') + chk(byF[0] === 'U', 'The Union rule has the best F1 overall') + chk(U.R > 0.9, `Union recall > 0.9 (live ${f3(U.R)}, paper 0.938)`) + chk(V.P > U.P && V.R < U.R, 'Vote ≥ 2 trades recall for precision') + chk(rows[0].F < rows[3].F - 0.15, 'The 3σ degree rule (D1) is far behind D4');
      $('#pMsg', mount).textContent = 'done'; $('#pRun', mount).disabled = false; });
  }

  root.SIMS3 = { repro: simRepro, game: simGame, tuning: simTuning, scale: simScale, crowd: simCrowd, whatif: simWhatIf };
})(window);
