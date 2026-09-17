# Rules for the Roblox Engineer

The rules the model is held to, and why each one exists. Every rule below was
added because something failed, and the failure is named. A rule with no
observed failure behind it is a guess, and guesses in a system prompt cost
context without buying reliability.

**The enforced copy is `SYSTEM` in `app/engineer/prompts.py`.** That is what the
model is actually sent. This file is the reasoning behind it: when a rule here
and a rule there disagree, the code is right and this file is stale.

**Rules are not what makes the output safe. The gate is.** A rule the model
ignores still fails `scripts/verify.ps1`. Rules exist so the model aims at the
checks instead of discovering them one refused attempt at a time.

---

## 1. The hard rules (the guard refuses these)

| Rule | Why |
|---|---|
| First line is exactly `--!strict` | Without it luau-lsp checks almost nothing. A BOM in front of it has already silently disabled strict mode in this project. |
| Never write `game`, `Game`, `workspace`, `Workspace` | Measured: `game:TotallyMadeUpMethod()` draws **no** luau-lsp diagnostic. `game` is a sourcemap node, not a typed value, so every mistake through it is invisible. |
| Never write `_G`, `shared`, `getfenv`, `setfenv`, `loadstring` | Each is a route back to the untyped environment the rules above close. |
| Never `require(<number>)` | An asset id is code from outside the repository that no check can see. |
| Never write `script` except inside `require(...)` | Same blind spot: `script.Parent:MadeUp()` type-checks clean. `require(script.Parent.X)` is exempt because it is the only route to the Services module. |
| Reach services through the generated `src/shared/Services.luau` | It casts each service to its concrete type, which is what makes the type checker able to see anything at all. Members on `Services.Players` *are* checked. |
| Never write the Services module yourself | It is generated and compared byte for byte. List what you need in `services` instead. |
| Ask for `DataModel` like a service for `BindToClose` | `BindToClose` exists nowhere else. Six attempts of a real run died writing `game:BindToClose(...)` — obeying the DataStore rule by breaking the `game` rule. The module now exposes `DataModel = game :: DataModel`, verified to type-check real methods and reject invented ones. |
| Write only under `src/server` and `src/shared` | No client require form has been verified to be both type-checked and correct at run time, and the checks cannot see the difference. |

## 2. The lint rules (selene; a warning fails the run like an error)

| Rule | Why |
|---|---|
| Never leave a parameter or local unused; prefix with `_` if a signature needs it | **This is the single biggest cause of refusal.** Four of six attempts on the first real run, and all six on another, died on nothing else. |
| Never shadow a name you still need | |
| Never assign a variable you never read | |

## 3. Writing comments

| Rule | Why |
|---|---|
| Every line of a comment needs its own `--`; prefer `--[[ ]]` past one line | A run was refused three attempts running because two lines of a copied file lost their `--` and became Luau: `treated as dead and taken, or the player could never rejoin.` |

## 4. The answer

| Rule | Why |
|---|---|
| A single JSON object, nothing else | |
| Complete content of every file you change | Each attempt starts from the unchanged project; there is no patch format yet. |
| **Only** the files you are changing | A file left out keeps its content exactly. Returning an unchanged file cannot improve it and can only corrupt it — which is precisely how the run above was lost. |

## 5. Engineering standards (not mechanically checked — read them)

- Strict types everywhere; export types for shared data.
- Modern APIs only: `task.wait` / `task.spawn` / `task.delay` / `task.defer`. No
  `wait`/`spawn`/`delay`, no `BodyVelocity`/`BodyPosition`/`BodyGyro`.
- Never invent an API. If unsure a member exists, do without it.
- The server is authoritative. Every RemoteEvent/RemoteFunction handler validates
  types and ranges, rate-limits per player, and checks the player may act. The
  client never decides damage, currency, inventory, position or cooldowns.
- DataStores: pcall with bounded retries and backoff, `UpdateAsync` for writes,
  session locks against double-joins, save on `PlayerRemoving` and in
  `BindToClose`, and **never overwrite a profile that failed to load**.
- Disconnect connections and destroy instances when players leave.
- No per-frame allocation in hot paths; no polling where an event exists.
- Small modules, one responsibility each; a server entry script wires them up.

---

## What the rules cannot fix

Two failure modes have now been measured that no amount of instruction has
corrected, because they are not knowledge gaps:

1. **Unused locals.** The rule is stated, with an example, in the prompt the
   model receives every attempt. It still forgets, run after run. A rule the
   model cannot reliably follow is a candidate for automation — formatting is
   already done for it by StyLua rather than asked of it.

2. **Copy fidelity.** Asked to return a 249-line file, the model returned 246
   lines byte-identical and dropped a token from three of them. The types, the
   session lock, the retries and `BindToClose` were all correct. No Luau
   training corpus addresses "drops a token when copying"; only a smaller ask
   does, which is what rule 4 now makes explicit.

Both are recorded as training examples by `app/engineer/capture.py`, which is
the other answer: teach the model out of its own refusals rather than only
telling it again.
