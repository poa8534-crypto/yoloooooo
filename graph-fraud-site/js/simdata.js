/* =====================================================================
   simdata.js -- "Synthetic data" explorer: generate an instance, compare it with the paper's Table 2,
   browse nodes/edges, copy or save CSV. Column layout is identical to the files in data/synthetic/
   (see tools/generate_dataset.js).
   ===================================================================== */
(function (root) {
  'use strict';
  const GE = root.GE, UI = root.UI, { $, slider, liveBadge, later } = UI;
  const TYPE = ['benign', 'fan_out_hub', 'fan_in_hub', 'cyclic_ring', 'isolated_cluster'];
  const TYPE_VAR = ['--honest', '--fanout', '--fanin', '--ring', '--cluster'];
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');

  function build(g, inst) {
    const { comps, comp } = GE.weakComponents(g), gid = new Array(g.n).fill('');
    (g.meta.hubs || []).forEach((v, i) => (gid[v] = 'H' + (i + 1))); (g.meta.rings || []).forEach((m, i) => m.forEach((v) => (gid[v] = 'R' + (i + 1)))); (g.meta.clusters || []).forEach((m, i) => m.forEach((v) => (gid[v] = 'C' + (i + 1))));
    const nodes = []; for (let v = 0; v < g.n; v++) nodes.push([inst, v, TYPE[g.label[v]], g.label[v] ? 1 : 0, gid[v], g.out[v].size, g.inn[v].size, g.deg(v), comps[comp[v]].length]);
    return { nodes, edges: g.edges().map(([u, v]) => [inst, u, v]) };
  }
  const NODE_HDR = ['instance', 'account_id', 'type', 'is_suspicious', 'group_id', 'out_degree', 'in_degree', 'degree', 'weak_component_size'], EDGE_HDR = ['instance', 'source', 'target'];
  const toCSV = (hdr, rows) => hdr.join(',') + '\n' + rows.map((r) => r.join(',')).join('\n') + '\n';

  function stats(g) {
    const st = GE.degreeStats(g), { comps } = GE.weakComponents(g), sc = GE.scc(g); let rec = 0; for (const [u, v] of g.edges()) if (g.hasEdge(v, u)) rec++;
    let cc = 0; for (let v = 0; v < g.n; v++) cc += GE.clustering(g, v).C;
    return [['Accounts |V|', g.n.toLocaleString(), '1,000'], ['Transactions |E|', g.m.toLocaleString(), '2,750'], ['Mean / median degree', `${st.mu.toFixed(2)} / ${st.median}`, '5.50 / 3'], ['Max degree / σ', `${st.max} / ${st.sigma.toFixed(2)}`, '471 / 18.26'],
      ['Density', (g.m / (g.n * (g.n - 1))).toFixed(5), '0.00275'], ['Reciprocity', (rec / Math.max(1, g.m)).toFixed(3), '0.020'], ['Average clustering', (cc / g.n).toFixed(3), '0.118'], ['Weak components', `${comps.length} (giant ${Math.max(...comps.map((c) => c.length))})`, '20 (giant 932)'], ['Strong components', `${sc.length} (largest ${Math.max(...sc.map((c) => c.length))})`, '933 (largest 44)']];
  }

  function simData(mount) {
    mount.innerHTML = `<div class="sim"><header><h3>Generate, inspect and export a synthetic network</h3><span class="tag">Data · same columns as the shipped CSV files</span></header>
      <div class="grid2" style="align-items:start"><div class="controls">${liveBadge('this is the exact generator behind every simulation')}
        ${slider('dN', 'Accounts N', 300, 4000, 100, 1000, (v) => (+v).toLocaleString())}${slider('dF', 'Fraud share f', 0.01, 0.15, 0.005, 0.05, (v) => (v * 100).toFixed(1) + '%')}${slider('dS', 'Random seed', 0, 99, 1, 0)}
        <div class="btns"><button class="btn primary" id="dGen">Generate</button><button class="btn" id="dCopyN">Copy nodes.csv</button><button class="btn" id="dCopyE">Copy edges.csv</button><button class="btn" id="dDlN">⬇ nodes.csv</button><button class="btn" id="dDlE">⬇ edges.csv</button><a class="btn" href="data/synthetic/exp0_baseline_30_instances/nodes.csv" download style="text-decoration:none">⬇ 30-network nodes.csv</a><a class="btn" href="data/synthetic/exp0_baseline_30_instances/edges.csv" download style="text-decoration:none">⬇ 30-network edges.csv</a></div>
        <div class="small" id="dMsg">Everything is generated here from the seed, so the files are identical to the ones in <code>data/synthetic/</code>.</div><textarea id="dBox" class="copybox" readonly hidden aria-label="CSV text to copy"></textarea></div>
      <div><div class="small" style="font-weight:700">Fraud budget, split by archetype</div><div class="budgetbar" id="dBudget"></div><div class="legend" id="dLeg"></div><div class="metrics" id="dMetrics" style="margin-top:10px"></div></div></div>
      <div class="grid2" style="margin-top:14px"><div><h4>This instance <span class="pill live">live</span> vs the paper's example <span class="pill paper">Table 2</span></h4><div class="tablewrap"><table class="t" id="dStats"></table></div><p class="small">The paper's column is its seed-0 instance (N = 1,000, 5%). Same recipe, different random numbers: expect the same shape, not the same digits (the biggest hub and σ differ).</p></div>
      <div><h4>Preview</h4><div class="seg" id="dFilter" style="margin:0 0 8px"><button data-t="sus">suspicious only</button><button data-t="-1">all accounts</button>${TYPE.map((t, i) => `<button data-t="${i}">${t}</button>`).join('')}</div><p class="small" style="margin:0">Pick a type to see only those accounts and the transfers that touch them. The default view shows every suspicious account.</p></div></div>
      <div class="grid2" style="margin-top:8px"><div><h4>nodes.csv <span class="small" id="dNcap"></span></h4><div class="tablewrap" style="max-height:380px;overflow:auto"><table class="t" id="dNodes"></table></div><div class="btns" style="margin-top:6px"><button class="btn" id="dMoreN">Show 100 more</button></div></div>
      <div><h4>edges.csv <span class="small" id="dEcap"></span></h4><div class="tablewrap" style="max-height:380px;overflow:auto"><table class="t" id="dEdges"></table></div><div class="btns" style="margin-top:6px"><button class="btn" id="dMoreE">Show 100 more</button></div></div></div></div>`;
    let cur = null, filt = 'sus', inst = '', nShow = 40, eShow = 40;
    $('#dLeg', mount).innerHTML = TYPE.map((t, i) => `<span><i style="background:var(${TYPE_VAR[i]})"></i>${t}</span>`).join('');
    mount.querySelectorAll('#dFilter button').forEach((b) => b.classList.toggle('on', false));
    ['dN', 'dF', 'dS'].forEach((id) => { const el = $('#' + id, mount); el.addEventListener('input', () => { const v = +el.value; $('#' + id + 'V', mount).textContent = id === 'dN' ? v.toLocaleString() : id === 'dF' ? (v * 100).toFixed(1) + '%' : v; }); el.addEventListener('change', () => gen()); });
    const gen = UI.serial(async () => {
      await later(() => 0); const N = +$('#dN', mount).value, f = +$('#dF', mount).value, seed = +$('#dS', mount).value; inst = `N${N}_f${+(f * 100).toFixed(1)}_seed${seed}`;
      const g = UI.getWorld({ N, f, seed }, false).g; cur = { g, ...build(g, inst) }; nShow = eShow = 40; const cnt = [0, 0, 0, 0, 0]; g.label.forEach((l) => cnt[l]++); const A = g.n - cnt[0];
      $('#dMetrics', mount).innerHTML = [['Accounts', g.n.toLocaleString()], ['Transfers', g.m.toLocaleString()], ['Suspicious', A + ' (' + (100 * A / g.n).toFixed(1) + '%)'], ['Hubs out / in', cnt[1] + ' / ' + cnt[2]], ['Ring accounts', cnt[3]], ['Cluster accounts', cnt[4]]].map((m) => `<div class="metric"><span>${m[0]}</span><b style="font-size:1.15rem">${m[1]}</b></div>`).join('');
      $('#dBudget', mount).innerHTML = [1, 2, 3, 4].map((t) => `<i title="${TYPE[t]}: ${cnt[t]}" style="width:${A ? (100 * cnt[t] / A).toFixed(1) : 0}%;background:var(${TYPE_VAR[t]})"></i>`).join('');
      $('#dStats', mount).innerHTML = '<tr><th>Property</th><th class="num"><span class="pill live">live</span></th><th class="num"><span class="pill paper">paper</span></th></tr>' + stats(g).map((r) => `<tr><td>${r[0]}</td><td class="num">${r[1]}</td><td class="num" style="color:var(--ink3)">${r[2]}</td></tr>`).join('');
      show();
    }, UI.errBox(mount));
    const match = (t) => (filt === 'sus' ? t !== 0 : filt < 0 ? true : t === filt);
    function show() {
      if (!cur) return; const rows = cur.nodes.filter((r) => match(TYPE.indexOf(r[2]))), head = rows.slice(0, nShow);
      mount.querySelectorAll('#dFilter button').forEach((b) => b.classList.toggle('on', b.dataset.t === String(filt)));
      $('#dNodes', mount).innerHTML = '<tr>' + NODE_HDR.slice(1).map((h) => `<th>${h}</th>`).join('') + '</tr>' + head.map((r) => `<tr>${r.slice(1).map((c, j) => (j === 1 ? `<td><i style="display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;background:var(${TYPE_VAR[TYPE.indexOf(c)]})"></i>${esc(c)}</td>` : `<td class="${j >= 4 ? 'num' : ''}">${esc(c)}</td>`)).join('')}</tr>`).join('');
      $('#dNcap', mount).textContent = `· showing ${head.length} of ${rows.length} rows${filt === 'sus' ? ' (suspicious only)' : ''}`; $('#dMoreN', mount).hidden = head.length >= rows.length;
      let er = cur.edges; if (filt !== -1) { const lab = cur.g.label, ok = (l) => (filt === 'sus' ? l !== 0 : l === filt); er = er.filter((r) => ok(lab[r[1]]) || ok(lab[r[2]])); } const eh = er.slice(0, eShow);
      $('#dEdges', mount).innerHTML = '<tr><th>source</th><th>target</th><th>source type</th><th>target type</th></tr>' + eh.map((r) => `<tr><td class="num">${r[1]}</td><td class="num">${r[2]}</td><td>${TYPE[cur.g.label[r[1]]]}</td><td>${TYPE[cur.g.label[r[2]]]}</td></tr>`).join('');
      $('#dEcap', mount).textContent = `· showing ${eh.length} of ${er.length} rows${filt === -1 ? '' : ' touching the chosen type'}`; $('#dMoreE', mount).hidden = eh.length >= er.length;
    }
    mount.querySelectorAll('#dFilter button').forEach((b) => b.addEventListener('click', () => { filt = b.dataset.t === 'sus' ? 'sus' : +b.dataset.t; nShow = eShow = 40; show(); }));
    $('#dMoreN', mount).addEventListener('click', () => { nShow += 100; show(); }); $('#dMoreE', mount).addEventListener('click', () => { eShow += 100; show(); });
    const msg = (t) => ($('#dMsg', mount).textContent = t);
    function copy(txt, label) { const box = $('#dBox', mount); box.value = txt.length > 400000 ? txt.slice(0, 400000) : txt;
      const fallback = () => { box.hidden = false; box.focus(); box.select(); msg(`Clipboard blocked here. ${label} is selected below: press Ctrl/⌘ + C.`); };
      try { navigator.clipboard.writeText(txt).then(() => { box.hidden = true; msg(`Copied ${label} to the clipboard.`); }, fallback); } catch (e) { fallback(); } }
    async function save(name, txt) {
      try { const d = root.claude && root.claude.use ? await root.claude.use('downloads') : null;
        if (d) { await d.save({ filename: name, data: txt }); msg(`Saved ${name}.`); return; } } catch (e) { if (e && e.code === 'declined') { msg('Save cancelled.'); return; } }
      try { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([txt], { type: 'text/csv' })); a.download = name; document.body.appendChild(a); a.click(); a.parentNode.removeChild(a); setTimeout(() => URL.revokeObjectURL(a.href), 10000); msg(`Requested ${name}. If nothing happens, use Copy instead.`); } catch (e) { msg('Saving is blocked here. Use Copy, or the ready-made files in data/synthetic/.'); }
    }
    $('#dGen', mount).addEventListener('click', gen);
    $('#dCopyN', mount).addEventListener('click', () => cur && copy(toCSV(NODE_HDR, cur.nodes), 'nodes.csv')); $('#dCopyE', mount).addEventListener('click', () => cur && copy(toCSV(EDGE_HDR, cur.edges), 'edges.csv'));
    $('#dDlN', mount).addEventListener('click', () => cur && save(`nodes_${inst}.csv`, toCSV(NODE_HDR, cur.nodes))); $('#dDlE', mount).addEventListener('click', () => cur && save(`edges_${inst}.csv`, toCSV(EDGE_HDR, cur.edges)));
    gen();
  }
  root.SIMDATA = simData;
})(window);
