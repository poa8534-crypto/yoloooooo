#!/usr/bin/env node
/* Writes the synthetic transaction-graph datasets used by the site as CSV/JSON.
 *
 *   node tools/generate_dataset.js            -> data/synthetic/  (compact set)
 *   node tools/generate_dataset.js --full     -> also the paper's full Exp. 2 / Exp. 3 instance sets (15 each)
 *
 * Every instance is produced by js/engine.js (a JavaScript port of the paper's generator, Table 1) from an
 * integer seed, so the output is fully reproducible. Ground truth is exact because it is planted.
 */
const fs = require('fs'), path = require('path');
const GE = require(path.join(__dirname, '../js/engine.js'));
const OUT = path.join(__dirname, '../data/synthetic');
const FULL = process.argv.includes('--full');
const TYPE = ['benign', 'fan_out_hub', 'fan_in_hub', 'cyclic_ring', 'isolated_cluster'];

function describe(g) {
  const st = GE.degreeStats(g), { comps, comp } = GE.weakComponents(g), sc = GE.scc(g);
  let rec = 0; for (const [u, v] of g.edges()) if (g.hasEdge(v, u)) rec++;
  const cnt = [0, 0, 0, 0, 0]; g.label.forEach((l) => cnt[l]++);
  return { comps, comp, stats: {
    accounts: g.n, transactions: g.m, suspicious: g.n - cnt[0], fan_out_hub: cnt[1], fan_in_hub: cnt[2], cyclic_ring: cnt[3], isolated_cluster: cnt[4],
    mean_degree: +st.mu.toFixed(3), median_degree: st.median, max_degree: st.max, degree_sd: +st.sigma.toFixed(3),
    density: +(g.m / (g.n * (g.n - 1))).toFixed(6), reciprocity: +(rec / Math.max(1, g.m)).toFixed(4),
    weak_components: comps.length, largest_weak_component: Math.max(...comps.map((c) => c.length)), strong_components: sc.length, largest_strong_component: Math.max(...sc.map((c) => c.length)) } };
}
function groups(g) { // group id for planted structures: H# hubs, R# rings, C# clusters
  const gid = new Array(g.n).fill('');
  (g.meta.hubs || []).forEach((v, i) => { gid[v] = 'H' + (i + 1); });
  (g.meta.rings || []).forEach((m, i) => m.forEach((v) => { gid[v] = 'R' + (i + 1); }));
  (g.meta.clusters || []).forEach((m, i) => m.forEach((v) => { gid[v] = 'C' + (i + 1); }));
  return gid;
}
function nodeRows(g, d, inst) {
  const gid = groups(g), rows = [];
  for (let v = 0; v < g.n; v++) rows.push([inst, v, TYPE[g.label[v]], g.label[v] ? 1 : 0, gid[v], g.out[v].size, g.inn[v].size, g.deg(v), d.comps[d.comp[v]].length].join(','));
  return rows;
}
function edgeRows(g, inst) { return g.edges().map(([u, v]) => [inst, u, v].join(',')); }
const NODE_HDR = 'instance,account_id,type,is_suspicious,group_id,out_degree,in_degree,degree,weak_component_size';
const EDGE_HDR = 'instance,source,target';

function writeSingle(name, opts, note) {
  const g = GE.generate(opts), d = describe(g), dir = path.join(OUT, name); fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'nodes.csv'), [NODE_HDR].concat(nodeRows(g, d, name)).join('\n') + '\n');
  fs.writeFileSync(path.join(dir, 'edges.csv'), [EDGE_HDR].concat(edgeRows(g, name)).join('\n') + '\n');
  fs.writeFileSync(path.join(dir, 'meta.json'), JSON.stringify({ instance: name, note, generator: 'js/engine.js (port of paper Table 1)', parameters: { N: opts.N, anomaly_share: opts.f, seed: opts.seed }, statistics: d.stats }, null, 2) + '\n');
  console.log('wrote', name, d.stats.accounts, 'accounts', d.stats.transactions, 'edges');
}
function writeCombined(name, specs, note) { // many instances in one nodes/edges file with an `instance` column
  const dir = path.join(OUT, name); fs.mkdirSync(dir, { recursive: true }); const nodes = [NODE_HDR], edges = [EDGE_HDR], idx = [];
  for (const s of specs) { const g = GE.generate(s.opts), d = describe(g); nodes.push(...nodeRows(g, d, s.id)); edges.push(...edgeRows(g, s.id)); idx.push(Object.assign({ instance: s.id, N: s.opts.N, anomaly_share: s.opts.f, seed: s.opts.seed }, d.stats)); }
  fs.writeFileSync(path.join(dir, 'nodes.csv'), nodes.join('\n') + '\n'); fs.writeFileSync(path.join(dir, 'edges.csv'), edges.join('\n') + '\n');
  const cols = Object.keys(idx[0]); fs.writeFileSync(path.join(dir, 'instances.csv'), [cols.join(',')].concat(idx.map((r) => cols.map((c) => r[c]).join(','))).join('\n') + '\n');
  fs.writeFileSync(path.join(dir, 'meta.json'), JSON.stringify({ note, generator: 'js/engine.js (port of paper Table 1)', instances: idx.length }, null, 2) + '\n');
  console.log('wrote', name, idx.length, 'instances');
}

fs.mkdirSync(OUT, { recursive: true });
writeSingle('representative_N1000_f5_seed0', { N: 1000, f: 0.05, seed: 0 }, 'Counterpart of the paper\'s representative instance (Table 2). Same recipe, different random numbers, so statistics are close to but not identical with Table 2.');
writeSingle('illustration_N220_f15_seed4', { N: 220, f: 0.15, seed: 4 }, 'Small instance used for the "you vs the algorithm" game; same setting as the paper\'s Fig. 3 (N = 220, 15% anomalous).');
writeCombined('exp0_baseline_30_instances', Array.from({ length: 30 }, (_, s) => ({ id: 'seed' + String(s).padStart(2, '0'), opts: { N: 1000, f: 0.05, seed: s } })), 'Experiment 0: N = 1,000, f = 5%, seeds 0-29.');
const Ns = [300, 600, 1000, 2000, 4000], Fs = [0.01, 0.025, 0.05, 0.10, 0.15], K = FULL ? 15 : 1;
writeCombined('exp2_sizes', [].concat(...Ns.map((N) => Array.from({ length: K }, (_, s) => ({ id: `N${N}_seed${String(s).padStart(2, '0')}`, opts: { N, f: 0.05, seed: s } })))), `Experiment 2: N in {300,600,1000,2000,4000}, f = 5%, ${K} instance(s) per size${FULL ? ' (paper protocol: 15)' : ' (run with --full for 15)'}.`);
writeCombined('exp3_shares', [].concat(...Fs.map((f) => Array.from({ length: K }, (_, s) => ({ id: `f${f * 100}_seed${String(s).padStart(2, '0')}`, opts: { N: 1000, f, seed: s } })))), `Experiment 3: f in {1,2.5,5,10,15}%, N = 1,000, ${K} instance(s) per share${FULL ? ' (paper protocol: 15)' : ' (run with --full for 15)'}.`);
