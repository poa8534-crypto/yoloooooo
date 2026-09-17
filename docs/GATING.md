# Who may write what, and what has to be true before it lands

Three things write to these repos now: a person, the Engineer Agent, and
Hermes. Hermes runs unattended, may commit and push, and has desktop control.
This document is how that stays safe without taking autonomy away.

The design rests on one asymmetry. **An agent can be trusted to produce work
and not to certify it.** Everything below follows from that.

---

## 1. The ground facts

These are checked, not assumed. They shape everything else.

| Fact | Consequence |
|---|---|
| The game repo has **no remote** | "Push" is meaningless there. The gate is what reaches `master`, not what reaches a server. |
| The game repo's trunk is **`master`** | The agents repo uses `main`. Automation that assumes `main` will fail silently on one of them. |
| The agents repo pushes to **github.com/poa8534-crypto/Roblox** | Hermes pushing there is real and public-facing. |
| The guard lives in the **agents** repo | It polices the game repo from outside it. |
| Hermes has `--worktree`, `cron`, `hooks`, `approvals` | The gate can be built from what already exists. |

---

## 2. One definition of "acceptable"

`C:\RobloxGames\game\scripts\verify.ps1`.

```powershell
powershell -File scripts\verify.ps1
```

Five checks, all of which run even after one fails, so an author fixes
everything in one pass:

1. `rojo sourcemap` — an input to the type check; if it fails the rest is meaningless
2. `selene` — lint
3. `stylua --check` — formatting, unchanged
4. `luau-lsp analyze` — types, with `--platform=roblox` and the **None** definitions
5. the guard — `game` and `script`, which luau-lsp cannot see

Exit 0 only if every check passed.

Why one script: three writers running four slightly different command lines
will drift, and the drift is invisible until something ships broken. A change
to the standard is now a diff in one file.

**The two flags are load-bearing.** Under the plain `globalTypes.d.luau`,
`Instance.new("Part"):GetDebugId()` type-checks clean and fails at run time.
Without `--platform=roblox`, Roblox semantics do not apply. Both were
established by testing, and both are recorded in the script's comments so
nobody "simplifies" them away.

`-WithoutGuard` exists for a machine with no agents checkout. It is named as a
statement rather than a default, because using it means `game` and `script` go
unchecked.

---

## 3. Branch layout

```
  game repo (local only)            agents repo (pushes to GitHub)
  ------------------------          ------------------------------
  master   <- gated trunk           main     <- gated trunk
    ^                                 ^
    | merge only when green           | merge only when green
    |                                 |
  hermes/<task>                     hermes/<task>
  engineer/<run-id>                 claude/<topic>
```

Rules:

- **Nothing commits directly to a trunk.** Not Hermes, not the Engineer Agent.
- Hermes runs with `hermes -w`, which gives each session its own git worktree
  under `.worktrees/`. Its work is isolated by construction rather than by
  good behaviour.
- The Engineer Agent's retry loop works on `engineer/<run-id>` and only asks
  for a merge once `verify.ps1` is green.
- A branch that cannot go green is **left in place**, not deleted. A failed
  attempt is evidence.

---

## 4. The merge gate

A branch becomes trunk only when all three hold:

1. `verify.ps1` exits 0 **on a checkout of that branch**
2. `verify.ps1` itself is **unmodified** relative to trunk
3. nothing under the protected paths changed without a human reading it

Point 2 is the one that matters. An agent that can edit the gate has no gate.
The check is a plain diff against trunk, not a hash stored somewhere the agent
can also reach.

### Protected paths — agent may propose, human must approve

```
  agents repo:
    app/engineer/luau_guard.py        the guard itself
    scripts/guard_check.py            how the gate invokes it
    scripts/refresh_roblox_services.py
    docs/GATING.md                    this document
    .github/                          any future CI
    tests/test_luau_guard.py          the guard's own tests

  game repo:
    scripts/verify.ps1                the gate
    selene.toml  stylua.toml          what the checks mean
    .gitattributes                    line endings the format check depends on
    default.project.json              what maps where
```

A change under these paths is not forbidden — the guard will need to grow, and
Hermes noticing a hole in it is a good outcome. It simply cannot be
self-certified. **The agent proposes; a person merges.**

Everything else — `src/`, docs, dashboard code, tests that are not the guard's
— an agent may land on its own once green.

---

## 5. Blast radius

Hermes was granted both repos and self-push deliberately. These bound it
without narrowing that grant.

**Refuse outright** (via `hermes approvals` / `command_allowlist`):

- `git push --force`, `git push -f`, and any push to `master` or `main`
- `git reset --hard` on a trunk
- reading or writing any `.env`
- `rokit` changes — the pinned toolchain is what makes results reproducible

**Allow freely:** everything in a worktree on its own branch, the whole of
`verify.ps1`, reading any source, running the test suites.

`hermes approvals suggest` mines past decisions and proposes allowlist entries.
Run it after the first week rather than guessing the list up front — what it
proposes is evidence about what Hermes actually does.

---

## 6. When something fails

A failure is a record, never a silent retry to green.

- `verify.ps1` non-zero → the branch stays, the output is the feedback. The
  Engineer Agent feeds it back to the model within its existing bounded budget
  and retries. When the budget is exhausted it **refuses**, which is the
  existing house rule and the correct outcome.
- A Hermes cron run fails → `hermes cron incidents` holds it. That is worth
  a weekly read; it is the only place a repeatedly-failing unattended job
  becomes visible.
- The gate itself cannot run → treat as failure, never as a pass. The script
  exits 1 when the guard is unreachable rather than skipping it, because a
  check that quietly disappears is worse than one that fails loudly. This is
  the same lesson as the Hermes installer, which exits 0 while printing
  `[X] Installation failed`.

---

## 7. What this does not protect against

Stated plainly, because a gate that is believed to cover more than it does is
worse than a smaller one that is understood.

- **Hermes has desktop control** (`cua-driver`, installed by choice). Nothing
  here constrains what it does outside git. The blast radius above is about
  repositories, not about the machine.
- **The guard is not a sandbox.** It reads tokens. It stops the specific
  untyped routes that were measured — `game`, `script`, `_G`, `loadstring`,
  asset-id requires — and it will not stop a genuinely novel one. When a new
  hole is found, it goes in the guard with a test, the way `script` did.
- **`luau-lsp` does not run the code.** Everything here is static. A design
  that type-checks can still be a bad game.
- **No CI server exists.** The gate runs on this machine. If the machine is
  wrong, the gate is wrong.

---

## 8. Starting order

1. Refresh the service cache: `python scripts/refresh_roblox_services.py`
   (212 services at the time of writing). Without it the Services module cannot
   be checked for invented service names, and the gate says so.
2. Confirm the gate is green on a clean trunk. It is now. A gate that is red
   on arrival teaches everyone to ignore it.
3. Give Hermes one narrow, reversible, scheduled job and read the result
   before widening. `hermes cron` with a single task on a `hermes/` branch.
4. Only then let it run unattended.
