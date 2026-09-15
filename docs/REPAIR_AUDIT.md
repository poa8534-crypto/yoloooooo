# Repair audit — implementation in progress

Baseline: `f54e55af8f8d5b92b9d9c4957e17bfca71a9f032`, 2026-09-15.
Existing uncommitted work is preserved. SHA256 before this repair:

| File | SHA256 |
| --- | --- |
| app/audit_activity.py | 54ACCE3872E648E7F60A0ABF20B327605A7E8F239F58343A5330F6C8373BE21E |
| app/main.py | 98907BC33FA52D43A76BE9CCB42ACA84F1426153B498F65AC681C344A68B3A22 |
| app/workflows.py | 69AC2C97D1298475340EFD9B0A09C402DF3CCEC7C6FD915114DF97043BD569F8 |
| frontend/src/App.tsx | 0081D55BEC99E13A01B65ED6FDA22E7B39D7B5559CC4E3657E2718F9438E0264 |
| tests/test_audit_durability.py | 60EC465606987696304ED0D774CF78D33D52DAB53513047929D1912BD05D7197 |

Baseline automated result: 332 backend tests pass, two dependency deprecation warnings.
This is not a release certification or complete browser coverage.

## Interaction inventory and baseline defects

Each family includes every rendered row, not just its first example. Mutation tests must use isolated fixtures. No live ledger reset or fabricated review is authorized by this verification.

| Control family | Expected behavior / prerequisite | Boundary / persistence | Baseline status | Severity / regression target |
| --- | --- | --- | --- | --- |
| Navigation, Back/Forward, reload | Preserve page and entity/version | URL only | failed: local state only | P1 routing tests |
| Home timeline 7D/30D/All | Filter captured dates | UI only | passed for current single-day data; multi-day not tested | timeline fixtures |
| Home refresh/live counts | Consistent current data, timestamp errors | GET summary/timeline/runs/sources | failed: stale zero counts during run | P1 polling tests |
| Home candidate Inspect | Open clicked candidate | UI route | failed: opens general list | P1 per-row routing |
| Idea filters | Show actual engine states | UI only | failed: collection_only omitted | P1 status mapping |
| Idea cards and disclosures | Separate proposals and games; exact identity | GET candidate/audit | failed: games presented as ideas | P2 identity tests |
| Brief anchors | Reach selected section without losing route | UI only | not tested comprehensively | navigation tests |
| Fact buttons | Exact claim, observation and passage | GET evidence/source | failed: entire batch shown | P1 provenance tests |
| Drawer close, Escape, keyboard | Close, trap and restore focus | UI only | failed: Escape/focus unsupported | P2 dialog tests |
| Sources search, row Inspect, links | All records searchable, sanitized URLs | GET sources/evidence | failed: capped local search; row action works | P2 paginated fixtures |
| History filters/search/expand/brief | Exact saved run and output | GET agent-runs/audit | not tested comprehensively; header not keyboard button | P2 history tests |
| Matching select/alternatives/refresh | Correct record and comparison | GET matching | passed representative selection; all rows not tested | review fixtures |
| Matching approve/reject/reassign | Required reason; immutable original | POST review | not tested live intentionally | isolated review tests |
| Matching laboratory tabs/collapse | Real action or absent | UI only | unavailable placeholders | remove placeholders |
| Hunter start/progress/report/resume | Durable bounded run | POST/GET runs + SSE | latest existing run complete; mutations not tested in audit | isolated lifecycle tests |
| Scout mode/readiness/start/activity | Bound input; durable execution | audit APIs | failed: ambiguous modes and candidate-keyed state | P1 job tests |
| Collection matrix | Captured history or explicit unimplemented | GET history/calibration | failed: promises unimplemented rows | P2 collection tests |
| Health and quotas | Observed health, not configuration claims | GET health | failed: configured labeled authenticated | P1 health tests |
| Theme/zoom | Minimalist and browser-native zoom | browser preferences | replacement requested; not tested | responsive screenshots |
| Alerts dismissal | Accessible labels and operation-specific error | UI only | not tested comprehensively | error fixtures |
| Reset administration | Explicit confirmation, backup, isolated only | CLI database writes | not tested in this audit | existing isolated reset tests |

## Acceptance still required

Implementation, current backend/component/browser suites, isolated mutation and recovery tests,
responsive screenshots, one bounded live run, and final migration/launch handoff.
No untested path should be marked passed. Do not infer source truth from provenance.
