# Handoff

Read this first. It is what the system is, how to turn it on, and the things
that are not guessable from the code.

Written 19 September 2026, after the `ascent` run finished: 16 systems, zero
refusals, 21 operations applied in Studio.

---

## 1. What this is

An autonomous Roblox game-development pipeline. One paragraph of intent goes in
at one end; a playable place in Roblox Studio comes out at the other. Nobody
writes Luau by hand.

```
idea  ->  Venture Scout      finds and audits game ideas
      ->  Blueprint Architect proposes features, the player journey, the systems
      ->  specification       compiled, validated, given a build order
      ->  Engineer            writes each system as real Luau
      ->  six-check gate      the only definition of acceptable
      ->  git                 landed on master if the whole project still builds
      ->  bridge              loopback HTTP, token paired
      ->  Studio plugin       applies operations into the open place
```

The model that writes the game code is **Antigravity** (`agy`), running
`gemini-3.8-flash-high`. Gemini API keys and a local Ollama are configured as
fallbacks in the same chain.

## 2. The machine layout

Everything that touches Studio runs on **this PC**. The dashboard is reachable
from other devices over Tailscale; the bridge is deliberately loopback-only,
because a plugin that accepts operations from the network is a plugin anyone on
the network can drive.

| Piece | Where | Port |
| --- | --- | --- |
| Dashboard (FastAPI + built React) | this PC | the Tailscale address, port 8742 |
| Bridge (Studio's only way in) | this PC, loopback | `127.0.0.1:34873` |
| Rojo (serves the game repo) | this PC, loopback | `127.0.0.1:34872` |
| Roblox Studio + plugin | this PC | — |
| Antigravity CLI (`agy`) | this PC | — |

Bridge port is one above Rojo's on purpose, so both can run together.

## 3. Turning it on

Double-click **`start-agents.bat`**. It starts what is not already running,
leaves alone what is, and each server gets its own named minimised window.

```
start-agents.bat                            the project named in .env
start-agents.bat C:\RobloxGames\ascent      that one instead
```

It checks rather than starts the things that are not servers — `agy` and the
Rokit tools — because discovering a missing formatter eight minutes into a build
is exactly the failure that check exists to prevent.

Then, in Studio: open the place, and make sure the Rojo plugin is connected and
the bridge plugin shows paired.

## 4. Configuration, and the one rule about it

`.env` at the repository root. **It is gitignored and must stay that way.**

The owner types keys into that file personally. Never ask for a key in chat,
never print one, not even partially, and never commit it.

Settings that matter:

| Key | What it does |
| --- | --- |
| `GAME_PROJECT_DIR` | the game repository the Engineer writes into |
| `DASHBOARD_TOKEN` | what the browser logs in with |
| `USAGE_HOURLY_LIMIT` / `USAGE_WEEKLY_LIMIT` | plan limits, so the usage strip can show what is left (0 = unset) |
| Gemini keys, Ollama models | the fallback chain |

The bridge pairing token is **not** in `.env`: it is generated once and
persisted to `data/bridge_token.txt`, because regenerating it on every start
made Studio fail to connect with `ConnectFail`.

## 5. Running a build

```bash
python scripts/autobuild.py --audit <audit-id> --prompt "one paragraph"
```

Omit `--prompt` to reuse the plan already saved on that blueprint. If the
blueprint has a journey and systems, the architect is not asked again — a second
opinion costs a second bill and builds a different game than the one that was
reviewed.

The same functions run behind the dashboard's build button. The script exists so
a run can be driven without handing a credential to something that has no
business holding one.

## 6. The doctrine

`app/engineer/instruction.md` is the Engineer's permanent instructions, loaded
by `app/engineer/doctrine.py` and substituted into the system prompt. One copy:
edit the file and the Engineer's instructions change. A test fails if the prompt
stops carrying it.

**The priority order is not negotiable and the doctrine must never be allowed to
override it:**

1. The user's approved `GameBuildSpecification`
2. Explicit user steering
3. `instruction.md`
4. The Engineer's own judgement

What it enforces, in short: understand the player's experience before writing
systems; data before behaviour; build in dependency order; get a vertical slice
playable before breadth; do not build features nobody asked for.

## 7. Things that are true and would cost you a day to rediscover

- **Python does not hot-reload.** A running server keeps the code and the `.env`
  it started with. This has caused a wrong answer four separate times — a
  dashboard serving a dead repository, a build writing into the wrong project.
  After changing config or code: restart the process. Not the file. The process.
- **Rojo serves one project.** If it is serving the wrong one and you sync, it
  renames the DataModel and pours the wrong game into the open place. This
  happened twice. Check `http://127.0.0.1:34872/api/rojo` — it names the project.
- **Rokit shims resolve tools from the project's `rokit.toml`**, so every gate
  command must run with `cwd` inside the game repository, not the agent repo.
- **`ExecutePlayModeAsync` never returns control to a plugin.** Report first,
  then start play, or the report is never sent.
- **Git ignores empty directories.** An empty `src/server` does not reach a
  worktree, and every system is then refused for writing outside the project.
- **Landing is by files, not by branches.** A branch can carry a refused file
  that was never accepted. `app/engineer/land.py` copies the accepted files onto
  master byte-exactly, and resets the index on every path that is not committed.
- **A system's `Start()` is the only entry point the bootstrap calls.** If a
  system needs building, `Start` must do it.
- **Heredocs mangle Windows paths.** `C:\Users` inside a bash heredoc is a
  unicode escape error. Write patch scripts to a file instead.
- **One service per ledger.** The service takes an operating-system lock on
  `data/venture_agents.db.locks/service.lock` before startup reads anything,
  and a second copy -- whichever launcher started it -- exits with
  `AnotherService`, naming the holder, before it can mark the first one's runs
  interrupted. `service.lock.holder` says which process holds it. The lock is
  released by the OS when the holder dies, so it never goes stale.
- **A build is running only while its lock is held.** `run_build` holds
  `data/venture_agents.db.locks/<build-id>.lock` for exactly as long as it
  runs and records it under `owner`. Reading a build asks that lock; a build
  that says it is running but whose lock is free is moved to `interrupted`,
  with the last thing it recorded as its end time. An exception escaping a
  build is recorded (`failed`, or `interrupted` if it was cancelled) instead
  of leaving the record claiming to be in progress.
- **The watchdog asks the dashboard first.** If anything answers on the
  dashboard's address, whichever launcher started it, the watchdog does
  nothing and logs nothing. Every line in `data/watchdog.log` is an outage.
- **Systems are written several at once.** `ENGINEER_PARALLEL_SYSTEMS`
  (default 3) is how many; a system still starts only after every dependency
  it has in the build has finished AND landed, because its worktree is cut
  from master when it starts and that is how it sees their real code. Not
  waves: a system starts the moment its own dependencies are done. Landing
  stays one at a time (it checks the whole project in the main checkout).
  `ENGINEER_PROVIDER_CONCURRENCY` (default `ollama=1`) caps a provider across
  the build; a call waits for its provider rather than falling back to a
  weaker one because the first was busy. Set 1 to get the old one-at-a-time
  build exactly.
- **Every `agy` call costs about 25,000 prompt tokens of its own overhead**,
  measured on a one-line prompt. Attempts are not free even when tiny.
- **Records are UTC; logs are local time (UTC+5:30).** Comparing one with the
  other without converting produced a wrong "seven hours" that was 2.2.
- **The venv's `python.exe` on Windows is a launcher**, not the interpreter:
  it runs the base Python as a child, inside a job that kills both together.
  The PID that holds a lock or a socket is the child's.

## 8. The current state

- Game repository: `C:\RobloxGames\ascent` — Checkpoint Ascent, a tower climb,
  16 systems on master, gate passing.
- The Studio place is saved at `C:\RobloxGames\fisherman\ascent.rbxl`. Harmless
  — Rojo syncs over the socket, not from the folder — but confusing, and worth a
  `Save As` into the ascent folder.
- `C:\RobloxGames\game` is the **abandoned** infected-base project. Its last two
  commits are ascent strays from a run that was pointed at the wrong repository.
  Nothing reads it. Ignore the whole folder.
- `C:\RobloxGames\fisherman` is the previous finished project: 11 systems, also
  zero refusals.
- The owner drives sessions from a Mac as well as this PC: Claude Code Remote
  Control links the running session to claude.ai, and the dashboard is on the
  tailnet. Both need this PC awake with the Claude app open; Studio itself is
  only visible here.

## 9. What is not built

- No 3D architecture map. The graph is 2D SVG. The three.js version was
  discussed and never started.
- `BuildChangeRequest` — revising a spec mid-build — is designed, not built.
- The fine-tune needs a merged GGUF; the adapter alone is not loadable.
- `TaskState.TESTING` exists in the model but nothing sets it: the gate has no
  separate test phase yet.
- A pool of model **accounts**. Systems are now written several at once (see
  section 7), but every `agy` call uses the one account in the keyring, so
  more accounts would not add capacity without a way to spread calls across
  them.

## 10. House rules

These came from the owner and are not up for reinterpretation:

- Every system built and every line written must be **scalable and flexible, not
  rigid**: extensible by data, never hard-coded.
- **No fake integration.** No simulated progress, no invented logs, no build
  state that was not measured. The 3D/graph views visualise real state or they
  show nothing.
- Design is the owner's team's job. Code correctness is ours.
- Tools are installed only from official sources, and only after asking.
