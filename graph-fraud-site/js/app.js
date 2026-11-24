/* =====================================================================
   app.js -- page shell: nav, toggles, present mode, paper tables/charts,
   hero + archetype animations, lazy-mounting of the simulations
   ===================================================================== */
(function () {
  'use strict';
  // defaults for hosts that don't let the page set <html>/<body> attributes (e.g. when published as an artifact)
  if (!document.documentElement.dataset.theme) document.documentElement.dataset.theme = 'light';
  document.body.classList.add('plain');
  const GE = window.GE, GV = window.GV, CH = window.CH, UI = window.UI, P = window.PAPER;
  const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const f3 = (x) => (x == null ? '–' : x.toFixed(3));
  const COL = { D1: '#4da3ff', D2: '#ff8a3d', D3: '#facc15', D4: '#14c9a0', D5: '#ff4f9a', U: '#a78bfa', V2: '#94a3b8' };
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } } };

  /* ---------- nav ---------- */
  const secs = $$('section[data-title]'); const nav = $('#nav');
  secs.forEach((s) => { const a = document.createElement('a'); a.href = '#' + s.id; a.textContent = s.dataset.title; a.dataset.id = s.id; nav.appendChild(a); });
  const io = new IntersectionObserver((es) => { es.forEach((e) => { if (e.isIntersecting) { $$('#nav a').forEach((a) => a.classList.toggle('on', a.dataset.id === e.target.id)); const on = $('#nav a.on'); if (on) on.scrollIntoView({ block: 'nearest', inline: 'center' }); } }); }, { rootMargin: '-45% 0px -50% 0px' });
  secs.forEach((s) => io.observe(s));
  addEventListener('scroll', () => { const h = document.documentElement; $('#progress').style.width = (100 * h.scrollTop / (h.scrollHeight - h.clientHeight || 1)) + '%'; }, { passive: true });
  $$('[data-goto]').forEach((b) => b.addEventListener('click', () => { const t = $(b.dataset.goto); if (t) t.scrollIntoView({ behavior: 'smooth' }); }));

  /* ---------- reveal on scroll ---------- */
  const rv = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); rv.unobserve(e.target); } }), { threshold: 0.08 });
  $$('.reveal').forEach((e) => rv.observe(e));

  /* ---------- toggles ---------- */
  function setTheme(t) { document.documentElement.dataset.theme = t; $('#btnTheme').textContent = t === 'light' ? 'Dark' : 'Light'; store.set('theme', t); window.dispatchEvent(new Event('themechange')); }
  const lvl = (on) => { document.body.classList.toggle('plain', !on); $('#btnLevel').setAttribute('aria-pressed', on); $('#btnLevel').textContent = on ? 'Plain language' : 'Technical detail'; store.set('tech', on ? '1' : '0'); };
  $('#btnTheme').addEventListener('click', () => { setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'); redrawAll(); });
  $('#btnLevel').addEventListener('click', () => lvl(document.body.classList.contains('plain')));
  if (store.get('theme')) setTheme(store.get('theme')); if (store.get('tech') === '1') lvl(true);
  function redrawAll() { drawPaperCharts(); setTimeout(() => window.dispatchEvent(new Event('resize')), 30); window.__views && window.__views.forEach((v) => v.draw()); }

  /* ---------- present mode ---------- */
  let present = false, notesOn = false;
  function setPresent(on) { present = on; document.body.classList.toggle('present', on); document.documentElement.style.fontSize = on ? '120%' : ''; $('#btnPresent').setAttribute('aria-pressed', on); $('#btnPresent').textContent = on ? 'Exit' : 'Present'; $('header.top').style.display = on ? 'none' : ''; if (!on) { notesOn = false; $('#notes').style.display = 'none'; if (document.fullscreenElement) document.exitFullscreen(); } showNotes(); }
  $('#btnPresent').addEventListener('click', () => setPresent(!present));
  function curSec() { let best = 0, bd = 1e9; secs.forEach((s, i) => { const d = Math.abs(s.getBoundingClientRect().top); if (d < bd) { bd = d; best = i; } }); return best; }
  function showNotes() { const n = $('#notes'); if (!notesOn) { n.style.display = 'none'; return; } n.style.display = 'block'; n.innerHTML = '<b>Speaker notes · ' + secs[curSec()].dataset.title + '</b><br>' + (secs[curSec()].dataset.note || '—'); }
  addEventListener('keydown', (e) => {
    if (/INPUT|TEXTAREA|SELECT/.test((e.target.tagName || ''))) return; const k = e.key.toLowerCase();
    if (k === 'p') setPresent(!present); else if (k === 't') lvl(document.body.classList.contains('plain')); else if (k === 'n' && present) { notesOn = !notesOn; showNotes(); } else if (k === 'f' && present) { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen && document.documentElement.requestFullscreen(); }
    else if (present && (k === 'arrowright' || k === 'arrowdown' || k === ' ' || k === 'pagedown')) { e.preventDefault(); const i = Math.min(secs.length - 1, curSec() + 1); secs[i].scrollIntoView({ behavior: 'smooth' }); setTimeout(showNotes, 700); }
    else if (present && (k === 'arrowleft' || k === 'arrowup' || k === 'pageup')) { e.preventDefault(); const i = Math.max(0, curSec() - 1); secs[i].scrollIntoView({ behavior: 'smooth' }); setTimeout(showNotes, 700); }
    else if (k === 'escape' && present) setPresent(false);
  });

  /* ---------- tables ---------- */
  function table(id, head, rows, cls) { const t = $('#' + id); if (!t) return; t.innerHTML = `<tr>${head.map((h, i) => `<th class="${cls && cls[i] === 'n' ? 'num' : ''}">${h}</th>`).join('')}</tr>` + rows.map((r) => `<tr class="${r.hl ? 'hl' : ''}">${r.cells.map((c, i) => `<td class="${(cls && cls[i] === 'n' ? 'num ' : '') + (cls && cls[i] === 'w' ? 'wrapcell ' : '') + (r.best && r.best[i] ? 'best' : '')}">${c}</td>`).join('')}</tr>`).join(''); }
  table('tbl1', ['Component', 'Specification'], P.table1.map((r) => ({ cells: [`<b>${r[0]}</b>`, r[1]] })), ['', 'w']);
  table('tbl2', ['Property', 'Value'], P.table2.map((r) => ({ cells: r })), ['', 'n']);
  table('tbl3', ['ID', 'Detector', 'DMGT concept', 'Flag rule (default)', 'Time'], P.table3.map((r) => ({ cells: [`<b>${r.id}</b>`, r.name, r.concept, r.rule, r.time] })));
  (function t4() {
    const best = (k, hi) => Math.max(...P.table4.map((r) => r[k])); const pm = (m, s) => `${m.toFixed(3)} <span class="small">± ${s.toFixed(3)}</span>`;
    table('tbl4', ['Detector', 'Precision', 'Recall', 'F1', 'AUROC', 'Flagged', 'ms'], P.table4.map((r) => ({ hl: r.id === 'D4' || r.id === 'U', cells: [r.name, pm(r.P, r.Psd), pm(r.R, r.Rsd), pm(r.F1, r.F1sd), r.AUROC == null ? '–' : r.AUROC.toFixed(3), r.flagged.toFixed(1), r.ms.toFixed(1)], best: [0, r.P === best('P'), r.R === best('R'), r.F1 === best('F1'), r.AUROC === 0.887, 0, 0] })), ['', 'n', 'n', 'n', 'n', 'n', 'n']);
    const tabs = [['P', 'Precision'], ['R', 'Recall'], ['F1', 'F1'], ['AUROC', 'AUROC']]; let cur = 'F1';
    $('#t4tabs').innerHTML = tabs.map(([k, l]) => `<button data-k="${k}" class="${k === cur ? 'on' : ''}">${l}</button>`).join('');
    function draw() { const rows = P.table4.filter((r) => cur !== 'AUROC' || r.AUROC != null); CH.barChart({ el: '#ch-table4', cats: rows.map((r) => r.name), rot: true, series: [{ name: tabs.find((t) => t[0] === cur)[1], color: css('--accent'), v: rows.map((r) => r[cur]) }], yMax: 1, refLine: cur === 'P' ? { v: 0.05, label: 'random baseline 0.05' } : null, h: 330, legend: false }); }
    window.__t4draw = draw; draw(); $$('#t4tabs button').forEach((b) => b.addEventListener('click', () => { cur = b.dataset.k; $$('#t4tabs button').forEach((x) => x.classList.toggle('on', x === b)); draw(); }));
  })();
  function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
  table('tbl5', ['Parameter', 'Default', 'P', 'R', 'F1', 'Best', 'P', 'R', 'F1'], P.table5.map((r) => ({ cells: [r.param, r.def, f3(r.dP), f3(r.dR), f3(r.dF), r.best, f3(r.bP), f3(r.bR), f3(r.bF)] })), ['', '', 'n', 'n', 'n', '', 'n', 'n', 'n']);
  (function t678() {
    const T = P.table6, ids = ['D1', 'D2', 'D3', 'D4', 'D5', 'U', 'V2']; const hd = ['N', 'D1', 'D2', 'D3', 'D4', 'D5', 'Union', 'Vote≥2'];
    table('tbl6', hd, T.N.map((n, i) => ({ cells: [n.toLocaleString()].concat(ids.map((d) => f3(T[d][i]))), best: [0].concat(ids.map((d) => Math.max(...ids.map((e) => T[e][i])) === T[d][i])) })), ['', 'n', 'n', 'n', 'n', 'n', 'n', 'n']);
    const T7 = P.table7; table('tbl7', ['N', 'D1', 'D2', 'D3', 'D4', 'D5', '|E|', 'All five'], T7.N.map((n, i) => ({ cells: [n.toLocaleString(), T7.D1[i], T7.D2[i], T7.D3[i], T7.D4[i], T7.D5[i], T7.edges[i].toLocaleString(), `<b>${T7.all[i]}</b>`] })), ['', 'n', 'n', 'n', 'n', 'n', 'n', 'n']);
    const T8 = P.table8; table('tbl8', ['Share', 'D1', 'D2', 'D3', 'D4', 'D5', 'Union', 'Vote≥2'], T8.share.map((s, i) => ({ cells: [s + ' %'].concat(ids.map((d) => f3(T8[d][i]))), best: [0].concat(ids.map((d) => Math.max(...ids.map((e) => T8[e][i])) === T8[d][i])) })), ['', 'n', 'n', 'n', 'n', 'n', 'n', 'n']);
  })();
  function drawPaperCharts() {
    const T = P.table6, T7 = P.table7, T8 = P.table8, names = { D1: 'D1 z-score', D2: 'D2 imbalance', D3: 'D3 clustering', D4: 'D4 cycles', D5: 'D5 components', U: 'Union', V2: 'Vote ≥ 2' };
    const ser = (T0) => Object.keys(names).map((d) => ({ name: names[d], color: COL[d], y: T0[d], hi: d === 'U' || d === 'D4' }));
    CH.lineChart({ el: '#ch-t6', x: T.N, xIndex: true, xFmt: (v) => v.toLocaleString(), xLabel: 'accounts N', yLabel: 'mean F1', yMax: 1, series: ser(T), h: 320 });
    CH.lineChart({ el: '#ch-t7', x: T7.N, xIndex: true, xFmt: (v) => v.toLocaleString(), xLabel: 'accounts N', yLabel: 'time (ms)', yMax: 350, yFmt: (v) => Math.round(v), series: ['D3', 'D4', 'all'].map((d) => ({ name: d === 'all' ? 'all five detectors' : d === 'D3' ? 'D3 clustering' : 'D4 cycles', color: d === 'all' ? COL.U : COL[d], y: T7[d], hi: d === 'all' })), h: 320 });
    CH.lineChart({ el: '#ch-t8', x: T8.share, xIndex: true, xFmt: (v) => v + '%', xLabel: 'share of anomalous accounts', yLabel: 'mean F1', yMax: 1, series: ser(T8), h: 320 });
    if (window.__t4draw) window.__t4draw();
  }
  drawPaperCharts();

  /* ---------- paired tests (match-up cards) ---------- */
  $('#pairs').innerHTML = P.tests.map((t, i) => { const w = +t.wins.match(/\d+/)[0]; const sq = Array.from({ length: 30 }, (_, k) => `<i style="display:inline-block;width:9px;height:9px;margin:1px;border-radius:2px;background:${k < w ? 'var(--good)' : 'var(--line)'}"></i>`).join(''); return `<div style="margin-top:10px"><div class="small"><b>${t.a}</b>: ${t.diff} · ${t.p}</div><div>${sq}</div><div class="small">better in ${t.wins} (paired t-test; order of squares is arbitrary)</div></div>`; }).join('');

  /* ---------- inconsistencies table ---------- */
  table('tblIncons', ['Detector F1 · N=1000, f=5%', 'Table 4 (30 inst.)', 'Table 5 (20)', 'Table 6 (15)', 'Table 8 (15)'], [['D1', .285, .303, .298, .284], ['D2', .557, .555, .584, .558], ['D4', .619, .613, .611, .611], ['Union', .655, null, .668, .651]].map((r) => ({ cells: [r[0]].concat(r.slice(1).map((x) => (x == null ? '–' : x.toFixed(3)))) })), ['', 'n', 'n', 'n', 'n']);

  /* ---------- skeptics ---------- */
  const SK = [['Isn’t this circular — you planted the fraud the detectors look for?', 'Partly, and the paper says so: D4’s recall on rings and D5’s on clusters is “partly by construction”. The experiments measure behaviour under controlled conditions, not live accuracy. D5 is the clearest case: at s<sub>min</sub> = 6 its precision is 1.000 only because honest components have ≤ 5 accounts and planted clusters 5–8.'],
    ['Real ledgers have loops. Won’t D4 collapse?', 'Probably lose precision — it is limitation (2). Try the what-if above: add legitimate loops and watch D4’s precision fall. Hub exclusion removes loops through exchanges, but not every honest loop.'],
    ['Were the parameters tuned on the test data?', 'Mostly fixed in advance, with one exception: q = 95 was chosen after a small pilot on seeds 1–3, which overlap the evaluation seeds 0–29, because q = 100 was clearly worse. Table 5’s “best” settings are chosen on the same instances, so they are optimistic.'],
    ['Why ignore amounts and timestamps?', 'To isolate what topology alone can do, and to keep every flag explainable. Amounts, timing, address clustering and a repeated-transfer count are all future work; Bitcoin’s UTXO model also makes “account = vertex” a modelling choice.'],
    ['Is “3σ” a straw man?', 'No — it is the textbook rule, and degree’s AUROC of 0.887 shows the signal exists. The lesson is that a <i>threshold</i> derived from μ and σ breaks on heavy-tailed data. Robust or rank-based thresholds are the suggested fix.'],
    ['Union’s best F1 still means about half its flags are wrong.', 'Yes: ≈ 47 false alarms per 1,000 accounts at 5% prevalence. Honest hubs are the “hard negatives”; separating them needs extra information such as amounts, timing, labels or an exchange whitelist.'],
    ['Are the run-times trustworthy?', 'They were measured on a single CPU core of a cloud sandbox and are indicative only. Trends (D3 grows fastest; whole pipeline ≈ 0.3 s at N = 4,000) matter more than absolute values.'],
    ['Are 30 paired instances enough for a t-test?', 'It is a reasonable sample for a controlled simulation; the win-counts (21 of 30, 30 of 30) give a distribution-free view that agrees with the p-values.']];
  $('#skeptics').innerHTML = SK.map((s) => `<details class="d"><summary>${s[0]}</summary><div class="small">${s[1]}</div></details>`).join('');

  /* ---------- refs ---------- */
  $('#refs').innerHTML = P.references.map((r) => `<li>${r}</li>`).join('');

  /* ---------- detector cards + pipeline ---------- */
  const DC = [['D1', 'Degree z-score', 'Has far more links than the average account.', 'z(v) = (deg(v) − μ) / σ > 3', COL.D1], ['D2', 'Imbalance', 'Almost only sends, or almost only receives.', 'β(v) = |deg⁺ − deg⁻| / deg ≥ 0.9 and deg ≥ 10', COL.D2], ['D3', 'Clustering', 'Its neighbours are all linked to each other.', 'C(v) ≥ 0.5 and k(v) ≥ 3', COL.D3], ['D4', 'Short cycles', 'Money returns to the sender in 3 to 5 hops.', 'on a simple directed cycle of length 3–5, after removing degree &gt; 95th percentile', COL.D4], ['D5', 'Small islands', 'Sits in a tiny cut-off group of 5 to 15.', 'weakly connected component with 5 ≤ |C| ≤ 15', COL.D5]];
  $('#detCards').innerHTML = DC.map((d) => `<div class="card arche" style="--c:${d[4]}"><div class="kicker" style="color:${d[4]};margin-bottom:6px"><b style="border-color:${d[4]}">${d[0]}</b></div><h4>${d[1]}</h4><p class="small" style="margin:0 0 6px">${d[2]}</p><p class="small techonly mono" style="margin:0">${d[3]}</p></div>`).join('') + `<div class="card" style="border:1px dashed var(--accent)"><h4>Every flag has a reason</h4><p class="small" style="margin:0">e.g. “sends to 47 accounts and receives from 1” or “lies on a directed 4-cycle among low-degree accounts”. Hover accounts in the <a href="#bench">detective bench</a> to see it live.</p></div>`;
  const PIPE = [['Transactions', '#problem'], ['Directed graph', '#toolkit'], ['Structural features', '#toolkit'], ['Five detectors', '#method'], ['Union / Vote ≥ 2', '#method'], ['Score vs truth', '#measure'], ['Three experiments', '#tuning']];
  $('#pipeline').innerHTML = PIPE.map((p, i) => `<a href="${p[1]}" class="btn" style="text-decoration:none">${i + 1}. ${p[0]}</a>${i < PIPE.length - 1 ? '<span style="color:var(--ink3)">→</span>' : ''}`).join('');

  /* ---------- archetype mini icons (static SVG) ---------- */
  function archeLayout(kind) {
    const nodes = [], edges = []; const C = [50, 50];
    if (kind === 'fanout' || kind === 'fanin') { nodes.push(C); for (let i = 0; i < 9; i++) { const a = (i / 9) * 6.283; nodes.push([50 + Math.cos(a) * 36, 50 + Math.sin(a) * 36]); edges.push(kind === 'fanout' ? [0, i + 1] : [i + 1, 0]); } }
    else if (kind === 'ring') { for (let i = 0; i < 5; i++) { const a = (i / 5) * 6.283 - 1.57; nodes.push([50 + Math.cos(a) * 30, 50 + Math.sin(a) * 30]); } for (let i = 0; i < 5; i++) edges.push([i, (i + 1) % 5]); edges.push([0, 2]); nodes.push([92, 14], [10, 90]); edges.push([1, 5], [6, 3]); }
    else { for (let i = 0; i < 6; i++) { const a = (i / 6) * 6.283; nodes.push([50 + Math.cos(a) * 28, 50 + Math.sin(a) * 28]); } for (let i = 0; i < 6; i++) for (let j = i + 1; j < 6; j++) if ((i + j) % 5 !== 0 || i === 0) edges.push([i, j]); }
    return { nodes, edges };
  }
  const KC = { fanout: 'var(--fanout)', fanin: 'var(--fanin)', ring: 'var(--ring)', cluster: 'var(--cluster)' };
  $$('.mini').forEach((m) => { const k = m.dataset.kind, L = archeLayout(k); const dir = k !== 'cluster';
    m.innerHTML = `<svg viewBox="0 0 100 100" width="100%" height="110"><defs><marker id="ar${k}" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L6 3L0 6z" fill="var(--ink3)"/></marker></defs>${L.edges.map(([a, b]) => `<line x1="${L.nodes[a][0]}" y1="${L.nodes[a][1]}" x2="${L.nodes[b][0]}" y2="${L.nodes[b][1]}" stroke="var(--ink3)" stroke-width="1.2" ${dir ? `marker-end="url(#ar${k})"` : ''} opacity=".8"/>`).join('')}${L.nodes.map((n, i) => `<circle cx="${n[0]}" cy="${n[1]}" r="${(k === 'fanout' || k === 'fanin') && i === 0 ? 8 : 4.6}" fill="${(k === 'fanout' || k === 'fanin') ? (i === 0 ? KC[k] : 'var(--honest)') : (k === 'ring' && i > 4 ? 'var(--honest)' : KC[k])}"/>`).join('')}</svg>`; });

  /* ---------- archetype animated canvas ---------- */
  (function arche() {
    const kinds = [['fanout', 'Fan-out hub'], ['fanin', 'Fan-in hub'], ['ring', 'Cyclic ring'], ['cluster', 'Isolated cluster']]; const text = { fanout: 'One account scatters money to many fresh accounts — it sends to many but receives from almost no one.', fanin: 'Many accounts pour money into one account — it receives from many but sends to almost no one.', ring: 'Money goes round and round the same few accounts to fake trading volume. A directed loop of length 3–6.', cluster: 'A tight group, densely linked inside, with no connection at all to the rest of the network.' };
    let kind = 'fanout'; $('#archeTabs').innerHTML = kinds.map(([k, l]) => `<button data-k="${k}" class="${k === kind ? 'on' : ''}">${l}</button>`).join(''); const cv = $('#archeCanvas'), ctx = cv.getContext('2d'); let parts = [], L = null, t0 = 0;
    function setK(k) { kind = k; $$('#archeTabs button').forEach((b) => b.classList.toggle('on', b.dataset.k === k)); $('#archeText').textContent = text[k]; L = archeLayout(k); if (k === 'cluster') L.edges = L.edges.concat(L.edges.map(([a, b]) => [b, a]).filter((_, i) => i % 3 === 0)); parts = L.edges.map((e, i) => ({ e, t: (i * 0.37) % 1 })); }
    $$('#archeTabs button').forEach((b) => b.addEventListener('click', () => setK(b.dataset.k))); setK('fanout');
    function frame(ts) { if (!document.body.contains(cv)) return; const r = cv.getBoundingClientRect(); if (r.bottom > 0 && r.top < innerHeight) { const dpr = Math.min(2, devicePixelRatio || 1), W = r.width, H = 240; if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; } ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H); const s = Math.min(W, H) / 100, ox = (W - 100 * s) / 2, oy = (H - 100 * s) / 2; const X = (p) => [ox + p[0] * s, oy + p[1] * s];
        const ink = css('--ink3'); ctx.lineWidth = 1.4; ctx.strokeStyle = ink; L.edges.forEach(([a, b]) => { const A = X(L.nodes[a]), B = X(L.nodes[b]); ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(...A); ctx.lineTo(...B); ctx.stroke(); }); ctx.globalAlpha = 1;
        const col = { fanout: css('--fanout'), fanin: css('--fanin'), ring: css('--ring'), cluster: css('--cluster') }[kind];
        parts.forEach((p) => { p.t = (p.t + 0.006) % 1; const A = X(L.nodes[p.e[0]]), B = X(L.nodes[p.e[1]]); const x = A[0] + (B[0] - A[0]) * p.t, y = A[1] + (B[1] - A[1]) * p.t; ctx.beginPath(); ctx.arc(x, y, 3.2, 0, 6.3); ctx.fillStyle = css('--accent'); ctx.fill(); });
        L.nodes.forEach((n, i) => { const p = X(n); const special = (kind === 'fanout' || kind === 'fanin') ? i === 0 : (kind === 'ring' ? i < 5 : true); ctx.beginPath(); ctx.arc(p[0], p[1], (kind === 'fanout' || kind === 'fanin') && i === 0 ? 12 : 7, 0, 6.3); ctx.fillStyle = special ? col : css('--honest'); ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = css('--bg'); ctx.stroke(); }); }
      requestAnimationFrame(frame); }
    requestAnimationFrame(frame);
  })();

  /* ---------- hero live graph ---------- */
  (function hero() {
    const cv = $('#heroCanvas'); const w = UI.getWorld({ N: 260, f: 0.12, seed: 3 }, true); window.__views = window.__views || [];
    let parts = [], rng = GE.makeRng(9); const edges = w.g.edges(); for (let i = 0; i < 90; i++) parts.push({ e: edges[Math.floor(rng() * edges.length)], t: rng(), v: 0.004 + rng() * 0.008 });
    const view = new GV.GraphView(cv, { onDraw: (ctx, vw) => { parts.forEach((p) => { const a = vw.toScreen(vw.pos[p.e[0]]), b = vw.toScreen(vw.pos[p.e[1]]); p.t += p.v; if (p.t > 1) { p.t = 0; p.e = edges[Math.floor(rng() * edges.length)]; } ctx.beginPath(); ctx.arc(a[0] + (b[0] - a[0]) * p.t, a[1] + (b[1] - a[1]) * p.t, 2.2, 0, 6.3); ctx.fillStyle = css('--accent'); ctx.fill(); }); } });
    view.setGraph(w.g, w.pos); view.mode = 'truth'; window.__views.push(view); let last = 0, visible = true;
    new IntersectionObserver((es) => (visible = es[0].isIntersecting)).observe(cv);
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    (function loop(ts) { if (visible && !reduce && ts - last > 33) { view.draw(); last = ts; } requestAnimationFrame(loop); })(0);
    new ResizeObserver(() => { view.fit(); view.draw(); }).observe($('#heroStage'));
  })();

  /* ---------- lazily mount the simulations ---------- */
  const S1 = window.SIMS1, S2 = window.SIMS2, S3 = window.SIMS3;
  const MOUNTS = { 'sim-toolkit': [S1.toolkit], 'sim-world': [S1.world, 'world'], 'sim-heavy': [S1.heavy], 'sim-cycles': [S2.cycles], 'sim-net': [S2.net], 'sim-bench': [S2.bench], 'sim-game': [S3.game], 'sim-repro': [S3.repro], 'sim-tuning': [S3.tuning, 'tuning'], 'sim-scale': [S3.scale], 'sim-crowd': [S3.crowd], 'sim-whatif': [S3.whatif, 'whatif'] };
  const mio = new IntersectionObserver((es) => es.forEach((e) => { if (!e.isIntersecting) return; const id = e.target.id; mio.unobserve(e.target); const [fn, lazy] = MOUNTS[id]; try { fn(e.target); if (lazy && window.__lazy && window.__lazy[lazy]) window.__lazy[lazy](); } catch (err) { console.error('sim ' + id, err); e.target.innerHTML = '<div class="note warn">This simulation failed to start: ' + err.message + '</div>'; } }), { rootMargin: '500px 0px' });
  Object.keys(MOUNTS).forEach((id) => { const el = document.getElementById(id); if (el) { el.style.minHeight = '120px'; mio.observe(el); } });
  window.addEventListener('themechange', () => setTimeout(() => window.dispatchEvent(new Event('resize')), 20));
})();
