# Interaction inventory

Every control family in the dashboard, what it is expected to do, and how that
expectation is currently checked. Classifications are **passed**, **failed**,
**unavailable** or **not tested**. "Not tested" is used wherever no automated
check exists; it is never replaced with an assumption that something works.

Automated coverage behind this table:

| Suite | Count | What it exercises |
| --- | --- | --- |
| `pytest` | 417 | API behaviour, evidence rules, agent guarantees |
| `vitest` | 8 | Routing helpers, run phases and the evidence drawer component |
| `playwright` | 234 | Real browser, three viewports, including actions that change data |

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
| Refresh data | Reloads every panel | passed | `coverage.spec.ts` |
| Theme toggle | Removed; the editorial direction commits to one look | unavailable | `coverage.spec.ts` asserts it is absent |
| Application zoom control | Removed in favour of browser zoom | unavailable | removed by design |
| Global alert dismissal | A failed panel says so and the alert clears | passed | `coverage.spec.ts` |

## Home

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Collection totals | Match the ledger, refresh coherently | passed (API) | `test_dashboard_api.py` |
| Timeline 7D / 30D / All | Filters captured days | passed | `coverage.spec.ts` (multi-day fixture with a gap) |
| Candidate "Inspect" | Opens that exact game | passed | `navigation.spec.ts` |
| Recent source links | Open that artifact's provenance | passed | `coverage.spec.ts` |

## Ideas and game dossiers

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Ideas vs game dossiers toggle | Generated concepts are not mixed with captured games | passed | `interactions.spec.ts` |
| "Research only" filter | Includes `collection_only` | passed | `interactions.spec.ts` |
| A filter with no members | Says so rather than showing everything | passed | `interactions.spec.ts` |
| Open a brief | Loads that candidate, survives reload | passed | `navigation.spec.ts` |
| Fact button | Opens that exact claim | passed | `interactions.spec.ts` |
| Brief disclosures | Open and close, including by keyboard | passed | `responsive.spec.ts` |
| Brief section anchors | Jump without losing the route | passed | `coverage.spec.ts` |
| Run Venture Scout audit | Starts a durable job; label matches the operation | passed | `mutations.spec.ts` |
| Watch and cancel from a brief | Same drawer and job as the Scout page | passed | `mutations.spec.ts` |

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
| Tab trapping | Focus stays inside while open | passed | `coverage.spec.ts` |

## Agent History

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Lists both agents, newest first | Interleaved by time | passed | `test_scout_deliberation.py` |
| Expand a run | Shows that exact stored output | passed | `mutations.spec.ts` |
| Citations | Re-verified now, shown as claims | passed | `test_scout_deliberation.py` |
| Blocked runs | Listed with the reason | passed | `test_scout_deliberation.py` |
| Operation recorded | An analysis is distinguishable from a critique | passed | `coverage.spec.ts` |
| Agent filter / search | Each filter shows only its agent; the two partition the list | passed | `coverage.spec.ts` |

## Matching Engine

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Placeholder laboratory tabs | Absent, not disabled decoration | passed | `interactions.spec.ts` |
| Disabled controls | Explain themselves | passed | `interactions.spec.ts` |
| Review selection and comparison | Shows the right record | passed | `coverage.spec.ts` |
| Approve / reject / reassign | Requires a reason; original immutable | not tested | deliberately not exercised |
| Queue totals | Count the queue, not the page | passed | `test_pagination.py` |

## Meta Hunter

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Start a run | Bounded, durable | not tested (browser) | backend covered; a live run is the real check |
| Empty niche | Refused | passed | `mutations.spec.ts` |
| Progress and stage | Reflects the current stage | passed | `interactions.spec.ts`, `research-phase.test.ts` |
| Report selection | Opens that run's report | passed | `coverage.spec.ts` |
| Interrupt / resume | Explained, resumable, budgets retained | passed | `coverage.spec.ts`, `test_audit_jobs*.py` |

## Venture Scout

| Control | Expected | Result | Evidence |
| --- | --- | --- | --- |
| Operation selector | Offers both operations | passed | `interactions.spec.ts` |
| Audit idea without a proposal | Disabled, with the reason | passed | `interactions.spec.ts` |
| Run an audit | Starts a durable job, stores, restores after reload | passed | `mutations.spec.ts` |
| Double submission | Cannot submit twice; records one run | passed | `mutations.spec.ts` |
| Background work drawer | Streams each pass and the model's own reasoning | passed | `mutations.spec.ts`, `coverage.spec.ts` |
| Cancel a running audit | Reaches a cancelled terminal state | passed | `mutations.spec.ts`, `test_audit_jobs*.py` |
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
| Build identity | Says which commit is answering | passed | `test_build_identity.py`, `coverage.spec.ts` |
| Failing dependencies | Listed first | passed | `interactions.spec.ts` |
| Quota display | Local reservations, not remote balance | passed | `coverage.spec.ts` |

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
| Contrast of visible body text | passed | `coverage.spec.ts` (programmatic ratio check, not a full audit) |

## Open findings

1. **`source_backed_design_incomplete` has never occurred in a live run.** The
   path is unit-tested and mutation-checked, and its rendering is now checked
   against a stubbed record in `coverage.spec.ts`. What remains unobserved is
   the state arising naturally: no real audit has yet failed its revision.
2. **Matching review mutations are deliberately not exercised.** Approving,
   rejecting or reassigning an association changes what downstream metrics are
   allowed to resolve through. Those paths are covered by backend tests against
   isolated fixtures and are not clicked in a browser.
