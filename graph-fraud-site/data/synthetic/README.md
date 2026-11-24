# Synthetic transaction graphs

Every file here is **synthetic**. Accounts are vertices, transfers are directed edges, and the
suspicious accounts are **planted by the generator**, so ground truth is exact. Nothing comes from a
real blockchain. The generator is `js/engine.js`, a JavaScript port of the paper's generator
(Table 1: Bollobás directed scale-free backbone, benign small components, four planted archetypes).
It uses its own random number generator, so the data are statistically close to, not identical with,
the instances in the paper.

Regenerate (deterministic, seeded): `node tools/generate_dataset.js` (add `--full` for 15 instances per size/share).

## Instances

| Folder | What | Paper |
|---|---|---|
| `representative_N1000_f5_seed0/` | N = 1,000, 5 % suspicious, seed 0 | Table 2 |
| `illustration_N220_f15_seed4/` | N = 220, 15 % suspicious (used by the "you vs the algorithm" game) | Fig. 3 |
| `exp0_baseline_30_instances/` | N = 1,000, 5 %, seeds 0–29 | Experiment 0 / Table 4 |
| `exp2_sizes/` | N ∈ {300, 600, 1000, 2000, 4000}, 5 % (1 instance each; `--full` gives 15) | Experiment 2 / Tables 6–7 |
| `exp3_shares/` | N = 1,000, f ∈ {1, 2.5, 5, 10, 15} % (1 instance each; `--full` gives 15) | Experiment 3 / Table 8 |

Each folder has `nodes.csv` and `edges.csv` (plus `meta.json`, or `instances.csv` for combined sets).

## `nodes.csv`

| Column | Meaning |
|---|---|
| `instance` | instance id (several instances can share one file) |
| `account_id` | integer vertex id, unique within an instance |
| `type` | ground truth: `benign`, `fan_out_hub` (T1), `fan_in_hub` (T2), `cyclic_ring` (T3), `isolated_cluster` (T4) |
| `is_suspicious` | 1 for T1–T4, 0 for benign |
| `group_id` | planted structure: `H#` hub, `R#` ring, `C#` cluster; empty for benign |
| `out_degree`, `in_degree`, `degree` | counted after collapsing parallel edges, no self-loops |
| `weak_component_size` | size of the weakly connected component containing the account |

## `edges.csv`

`instance,source,target`: one row per directed transfer `source → target`. Parallel transfers are
collapsed into one edge and self-loops are removed, as in the paper. No amounts or timestamps (the
study is topology-only).

## Facts you can check

* `Σ out_degree = Σ in_degree = |E|` (directed handshaking lemma) and mean degree `μ = 2|E|/|V|`.
* The detectors see only `edges.csv`. `type` / `is_suspicious` are used only to score them.
