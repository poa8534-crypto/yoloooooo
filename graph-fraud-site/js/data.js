/* =====================================================================
   data.js -- every number below is copied from the paper (Tables 1-9 and
   the in-text results). Nothing here is simulated.
   ===================================================================== */
window.PAPER = {
  title: 'Graph-Property-Based Detection of Suspicious Accounts in Blockchain Transaction Networks: Degree, Subgraph, Cycle and Connectivity Analysis',
  detectorNames: { D1: 'Degree z-score', D2: 'Degree imbalance', D3: 'Clustering coeff.', D4: 'Short cycles', D5: 'Small components', U: 'Union (≥ 1 vote)', V2: 'Vote (≥ 2)' },

  /* Table 4 -- Experiment 0, N=1000, f=5%, 30 instances (mean ± sd) */
  table4: [
    { id: 'D1', name: 'Degree z-score',    P: .643, Psd: .086, R: .187, Rsd: .064, F1: .285, F1sd: .082, AUROC: .887, flagged: 14.4, ms: 0.7 },
    { id: 'D2', name: 'Degree imbalance',  P: .696, Psd: .075, R: .466, Rsd: .048, F1: .557, F1sd: .051, AUROC: .751, flagged: 33.7, ms: 0.7 },
    { id: 'D3', name: 'Clustering coeff.', P: .347, Psd: .076, R: .195, Rsd: .034, F1: .246, F1sd: .038, AUROC: .708, flagged: 29.3, ms: 42.1 },
    { id: 'D4', name: 'Short cycles',      P: .915, Psd: .086, R: .470, Rsd: .041, F1: .619, F1sd: .045, AUROC: null, flagged: 25.9, ms: 7.0 },
    { id: 'D5', name: 'Small components',  P: .453, Psd: .139, R: .192, Rsd: .035, F1: .266, F1sd: .052, AUROC: null, flagged: 22.4, ms: 0.7 },
    { id: 'U',  name: 'Union (≥ 1 vote)',  P: .505, Psd: .062, R: .938, Rsd: .027, F1: .655, F1sd: .057, AUROC: null, flagged: 94.0, ms: 51.2 },
    { id: 'V2', name: 'Vote (≥ 2)',        P: .867, Psd: .080, R: .391, Rsd: .079, F1: .534, F1sd: .080, AUROC: null, flagged: 22.7, ms: 51.2 }
  ],
  randomBaselinePrecision: 0.05,

  /* Fig. 4 / finding (i): recall of each rule on each anomaly type (values stated in the text) */
  recallByType: {
    // [fan-out, fan-in, ring, cluster]  ; null = not stated numerically in the text
    D2: [0.89, 0.89, 0, 0],
    D4: [0, 0, 0.98, 1.00],
    D3: [0, 0, null, 0.97],   // "almost no rings"
    D5: [0, 0, null, 1.00]
  },

  /* Table 2 -- representative instance (seed 0) */
  table2: [
    ['Accounts |V|', '1,000'], ['Mean / median degree', '5.50 / 3'], ['Transactions |E|', '2,750'], ['Maximum degree / σ', '471 / 18.26'],
    ['Planted anomalies', '50 (5 %)'], ['Density', '0.00275'], ['fan-out / fan-in', '12 / 12'], ['Reciprocity', '0.020'],
    ['ring / cluster accounts', '15 / 11'], ['Average clustering coefficient', '0.118'], ['Weakly connected components', '20 (giant: 932)'], ['Strongly connected components', '933 (largest: 44)']
  ],

  /* Table 5 -- default vs best setting (20 instances) */
  table5: [
    { param: 'D1  threshold τ',       def: '3',   dP: .661, dR: .200, dF: .303, best: '1',   bP: .671, bR: .523, bF: .586 },
    { param: 'D2  threshold β*',      def: '0.9', dP: .699, dR: .463, dF: .555, best: '0.9', bP: .699, bR: .463, bF: .555 },
    { param: 'D3  threshold c*',      def: '0.5', dP: .328, dR: .192, dF: .239, best: '0.6', bP: .431, bR: .184, bF: .256 },
    { param: 'D4  max length L',      def: '5',   dP: .900, dR: .467, dF: .613, best: '6',   bP: .901, bR: .476, bF: .621 },
    { param: 'D4  hub percentile q',  def: '95',  dP: .900, dR: .467, dF: .613, best: '95',  bP: .900, bR: .467, bF: .613 },
    { param: 'D5  min size s_min',    def: '5',   dP: .452, dR: .193, dF: .266, best: '5',   bP: .452, bR: .193, bF: .266 }
  ],

  /* Table 6 -- mean F1 vs N (5 % anomalous, 15 instances each) */
  table6: {
    N: [300, 600, 1000, 2000, 4000],
    edges: [1003, 1767, 2947, 5816, 11720],
    D1: [.545, .395, .298, .083, .000],
    D2: [.755, .598, .584, .545, .541],
    D3: [.029, .225, .245, .295, .281],
    D4: [.370, .611, .611, .614, .625],
    D5: [.000, .238, .240, .265, .268],
    U:  [.619, .643, .668, .670, .681],
    V2: [.596, .616, .543, .411, .336]
  },
  /* Table 7 -- mean run time in ms */
  table7: {
    N: [300, 600, 1000, 2000, 4000],
    D1: [0.3, 0.5, 0.8, 1.5, 3.0], D2: [0.2, 0.5, 0.8, 1.6, 3.0], D3: [15.2, 28.5, 48.0, 121.5, 281.8],
    D4: [3.2, 5.1, 8.0, 15.5, 29.9], D5: [0.2, 0.5, 0.8, 1.8, 4.1], edges: [1003, 1767, 2947, 5816, 11720],
    all: [19.1, 35.0, 58.4, 142.0, 321.8]
  },
  /* In-text: the 3-sigma cut-off mu+3*sigma versus graph size, and largest benign degree */
  cutoffByN: { N: [300, 1000, 4000], cutoff: [45, 56, 85], maxBenign: [126, 365, 1236], maxPlantedHub: 70 },

  /* Table 8 -- mean F1 vs anomaly share (N=1000, 15 instances each) */
  table8: {
    share: [1, 2.5, 5, 10, 15],
    D1: [.298, .275, .284, .214, .131],
    D2: [.413, .457, .558, .612, .629],
    D3: [.000, .164, .270, .335, .349],
    D4: [.371, .586, .611, .607, .621],
    D5: [.000, .231, .253, .285, .289],
    U:  [.217, .427, .651, .823, .875],
    V2: [.291, .495, .533, .494, .454]
  },
  /* In-text: Union precision 0.123 at 1% -> 0.812 at 15%; recall stays 0.94-0.95;
     benign false alarms ~70/instance at 1% and ~34 at 15%; true positives ~9.5 -> ~143 (see Sec 3.4) */
  unionByShare: { share: [1, 15], precision: [0.123, 0.812], fpPerInstance: [70, 34], tp: [9.5, 143] },

  /* Experiment-1 values stated in the text (Fig. 5 is shown as the original image) */
  sens: {
    D1tau: [{ tau: 1, F1: .586 }, { tau: 3, F1: .303 }, { tau: 5, F1: 0 }],
    D4L:   [{ L: 3, R: .323 }, { L: 5, R: .467 }, { L: 6, R: .476 }],
    D4q:   [{ q: 80, R: .153 }, { q: 95, P: .900, R: .467 }, { q: 100, P: .285 }],
    D5smin:[{ smin: 5, P: .452, R: .193 }, { smin: 6, P: 1.0, R: .143 }]
  },

  /* paired tests, Sec 3.1 (ii)(iii) */
  tests: [
    { a: 'D4 vs D2', diff: '+0.063 F1', p: 'p < 0.001', wins: '21 of 30 instances' },
    { a: 'D2 vs D1', diff: '+0.272 F1', p: 'p < 10⁻¹⁴', wins: '30 of 30 instances' },
    { a: 'Union vs D4', diff: '+0.036 F1', p: 'p = 0.010', wins: '21 of 30 instances' }
  ],

  /* Table 3 -- detector summary */
  table3: [
    { id: 'D1', name: 'Degree z-score', concept: 'Vertex degree, handshaking lemma', rule: 'z(v) > 3', time: 'O(|V| + |E|)' },
    { id: 'D2', name: 'Degree imbalance', concept: 'In-degree / out-degree', rule: 'deg ≥ 10 and β ≥ 0.9', time: 'O(|V|)' },
    { id: 'D3', name: 'Clustering coefficient', concept: 'Subgraphs, triangles', rule: 'k ≥ 3 and C ≥ 0.5', time: 'O(Σ k(v)²)' },
    { id: 'D4', name: 'Short cycles', concept: 'Paths, cycles, induced subgraph', rule: 'cycle length 3–5 after removing degree > 95th percentile', time: 'O(|V| Δq^(L−1)) worst case' },
    { id: 'D5', name: 'Small components', concept: 'Connectivity', rule: '5 ≤ |C| ≤ 15', time: 'O(|V| + |E|)' },
    { id: 'U',  name: 'Union', concept: 'Combination', rule: 'votes ≥ 1', time: 'sum of D1–D5' },
    { id: 'V2', name: 'Vote ≥ 2', concept: 'Combination', rule: 'votes ≥ 2', time: 'sum of D1–D5' }
  ],

  /* Table 1 -- generator */
  table1: [
    ['Benign backbone', 'Directed scale-free graph of Bollobás et al. (NetworkX): α = 0.41, β = 0.54, γ = 0.05, δin = 0.2, δout = 0; self-loops removed, parallel edges collapsed.'],
    ['Benign small components', 'About 6 % of benign accounts form isolated components of 2–5 accounts (size probabilities 0.35, 0.30, 0.20, 0.15): random tree plus extra edges with probability 0.12.'],
    ['T1 Fan-out hub', 'One account sends to d distinct benign accounts, d uniform on {25 … 70}; 0–2 incoming edges. 25 % of the anomaly budget.'],
    ['T2 Fan-in hub', 'Mirror image of T1: d incoming edges, 0–2 outgoing edges. 25 % of the budget.'],
    ['T3 Cyclic ring', 'Size 3–6; directed cycle, reverse edge with probability 0.4, chord between non-consecutive members with probability 0.3; each member linked with probability 0.7 to 1–2 random benign accounts. 30 % of the budget.'],
    ['T4 Isolated dense cluster', 'Size 5–8; a path guarantees connectivity, each pair linked with probability 0.8, linked pair made reciprocal with probability 0.4; no edges to the outside. Remaining 20 % of the budget.'],
    ['Anomaly budget', 'A = round(fN) accounts, default f = 5 %. Leftover budget that cannot form a ring (< 3) or a cluster (< 5) is assigned to fan-out hubs.']
  ],

  references: [
    '[1] S. Nakamoto, “Bitcoin: A peer-to-peer electronic cash system,” 2008.',
    '[2] F. Reid and M. Harrigan, “An analysis of anonymity in the Bitcoin system,” in Security and Privacy in Social Networks, Springer, 2013, pp. 197–223.',
    '[3] M. Möser, R. Böhme, and D. Breuker, “An inquiry into money laundering tools in the Bitcoin ecosystem,” APWG eCrime Researchers Summit, 2013.',
    '[4] F. Victor and A. M. Weintraud, “Detecting and quantifying wash trading on decentralized cryptocurrency exchanges,” WWW ’21, 2021.',
    '[5] V. Chandola, A. Banerjee, and V. Kumar, “Anomaly detection: A survey,” ACM Comput. Surv., vol. 41, no. 3, 2009.',
    '[6] L. Akoglu, H. Tong, and D. Koutra, “Graph based anomaly detection and description: A survey,” Data Min. Knowl. Discov., vol. 29, no. 3, pp. 626–688, 2015.',
    '[7] M. Weber et al., “Anti-money laundering in Bitcoin: Experimenting with graph convolutional networks for financial forensics,” arXiv:1908.02591, 2019.',
    '[8] D. B. West, Introduction to Graph Theory, 2nd ed., Prentice Hall, 2001.',
    '[9] R. Diestel, Graph Theory, 5th ed., Springer, 2017.',
    '[10] K. H. Rosen, Discrete Mathematics and Its Applications, 8th ed., McGraw-Hill, 2019.',
    '[11] D. J. Watts and S. H. Strogatz, “Collective dynamics of ‘small-world’ networks,” Nature, vol. 393, pp. 440–442, 1998.',
    '[12] A.-L. Barabási and R. Albert, “Emergence of scaling in random networks,” Science, vol. 286, pp. 509–512, 1999.',
    '[13] B. Bollobás, C. Borgs, J. Chayes, and O. Riordan, “Directed scale-free graphs,” SODA 2003, pp. 132–139.',
    '[14] A. A. Hagberg, D. A. Schult, and P. J. Swart, “Exploring network structure, dynamics, and function using NetworkX,” SciPy 2008, pp. 11–15.',
    '[15] C. R. Harris et al., “Array programming with NumPy,” Nature, vol. 585, pp. 357–362, 2020.',
    '[16] W. McKinney, “Data structures for statistical computing in Python,” 9th Python in Science Conf., 2010.',
    '[17] F. Pedregosa et al., “Scikit-learn: Machine learning in Python,” JMLR, vol. 12, pp. 2825–2830, 2011.',
    '[18] J. D. Hunter, “Matplotlib: A 2D graphics environment,” Comput. Sci. Eng., vol. 9, no. 3, pp. 90–95, 2007.',
    '[19] P. Virtanen et al., “SciPy 1.0: Fundamental algorithms for scientific computing in Python,” Nat. Methods, vol. 17, pp. 261–272, 2020.',
    '[20] J. Leskovec and A. Krevl, “SNAP Datasets: Stanford large network dataset collection,” 2014.',
    '[21] S. Kumar, F. Spezzano, V. S. Subrahmanian, and C. Faloutsos, “Edge weight prediction in weighted signed networks,” ICDM, 2016.'
  ]
};
