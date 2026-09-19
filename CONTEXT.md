# Context

The history behind the code, in the order it happened. `HANDOFF.md` is what the
system is; this is why it is that way, and what it cost to find out.

Everything here was observed. Where a number appears, it was measured.

---

## The shape of the project

An autonomous pipeline that turns one paragraph of intent into a playable
Roblox place, with no hand-written Luau. Five stages: Venture Scout audits
ideas, the Blueprint Architect plans, the Engineer writes systems, a six-check
gate decides what is acceptable, and a bridge applies the result into Roblox
Studio.

The model writing game code is Antigravity (`agy`) on `gemini-3.8-flash-high`.
Gemini API keys and a local Ollama sit behind it in a fallback chain.

---

## Phase 1 — the control room

The dashboard was rebuilt into a dense operations view: architecture map, node
inspector, Studio explorer, build machine, live log. Then a **steering panel**
was added so a directive can be sent into a build that is already running.

The rule that came out of it: a directive can never reach the system currently
in flight, because that system's prompt was built before the directive existed.
`reachable` excludes it, and `carried_into` records which systems the
instruction actually went into — that is what makes "applied" checkable rather
than claimed.

## Phase 2 — making it work from anywhere

Requirement: a command given from a Mac, through the website, must use every
tool without issue. The decision was **Studio stays on the PC**. The dashboard
binds a Tailscale address so other devices can drive it; the bridge stays
loopback-only, because a plugin that accepts operations from the network is a
plugin anyone on the network can drive.

## Phase 3 — the merge gap

Accepted systems were not reaching master. Worse: a landing branch was found
carrying a **refused** file — `AirlockService`'s branch held a refused
`InfectedService.luau`.

Fix: land **files, not branches**. `app/engineer/land.py` copies the accepted
files onto master byte-exactly (`read_bytes`/`write_bytes`, because a text
round-trip renormalised line endings), and resets the index on every path that
is not part of the commit.

Then six systems landed and master went **red** — four cross-system type errors
that no single-system gate could see. Rolled back, added a project-level
`verify` that runs after landing. A system now lands only if the whole project
still builds.

## Phase 4 — the first full run

A simple prompt, left alone to see what the chain does when nobody rescues it.
It produced a red block jumping in front of the player: systems existed, world
did not. World building was added to the prompts and the schema
(`builds_world`).

The fisherman project followed: **11 systems, zero refusals**, all landed, gate
passing, a world visible in Studio.

## Phase 5 — the doctrine

The Engineer was generating plausible code in an implausible order. The fix was
a permanent instruction file, `app/engineer/instruction.md`, plus the machinery
to prove it is obeyed rather than merely present.

- `doctrine.py` loads it and substitutes it into the system prompt. One copy.
- `planning.py` computes build order with **Kahn's algorithm**, priority only
  breaking ties between systems that are equally ready. Topology is absolute.
- `validate_plan()` runs at compile time, before any code: a reward with nowhere
  to go, an action no system implements, a refused feature returning, a
  DataStore in a session-only game, a dependency circle.
- `gates.py` derives task states and playability gates from what is actually
  built. A gate nothing claims does not pass.
- The UI gained **Player flow** and **Build order** views beside the systems map.

Proof it is not a model's preference: systems fed in scrambled come out
`ItemDefinition -> RarityDefinition -> FishCatalog -> CurrencyService ->
InventoryService -> RodService -> FishingService -> ShopService -> QuestService`.
Quests last, because nothing depends on them.

The priority order, fixed: the approved specification, then explicit user
steering, then the doctrine, then the Engineer's judgement. The doctrine is
never allowed to override an explicit user decision.

## Phase 6 — the ascent run

A deliberately different genre, to prove the doctrine is not fishing-specific: a
tower climb, with no inventory, no economy, and **progress** as the reward.

It immediately found a real bug. The plan validator asked "does any system hold
the reward?" by matching system names against a list of storage nouns —
`inventory`, `wallet`, `satchel`, `vault`. Ascent's holder is a
`CheckpointService`. No match, plan refused. The vocabulary was fishing-shaped.

Fixed by asking a genre-neutral question first: does any system share a real
word with the destination the plan itself named? The storage nouns stayed as a
fallback, so a plan that says "creel" and builds an `InventoryService` still
passes.

**Result: 16 systems, zero refusals, 32 attempts (average 2.0), 21 operations
applied in Studio, 0 failed.** Hardest system: `CheckpointService`, 4 attempts.

## Phase 7 — checking that what is running is what the records say

The next session began by verifying the handoff instead of trusting it, and
the service log disagreed with it three ways.

**The Engineering page was asking for a finished build ten times a second.**
1871 requests in 176 seconds from the Mac, for the ascent build, which had
finished hours before and should have been fetched once. The polling effect
depended on the graph it fetched; every answer was a new object, so every
answer re-ran the effect, whose first act was to ask again. It had been there
since the page was first written, and it filled three 5 MB log rotations in
about ten minutes.

**A second copy of the service could rewrite the first one's ledger.** The
watchdog judged the service by the scheduled task's state. While the dashboard
was running from `start-agents.bat` instead, the task sat idle, so the watchdog
launched a copy every five minutes -- 21 that afternoon, each logged as an
outage. Each copy ran startup in full and only then failed to bind the port,
because uvicorn binds after startup, and startup marks every research run,
audit and audit job in flight as interrupted by a previous shutdown. Nothing was
in flight that day (checked in the database); anything that had been would
have been recorded as interrupted while it was still running. Fixed at the
ledger, not the port: the service takes an OS lock before startup reads
anything, and a second copy exits naming the holder. Proved live by starting a
duplicate against the running service -- exit 3, port never bound.

**Nine records said work was in flight when nothing was doing it.** Five build
records sat in `generating`, `planning` and `building` for 2 to 17 hours, and
the dashboard drew them as running -- "Working on PlayerSpawnService" two hours
after that process had gone. Three stopped mid-system with the next build
starting minutes later: killed, which writes nothing. Two stopped exactly
where bug 11's missing transitions would have raised, so an exception escaped
`run_build` inside a dashboard that kept serving. The other four were
`engineer-run` records, stale for up to 42 hours. A startup sweep would have
been wrong, because `autobuild.py` runs builds in a process of its own. So a
build now holds a lock for as long as it runs and names it on the record, and a
reader asks the lock. The five old records named no lock, so they were settled
once by hand after checking that no build process existed and that the service
had been restarted since each last wrote. Nothing displays the `engineer-run`
records, so they were left.

One number in this session was wrong when first reported: "seven hours" for
that PlayerSpawnService build, from comparing a UTC timestamp with local time
(UTC+5:30). It was 2.2 hours. Timestamps in the records are UTC; the logs are
local.

The lock (`app/ownership.py`) was tested against real processes: one killed
outright, and one whose child outlives it, as `agy` can outlive a build. On
Windows a byte-range lock belongs to the process that took it -- measured by
making the handle inheritable on purpose -- so even an inherited handle does
not keep a dead build alive.

---

## Phase 8 — writing systems at once

The runner walked `spec.build_order` one system at a time. Ascent took 4948 s
from first event to last, of which 4902 s was the systems' own time and 43 s
was landing -- 2.5 to 2.9 s a system, because the whole-project check is fast.

**Measured before building it.** Three `agy` calls at once all answered, with
no errors and no 429s: one alone took 17.0 s, three together 33.9 s of wall
time. Not fully concurrent, not queued. The same probe showed every `agy` call
spends about 25,000 prompt tokens of its own before it reads a word of ours.

**Not waves.** Ascent's real graph is five layers deep and six wide, but
waiting for a whole layer holds the next one hostage to its slowest system. A
system now starts the moment its own dependencies have finished -- landed or
refused, because a system learns what its dependencies export from the
project its worktree is cut from. On ascent's graph that makes the longest
possible build 1669 s, against 1797 s for waves. From the measured per-system
times (a projection: it assumes a system takes as long alongside others as
alone), one at a time reproduces the real 82.4 minutes, two at once gives 43,
three 34, six 28. Three is the default, because three is what was measured.

Landing stays one at a time behind a lock, together with the Services-module
merge that reads the checkout it writes to. Provider limits are per build and
a call waits for its own provider: a system is never written by Ollama
because `agy` happened to be busy. Git lock collisions between runs sharing
one `.git` are retried briefly; nothing else is.

Proved the scheduler with tests built to deadlock if a claim is false -- two
systems that each wait for the other to be inside, a system that will not
finish until a later-layer system has started -- and proved the build loop by
disabling parallelism (the overlap test times out) and the landing lock (two
landings overlap).

## Bugs worth remembering

Ordered by how much time they cost, not by when they happened.

1. **Python does not hot-reload.** Hit four times. A running dashboard served a
   dead repository; a build wrote into the wrong project because it inherited an
   old `.env`. Restart the process, not the file.
2. **Rojo served the wrong project — twice** — renaming the DataModel and
   pouring the wrong game into the open place.
3. **A build wrote into `C:\RobloxGames\game`** instead of `ascent`, because the
   first run had an inline environment override and the relaunch did not.
   `TowerService` and `SessionLifecycleService` landed in the dead repository.
4. **Landing branches carried refused code.** See phase 3.
5. **Six systems landed and master went red.** See phase 3.
6. **`GateReport.failed` is a property, not a method**, so every real failure was
   reported as "the check could not run".
7. **Empty `src/server` missing from worktrees** — git does not track empty
   directories — so all 14 systems were refused for writing outside the project.
8. **`land()` left staged residue** (`AD src/server/BayService.luau`) until the
   index was reset on every non-commit path.
9. **`Author identity unknown`** in a fresh repository; the git identity is now
   passed explicitly.
10. **The bridge token regenerated on every start** → `ConnectFail`. Now
    persisted to `data/bridge_token.txt`.
11. **`planning -> validating` and `building -> succeeded` were missing from the
    transition table.** Both did all the work and then refused to finish.
12. **The playtest swallowed the report**: `ExecutePlayModeAsync` never returns
    control to a plugin. Report first, then play.
13. **`PierService.Build` was never called** — the bootstrap calls only
    `Start()`. The contract is now in both prompts.
14. **`"pet"` was filtered by `len(word) > 3`**, so the excluded-feature check
    never matched. Now `>= 3` with word-boundary CamelCase matching, which
    catches `PetService` without catching `Carrier`.
15. **An infinite source-fetch retry loop**: the effect depended on `loading`,
    and failure reset it. Keyed on `asked` instead.
16. **Duplicate React keys** in the Studio tree, from depth+name. Full paths now.
17. **`UnicodeEncodeError`** from selene's box-drawing characters on Windows.
18. **f-string `{DOCTRINE}`** evaluated at import; escaped to `{{DOCTRINE}}`.
19. **Heredocs mangle Windows paths.** `C:\Users` inside a bash heredoc is a
    unicode escape error. Recurred about eight times, and once more in phase 8
    as a `\\n` that arrived in the file as a real line break inside an
    f-string. Write patch scripts to a file.
20. **`/api/builds` shipped every event of every build to the browser.** Now it
    returns summaries with counts computed backend-side.
21. **A missing field in a usage response white-screened the workspace.** Found
    by the tests it broke; the strip now renders nothing rather than crashing.
22. **Two type errors were failing `tsc -b` while `tsc --noEmit` passed**,
    because the latter skips the test project. The report that said "build
    clean" was wrong, and was corrected.
23. **A React effect keyed on the object it fetched** re-fetched on every
    answer: 10.6 requests a second for a finished build. Key polling on a
    boolean, never on the answer.
24. **Startup ran before the port was bound**, so a duplicate service did all
    its reconciling -- marking the other service's runs interrupted -- and only
    then failed. The guard has to come before the first read, not at the bind.
25. **Records that say "running" were believed.** A killed process writes
    nothing, so a record's last word is not evidence that anything is still
    working. Ask something the operating system maintains: a held lock.

## Method

The habit that found most of these: **measure, do not assume.**

- Proved the interface note worked by grepping regenerated Luau for real
  function names.
- Proved tests catch bugs by reverting the fix and watching them fail — done
  again for the build-history feature, where stubbing out the refusal count
  failed the test that claims to see it.
- Used `delete_instance`'s skip-if-absent behaviour as the *check* for zombie
  contamination, rather than trusting a report.
- Verified build order by scrambling the input and reading the output.

## What the owner has said, and what it means

- **"Every system and every line must be scalable and flexible, not rigid."**
  Extensible by data, never hard-coded. This is the rule the fishing-vocabulary
  validator broke, and why it was rewritten rather than patched with more nouns.
- **"Do not use fake integration."** No simulated progress, no invented logs, no
  build state that was not measured.
- **"The 3D visualisation is not an animation of what we imagine the Engineer is
  doing."** It shows real state or it shows nothing.
- **Keys go only into gitignored `.env` files, typed by the owner.** Never asked
  for in chat, never printed, not even partially.
- **Tools are installed only from official sources, and only after asking.**
- Design is the owner's team's job; code correctness is ours.

## Open threads

- Parallel builds are built but not yet measured on a real build. The
  projection says about 34 minutes for ascent at three at once; the first real
  run will say how much of that survives rate limits and CPU contention. The
  dashboard shows the systems' own time beside the elapsed time, so the answer
  is on screen rather than estimated.
- `engineer-run:` records can still be left saying `running` by a killed
  process. Nothing displays them today; if something ever does, they need the
  same lock the builds have.
- Build status labels live in three frontend maps (the workspace, the history
  list, the blueprint page), and the history one names a status, `applying`,
  that does not exist. One map would be one place to add a status.
- `app/service.py` logs everything uvicorn writes to stderr at ERROR, so
  "Started server process" is an ERROR line and searching the log for ERROR
  finds mostly noise.
- Cross-provider retry: a refusal loop is one model failing the same way three
  times. `InfectedService` went 6/6 refused before prompt rules were added.
- A reviewer pass before the gate, to catch cross-system type errors earlier.
- Roblox now ships a first-party Studio MCP server. Worth evaluating for
  *verification* — reading the real DataModel instead of trusting the plugin's
  report — though it is stdio, so the client must run on the PC.
- Antigravity reports real quota (weekly and five-hour percentages) in its own
  settings panel, but exposes no CLI or file for it. Reading it would mean
  reverse-engineering a private API with the owner's session token, which was
  declined. The dashboard shows measured spend and the real count of 429s
  instead.
