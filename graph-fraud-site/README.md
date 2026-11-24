# Shapes of Suspicion — interactive companion to the DMGT paper

Static website (plain HTML/CSS/JS, no build step, no network needed) for presenting
*"Graph-Property-Based Detection of Suspicious Accounts in Blockchain Transaction Networks"*.

## Run it

Open `index.html` in a browser (double-click), or serve the folder:

```bash
python3 -m http.server 8000   # then open http://localhost:8000
```

## Presenting

| Key | Action |
|---|---|
| `P` | Present mode (big type, one section per screen) |
| `←` `→` | previous / next section (present mode) |
| `T` | toggle **Plain language ⇄ Technical detail** (equations, pseudocode, p-values) |
| `N` | speaker notes for the current section (taken from the slide deck notes) |
| `F` | fullscreen (present mode) |

Each simulation also has a one-click guided demo (heavy-tail slider, detective-bench tour, size experiment).

## What is live and what is the paper's

* **● live** badges — computed in your browser by `js/engine.js`, a JavaScript re-implementation of the paper's
  generator (Table 1), the five detectors + Union / Vote ≥ 2 (Table 3, Algorithm 1) and the metrics (Eq. 6–8).
* **▣ paper** badges — numbers copied from the report into `js/data.js` (Tables 1–9, in-text results). Never simulated.

The port uses its own random generator, so live numbers are close to, not identical with, the paper's.
`node tools/calibrate.js` re-runs Experiment 0 (30 seeds) and prints it beside Table 4.

## Files

```
index.html        narrative (14 sections) + simulation mount points
styles.css        design system (light/dark, responsive, present mode)
js/engine.js      generator, detectors, metrics, SCC / cycle helpers
js/data.js        every number from the paper
js/graphview.js   force layout + zoom/pan canvas renderer
js/charts.js      small SVG charts with tooltips
js/sims1.js       Sim 1 toolkit playground · Sim 2 world builder · Sim 3 heavy tail vs 3σ
js/sims2.js       Sim 4 detective bench · Sim 5 cycle hunt (Algorithm 1) · Sim 6 the net (P/R/F1)
js/sims3.js       Sim 7 investigator game · Sim 8 tuning lab · Sim 9 scale lab · Sim 10 rare-fraud crowd
                  · Table-4 reproduction · "legitimate loops" what-if
js/app.js         nav, toggles, present mode, paper tables/charts, hero
img/              the paper's six figures, unchanged
```

## Before presenting

Fill in the placeholders: team names / USNs, department, faculty (hero), the reflection sentence and
Table 9 (contributions), exactly as they are blank in the paper.
