# Interaction inventory

Every control family in the dashboard, what it is expected to do, and how that
expectation is currently checked. Classifications are **passed**, **failed**,
**unavailable** or **not tested**. "Not tested" is used wherever no automated
check exists; it is never replaced with an assumption that something works.

Automated coverage behind this table:

| Suite | Count | What it exercises |
| --- | --- | --- |
| `pytest` | 406 | API behaviour, evidence rules, agent guarantees |
| `vitest` | 8 | Routing helpers, run phases and the evidence drawer component |
| `playwright` | 162 | Real browser, three viewports, including actions that change data |

Browser tests run against an isolated ledger created by
`scripts/e2e_fixture.py`: a temporary database and artifact directory, a stub
answering the model endpoint, and no API credentials. Nothing in this suite can
reach the operator's ledger or spend quota. Two tests assert that directly.

## Global shell

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Sidebar navigation, all nine pages | Opens the page, marks it current, updates the URL | passed | `navigation.spec.ts` (one test per page) |
| Browser Back / Forward | Moves between visited pages | passed | `navigation.spec.ts` |
| Reload | Keeps the page | passed | `navigation.spec.ts` |
| Unknown route | Falls back to Home, not a blank shell | passed | `navigation.spec.ts` |
| `aria-current` on the active item | Exactly one item marked | passed | `navigation.spec.ts` |
| Keyboard navigation | Focus and Enter operate the sidebar | passed | `responsive.spec.ts` |
| Focus ring | Visible on focused controls | passed | `responsive.spec.ts` |
| Refresh data | Reloads every panel, surfacing per-panel errors | not tested | manual only |
| Theme toggle | Switches light/dark | not tested | manual only |
| Application zoom control | Removed in favour of browser zoom | unavailable | removed by design |
| Global alert dismissal | Clears the alert | not tested | — |

## Home

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Collection totals | Match the ledger, refresh coherently | passed (API) | `test_dashboard_api.py` |
| Timeline 7D / 30D / All | Filters captured days | not tested | single-day fixture only |
| Candidate "Inspect" | Opens that exact game | passed | `navigation.spec.ts` |
| Recent source links | Open that artifact's provenance | not tested | — |

## Ideas and game dossiers

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Ideas vs game dossiers toggle | Generated concepts are not mixed with captured games | passed | `interactions.spec.ts` |
| "Research only" filter | Includes `collection_only` | passed | `interactions.spec.ts` |
| A filter with no members | Says so rather than showing everything | passed | `interactions.spec.ts` |
| Open a brief | Loads that candidate, survives reload | passed | `navigation.spec.ts` |
| Fact button | Opens that exact claim | passed | `interactions.spec.ts` |
| Brief disclosures | Open and close, including by keyboard | passed | `responsive.spec.ts` |
| Brief section anchors | Jump without losing the route | not tested | — |
| Run Venture Scout audit | Runs, stores, restores | passed | `mutations.spec.ts` |

## Sources

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Search | Matches the whole library, not one page | passed | `interactions.spec.ts`, `test_pagination.py` |
| Paging | Walks every record once; disabled at the ends | passed | `test_pagination.py`, `interactions.spec.ts` |
| Row inspect | Opens that artifact | passed | `interactions.spec.ts` (via drawer) |
| Captured URLs | Carry no credentials | passed | `mutations.spec.ts` |
| External links | Open the sanitised destination | not tested | not followed, by policy |

## Evidence drawer

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Opens on the selected claim | Shows that claim, not the whole response | passed | `interactions.spec.ts` |
| Escape | Closes | passed | `interactions.spec.ts`, `EvidenceDrawer.test.tsx` |
| Focus restoration | Returns focus to the opener | passed | `interactions.spec.ts` |
| Dialog semantics | Labelled region | passed | `responsive.spec.ts` |
| Tab trapping | Focus stays inside while open | not tested | implemented, unverified |

## Agent History

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Lists both agents, newest first | Interleaved by time | passed | `test_scout_deliberation.py` |
| Expand a run | Shows that exact stored output | passed | `mutations.spec.ts` |
| Citations | Re-verified now, shown as claims | passed | `test_scout_deliberation.py` |
| Blocked runs | Listed with the reason | passed | `test_scout_deliberation.py` |
| Agent filter / search | Narrows the list | not tested (browser) | API covered |

## Matching Engine

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Placeholder laboratory tabs | Absent, not disabled decoration | passed | `interactions.spec.ts` |
| Disabled controls | Explain themselves | passed | `interactions.spec.ts` |
| Review selection and comparison | Shows the right record | not tested (browser) | API covered |
| Approve / reject / reassign | Requires a reason; original immutable | not tested | deliberately not exercised |
| Queue totals | Count the queue, not the page | passed | `test_pagination.py` |

## Meta Hunter

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Start a run | Bounded, durable | not tested (browser) | backend covered; a live run is the real check |
| Empty niche | Refused | passed | `mutations.spec.ts` |
| Progress and stage | Reflects the current stage | passed | `interactions.spec.ts`, `research-phase.test.ts` |
| Report selection | Opens that run's report | not tested | — |
| Interrupt / resume | Durable, budgets retained | not tested (browser) | `test_audit_jobs*.py` |

## Venture Scout

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Operation selector | Offers both operations | passed | `interactions.spec.ts` |
| Audit idea without a proposal | Disabled, with the reason | passed | `interactions.spec.ts` |
| Run an audit | Runs, stores, restores after reload | passed | `mutations.spec.ts` |
| Double submission | Cannot submit twice; records one run | passed | `mutations.spec.ts` |
| Activity drawer | Streams each pass | not tested (browser) | backend covered |
| Readiness gates | Computed server-side per candidate | passed | `interactions.spec.ts` |

## Collection & Calibration

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Snapshot continuity | Real capture days, gaps not interpolated | passed | `test_dependency_health.py`, `interactions.spec.ts` |
| Niche-cluster calibration | Named as unimplemented | passed | `interactions.spec.ts` |
| Scoring lock | No score before activation | passed | `interactions.spec.ts` |

## System Health

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Dependency status | Observation separate from configuration | passed | `test_dependency_health.py`, `interactions.spec.ts` |
| Failing dependencies | Listed first | passed | `interactions.spec.ts` |
| Quota display | Local reservations, not remote balance | not tested (browser) | API covered |

## Administration

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Ledger reset | Backs up, wipes, restores triggers, keeps quota | passed | `test_reset_ledger.py` |

## Layout and accessibility

| Check | Result | Evidence |
| --- | --- | --- |
| No horizontal page scroll, 9 pages × 3 widths | passed | `responsive.spec.ts` |
| Browser zoom | passed | `responsive.spec.ts` |
| Keyboard-only navigation, filters, disclosures | passed | `responsive.spec.ts` |
| Labelled inputs | passed | `responsive.spec.ts` |
| Screen-reader audit | not tested | no assistive-technology pass has been run |
| Colour-contrast audit | not tested | — |

## Open findings

1. **`source_backed_design_incomplete` has never been produced live.** The path
   is unit-tested and mutation-checked, but no real audit has yet failed its
   revision, so the banner and history rendering for it are unverified against
   real data.
2. **Matching review mutations are deliberately not exercised.** Approving,
   rejecting or reassigning an association changes what downstream metrics are
   allowed to resolve through. Those paths are covered by backend tests against
   isolated fixtures and are not clicked in a browser.
