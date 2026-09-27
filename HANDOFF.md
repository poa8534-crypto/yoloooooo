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

## 4a. Starting a new game

One repository is one game, and a build refuses a repository that already
holds another blueprint's systems (it answers the Build button with a 409
naming that game). So a new game gets its own:

```bash
python scripts/new_game.py neuromine
```

It copies `templates/game` (the toolchain, owned by this repository), names
the Rojo project after the game, copies the two generated type files from the
current game, makes one commit, and runs the new repository's own
`verify.ps1`, which must pass before the first build. Then set
`GAME_PROJECT_DIR` in `.env` (the owner's file), restart the dashboard, and run
`start-agents.bat` so Rojo serves the new project.

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

## 5a. When the provider runs out: building by hand

When Antigravity answers "Individual quota reached", a build does not stop. It
walks the rest of the plan and refuses each system in about twenty seconds.
`scripts/handbuild.py` is the way to carry on without it. Use it **only when
the owner asks**: the rule is still that the agents write the game.

```bash
python scripts/handbuild.py status                # what is left, and what blocks each
python scripts/handbuild.py brief <System>        # the Engineer's own task, verbatim
python scripts/handbuild.py open <System>         # a worktree cut from master
python scripts/handbuild.py check <System>        # format, then the six checks
python scripts/handbuild.py land <System>         # onto master, project verified
```

- `open` makes `<slug>-handbuilt-<hex>` under the game's `-worktrees` folder.
  Cut it only after everything the system depends on has landed, or it is
  written against code that is gone.
- `check` formats every Luau file the worktree changed, not only the system's
  own. A data row added to `ColonyConfig` for a system is landed with it, so
  it has to pass the format check too.
- `land` lands every `.luau` file the worktree gained under `src/` or `tests/`,
  so the system's behaviour spec lands with it. The Services modules are
  regenerated for the whole project rather than taken from the worktree.
- A spec is `tests/<System>.spec.luau`. It runs under Lune through
  `tests/harness.luau`, where `task.wait` returns at once and `task.spawn` runs
  synchronously, so a spec must never call a `Start()` that loops. Pass time
  and randomness in as arguments, and test the methods.
- Prove each spec by breaking the code it guards and watching it fail with the
  right message, then restoring. A spec that has never failed has not been
  shown to check anything.

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
- **luau-lsp infers a module's `self` from the body of each method**, so a
  method that calls `self:Other()` can pass type-checking in its own module and
  fail when a sibling calls it: "Expected this to be exactly 'X', but got
  'Folder?'". There are two fixes, depending on whose file it is.
  - In the module that owns the method, annotate the local that holds the
    result: `local descent: Descent? = self:Active(player)`.
  - In a caller, go through a cast and check the answer instead of trusting
    it: `local found = (ColonyWorldService :: any):GetSpawnLocation()`, then
    `typeof(found) == "Instance" and found:IsA(...)`. SpawnManager, IncubationService and
    ServerEventService all do this.
- **A player's state must be written to their profile as it changes.**
  SaveDataService saves the profile when a player leaves, before any later
  system's leaving handler runs. So state that is held in memory and written
  out "on leave" never reaches the save. Keep each system's state under its
  own profile field and write it through `SaveDataService:SetField` whenever
  it changes.
- **Iterating `x or {}` fails luau-lsp** ("Cannot call a value of type
  {T} in union"). Check for nil first, or annotate the local (`local here:
  { Player } = players or Services.Players:GetPlayers()`). This stopped four
  Island Haven systems at the gate once each.
- **A build works in one repository, resolved once.** `resolve_game_repo`
  finds a game's repository from its blueprint title
  (`C:\RobloxGames\<title-slug>`), falling back to `GAME_PROJECT_DIR`. The
  build passes that repository to every stage: the Engineer's worktrees, the
  gate, landing and the Studio sync. On 2026-09-19 only landing used it, so
  the Engineer wrote 11 Island Haven systems on branches of neuromine, and
  landing found nothing on those branches in island-haven.
  `tests/test_parallel_build.py` now fails if any stage is handed a different
  repository.

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
- `C:\RobloxGames\neuromine` is the current game, NeuroMine: Symbiotic
  Extraction (blueprint `452bb7a75e7ef765`, specification revision 38). All
  **51 of 51 systems are on master**.
  - The Engineer wrote six of them before Antigravity's quota ran out. The
    other forty-five were written by hand through `scripts/handbuild.py`, at
    the owner's request, over two sessions ending 2026-09-19.
  - Measured on master at that date: `verify.ps1` exits 0, with all six checks
    passing, 0 selene warnings and 442 behaviour checks.
  - Synced to Studio and playtested via `scripts/handbuild.py sync`:
    - 45 server modules loaded and started cleanly (`[VentureBootstrap]`).
    - 4 client modules loaded and started cleanly (`[VentureClientBootstrap]`).
      (`Services.luau` is a locator table with no `Start()` method).
    - World instantiated via `ColonyWorldService:Start()`.
    - Measured from active Studio log: 0 errors, 4 DataStore warnings
      (expected in Studio test mode).
  - Place files `.gitignore`: `templates/game/.gitignore` and neuromine now
    ignore `*.rbxl` and `*.rbxlx`.
  - Section 9 lists what these systems record that nothing reads yet.
- `C:\RobloxGames\island-haven` is the newest game. On 2026-09-21 the owner
  redesigned it as **"Fish a House"** (fish for blueprints, chop wood, build
  a free house, earn Shells), and it has been hand-built by Claude at the
  owner's request since then. The original Island Haven systems are kept in
  `archive/`. It pushes to the private GitHub repo `poa8534-crypto/fish-a-house`.
  - Since 2026-09-24 the owner wants Claude to hand coding work to Antigravity
    itself (never to relay briefs through the owner) and report only when a
    plan is done and play-tested. `scripts/agy_edit.py` does that the
    pipeline's safe way: `agy` gets the files in the prompt with no tools,
    answers with exact search/replace edits, the script applies them, and
    Claude runs `verify.ps1` in a worktree and feeds failures back. Headless
    `agy` with tools is refused file reads without permission rules, so this is
    the working route. Each change is play-tested in Studio before merging.
  - Shipped 2026-09-24: the Battle Pass and Tool Masteries (after fixing five
    gaps in Antigravity's first version), and the Bottle Run
    (`docs/plans/bottle-run.md`): fished blueprints are carried home as timed
    bottles, knocked loose by night sharks, day seagulls and, in the Danger
    Deep only, other players' tackles; a plank run (`tools/world/BuildPlankRun.luau`)
    crosses the channel to Volcano Island. `ServerStorage.DevTools` gained
    `Fire` (any GameEvents signal) and `GiveBottle` for Studio tests.
    Not yet tested: tackles, which need two or more players.
  - Shipped 2026-09-25, the Living World (`docs/plans/living-world.md`):
    8 plots (two new neighbourhoods grown onto the island by
    `tools/world/BuildNeighbourhoods.luau`, land only ever added), optional
    roommates, a waiting list when plots are full, the mystery bottle reveal,
    a volcano scaled up for 8 players (`BuildVolcanoIsland.luau`), crabs,
    seagull flocks, fireflies, jellyfish and night lamps, thunderstorms and
    tsunamis (`DevTools.StartStorm` / `StartTsunami` test them), hidden
    treasure chests and whirlpools (`BuildSeaLife.luau`). World scripts are
    run in Studio by syncing them through Rojo as a temporary untracked
    ModuleScript and requiring it in Edit mode. Backups of the island,
    volcano, Danger Deep and terrain from before are in
    ServerStorage.Backups/*_2026-09-25.
  - Built 2026-09-26, on branch `feat/polish-expansion` (stacked on
    `feat/fishing-v2`; 20 commits past `master`, which still holds `ccd8fef`
    and is not pushed). Neither branch is merged: merging fishing-v2, then
    polish-expansion, and pushing is the owner's call. Gate: 142 behaviour
    checks, all passing. Everything below was play-tested in Studio, with the
    owner's save kept safe by `DevTools.Snapshot` / `Restore`:
    - Fishing v2: charge, cast, bite, catch bar, catch card; Base/Shop buttons.
      Fixed on review: the `Bite` remote was made lazily, which deadlocked
      the client, and TackleService was started twice.
      `tests/Startup.spec.luau` now catches both.
    - Chopping: all 273 Home Island trees are tagged `ChopTree`
      (`tools/world/MakeTreesChoppable.luau`). The swing is whole-body; the
      tree falls away from the player; logs fly to them. Three swings give +3 Wood.
    - The guide pillar shows only during the first-house guide.
    - The plan (`docs/plans/polish-and-expansion.md` section 6):
      - The spawn is in the Town Square, and new players get an intro sweep.
      - The Volcano Heist (`VolcanoHeistService`, altars from
        `BuildHeistAltars.luau`) has a guardian chase and bombs.
      - NpcLife: NPCs breathe, turn their heads, wave, float an icon, and
        chatter with a speech bubble and a camera zoom.
      - `BuildTown.luau` dresses and places 4 new NPCs.
      - The forest understory comes from `BuildForestDetail.luau`, and the
        Blue Mansion grounds and furnishings from `BuildMansion.luau`.
      - Lighting uses the plan's values. Juice: button springs, the catch
        pop, the delivery shake, 7 soundscapes.
    - NPCs in a StreamingEnabled place need `ModelStreamingMode = Atomic`, or
      a client can see a model before its Head arrives and NpcLife skips it.
    - Owner bug round, same day (`b714b5e`), each fix play-tested:
      - The white line by the dock had two causes:
        - The guide's straight `GuideBeam`. It is gone.
        - Barnaby's own fishing line. Its `RodShaft.RodTip` attachment
          held a world position in its local Position, so the line ran
          about 300 studs into the sky. It was fixed in the place, in Edit
          mode, to the top of the rod. That attachment is place-only, not
          in the repo.
      - The heist altar labels were a fixed-pixel banner, drawn on top of
        everything and visible from the whole map. They now show only
        within `HEIST_LABEL_DISTANCE` (45) (`16f6969`).
      - A heist bottle knocked loose now waits 5 s on the ground. Anyone
        with a plot and free hands can pick it up. If it is untouched, it
        rolls back to its altar. This is `BottleService`'s general
        `BottleOrigin` hook.
      - The volcano's bare rock (Terrain, between y=30 and the rim) slides
        players downhill (`VolcanoSlide.client`, `GameConfig.VOLCANO_SLIDE`),
        so the obby is the only way up.
      - Chopping reaches a tree from anywhere under its canopy
        (`CHOP_REACH_EDGE`). Canopies are up to 67 studs wide, and the old
        16-stud trunk reach said "Get closer".
    - Owner round 3 (2026-09-26): commits `a8be062` and `3d60f96`, gate at
      165 checks.
      - 24 rods, owned as a set. They are sold only at rod shops:
        MarketStall_3 in town, and Pip's stall for the 4 fire rods.
      - Barnaby now sells bait and hands out the daily quests
        (BaitService, Barnaby.client).
      - Sail Away shows from a Castle; the Star Storm comes every 10 min.
      - The world changes are in BuildTown and BuildPlaza: the fish
        fountain, the sell stall, the Rod Shop sign, the Crystal board,
        Flora, Finn and Barnaby's facing.
      - Rod and koi meshes: `blender/rods/out/Roblox_Import_Rods_and_Fountain.fbx`
        and its .json sidecar, which holds the colours, the glow, and the
        mouth position for the jet. After the owner imports the FBX in
        Studio, still to do:
        - move the rods into ReplicatedStorage.RodModels/<id>, with the
          pivot at the handle's bottom and scaled to about 10 studs, and
          colour them from the sidecar;
        - swap the part-built fish in the fountain for the FishStatue.
      - Not yet play-tested: Barnaby's window after the daily-reward popup
        is closed, bait buy/use, quest take/claim, and the hidden boat.
        The last Play session got a DataStore "AccessForbidden" error and
        ran unsaved; check Game Settings > Security > Studio API access.
    - Not done:
      - The new seagull mesh needs the owner to turn on EditableMesh in
        Game Settings > Security.
      - Material footsteps: no licensed audio was found.
      - The trophy case has no leaderboard names.
      - The four new NPCs only talk and sell nothing; that is a design call.
      - The plank run does not set the `Island` attribute.
  - 2026-09-27, branch `feat/codex-pass-and-ui` (not merged, not pushed):
    - UI: one big window at a time (`Windows.luau`, and Popups' queue waits
      for it), a bottom-left player card (level, XP, health), Free Gifts
      rebuilt as a top-right card, Base/Shop lifted into Roblox's top bar.
    - Fishing: the cast line leaves the real rod tip (measured from the
      rods' vertices in `Fishing rods.blend`; Roblox X is Blender X
      mirrored), the reel game hides the fish until it is landed, a new
      power gauge with a drawn rod badge, a hologram "!" with a "toing!"
      (rbxassetid://2772396665). The bite reaction window is 1.6-2.2 s:
      Codex's 0.75-1.25 s was too short to click, so the reel game "never
      came". Codex's own restore of the authored rod and reel game is
      commit `8cbd28e`, committed as found.
    - World 1 "The Five Tides" (`00b6ddd`): `tools/world/BuildArchipelago.luau`
      removed the plank run, moved Volcano Island (Ember Isle) from z 960
      to z 2400 with its terrain, and built Frostfall, Wildtide Jungle,
      Tempest Peaks and Starfall Sanctuary on a 2400-stud ring, each with
      a ferry dock, story NPC anchors and quest markers. The place streams,
      so far terrain never reaches a client: each island has a Persistent
      low-poly `Landmark` (and mist) that is what you see from afar.
      Backups: `ServerStorage.Backups.Archipelago_20260927`.
    - The story is data (`StoryConfig`), pure rules (`StoryRules`,
      `tests/Story.spec.luau`), `StoryService`/`StoryNpcs` on the server
      and `Story.client` on screen. It replaced FinnStoryService. 8 main
      and 8 side quests; the new islands open for the ferry as main quests
      finish. The boss battles (Magmajaw etc.), the Worldline Rod, the
      Tide Beacon and the Worldfin are not built: the main story ends,
      for now, with Astra naming the five guardians.
    - With the plank run gone, the Bottle Run and the Volcano Heist have
      no walking route home from the volcano; the owner's team is making
      boats. The heist and the danger zones still work by position.
    - Studio facts learned: deleting a Rojo-synced instance by hand in
      Studio disconnects the Rojo plugin (the owner has to press Connect
      again), and while it is off, changes can be pushed with the Studio
      MCP (`multi_edit`, or `ScriptEditorService:UpdateSourceAsync` when
      the script is open in an editor tab, or the tab writes the old text
      back). The Studio `screen_capture` tool does not draw BillboardGuis.
    - Island design round (`bc4916a`, fixes `3bc2239`): the owner's island
      brief turned each island into zones, built by one module per island
      under `tools/world/Archipelago/` (shared helpers in `Kit.luau`; run
      `BuildArchipelago` with its `Only` attribute set to, say, "Frost" to
      rebuild one island).
      - The zones: Ember's Blackshore Dock, Lava Village and Magma Caverns;
        Frostfall's harbour, ice-fishing plains, frozen falls, ice cave and
        Glacier Cavern; Wildtide's mangroves, Giant Tree, ruins and Rootback
        Basin; Tempest's Shipwreck Bay, rope bridges, floating rocks and
        Storm Tower; Starfall's Moonshore, cosmic pools, Floating Sanctuary,
        Meteor Valley and aurora.
      - Every boss has an empty arena part tagged `BossArena` (attributes
        `Boss`, `Island`). The fights, eruptions, wind gusts, low gravity,
        the Star Storm and island-only fish are not built.
      - Sea mist: `Archipelago.SeaMist`, 88 fog banks with gaps the islands
        show through. `MistPrewarm.client` fills every emitter tagged
        `PrewarmMist` as it streams in; otherwise the banks start empty.
      - Every special water (ice holes, the cavern lakes, the root pools,
        the sky pond, the star pools) was cast into in Play and reached
        WaitingForBite.
    - Boss fights, the Five Guardians (`docs/plans/boss-fights.md`, merge
      `9a13ef5`): written by Antigravity through `scripts/agy_edit.py`.
      There were three parts, dispatched in parallel, with 2-3 review rounds
      each. Claude reviewed and gated them, and built the world side
      (`ee09bad`).
      - A cast hooked in a `BossArena` becomes a fight when the story's
        current step is `{ kind = "boss" }`, or when a won boss's cooldown
        has passed.
      - `BossRules` is pure and seeded. `tests/Boss.spec.luau` replays
        fights with a scripted player.
      - `BossService` refuses wins faster than `BossRules.minSeconds`.
      - The first win gives the island's Guardian Rod, a Tide Compass
        fragment (the compass shows on the story board) and moves the
        tide quest on.
      - `DevTools.BossReady:Invoke(player, bossId)` sets the story up for
        a test.
      - Play-tested:
        - Magmajaw: the cast, the hook and the fight screen; holding Space
          snaps the line; a win in 3 s is refused; a win after the minimum
          pays 2500 shells and 25 gems, gives the Inferno Rod (the model
          with its aura) and the 🔥 fragment, and moves EmberTide on.
        - The Stormray fight starts from the new cliff ledge.
        - The owner's save was restored after (Snapshot/Restore).
      - Not yet play-tested:
        - the Glacier Serpent, Rootback and Astral Whale fights in Studio
          (the spec plays all five);
        - touch and gamepad controls;
        - two players at one arena.
      - Review found, and Antigravity fixed: the fight never reported its
        end (BossRules sets the outcome itself), and a thumbstick bound
        twice. Claude fixed `type(x) == "CFrame"` (always false; `typeof`).
      - Also: steering the wrong way costs tension only while reeling, so
        letting go is always safe apart from a warned spike.
    - More Studio facts:
      - A folder deleted from disk that Rojo synced while connected may
        still stay in Studio (`ReplicatedStorage.Shared._Tools5`); delete
        it by hand only if the owner can reconnect Rojo afterwards.
      - A raycast straight after a terrain write can still see the old
        ground. Read the voxels instead (`kit.terrainTop`).
      - `CanQuery = false` only takes effect when `CanCollide` is false too.
      - A Play-mode character anchored from the client stops replicating
        its position, so the server rejects its casts as too far away.
      - In Play, Studio's automatic graphics quality cuts view distance, so
        far islands look missing in screenshots. Judge far views in Edit.
      - The right-click camera worked in a fresh session. It broke only in
        a session where camera tests had been run over MCP.
  - The Studio place is "Team Power rangers" (placeId 105486459429267), a
    Team Create place. Rojo serves the repo on localhost:34872. Run only
    **one** Rojo server: two at once put every new script in twice.
  - Shipped phases:
    - 1–4: the core loop, per-player trees, the mailbox, Sail Away,
      Adopt-Me-style furnishing, the tide and clams, the Celestial Crystal
      and Cataclysm, and house mutations with gems.
    - 5: a first-house guide, sounds, leaderboards, phone scaling and
      session-locked saves.
    - D: a daily login streak and daily quests.
    - B: Volcano Island (commit `83ed434`), reached by ferry after one Sail
      Away. It has lava fishing with the Lava Rod, the only place the
      Obsidian Citadel blueprint bites, and villager shops run by Pip,
      Marigold and Bramble.
    - C: the Gem shop, opened from a HUD button. It sells rod skins, house
      paint and name tags for Gems, plus four gem packs for Robux.
  - The gem packs are Developer Products. They stay "Coming soon" until the
    owner creates them on the Creator Dashboard and puts each product id
    into `GEM_PACKS` in `src/shared/GameConfig.luau`.
  - The pieces that live only in the Studio place, not in the repo:
    - the terrain, trees and sky;
    - Workspace.VolcanoIsland;
    - ServerStorage.Ferry, the ferry boat;
    - ServerStorage.TreeKit;
    - the villager models, in ServerStorage.LaterIslands.
  - Measured on master at `83ed434`: `scripts/verify.ps1` exits 0, with 69
    behaviour checks. Phase B was play-tested in Studio:
    - the ferry refused a player with no voyages, and both crossings worked;
    - lava fishing refused the Twig Rod and caught lava fish with the Lava
      Rod;
    - all three shops answered.
    The owner's save was put back afterwards through
    `ServerStorage.DevTools.SetData`, a Studio-only hook.
  - Not yet done: a 4-player Clients-and-Servers test.
  - `GAME_PROJECT_DIR` in `.env` still names neuromine.
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
- **NeuroMine's loose ends.** These come from the last seven hand-built
  systems, whose headers say what they record that nothing reads yet. On
  2026-09-19 master was checked for each one. No module requires
  EvolutionService, SpecializationService, BrainrotIndexService or
  ServerEventService, and nothing in `src/client` names any of the remotes
  below. Each one is a design decision for the owner's team before it is code:
  - No system reads these yet:
    - EvolutionService's `modelScale` and `quirkSlots`.
    - SpecializationService's `Multiplier()`.
    - BrainrotIndexService's `Bonus()`.
    - ServerEventService's `Modifier()`. During a golden rush, MiningService
      pays the same gold as at any other time.
  - Nothing calls EvolutionService's `AddExperience` or `RecordMeal`, so
    companions gain no XP from play.
  - ServerEventService's meteor fragments can be seen but not mined.
    MiningService has no node kind for them, and no loot table says what a
    fragment pays.
  - GenePodService launches a satchel of ore. There are no embryos to rescue,
    because the satchel holds nothing else.
  - There is no client UI for the remotes these systems added:
    - `MetabolismFeed` and `MetabolismClaim`
    - `EvolveCompanion` and `EvolutionProgress`
    - `Rebirth` and `AllocateSpecialization`
    - `LaunchGenePod`
    - `CraftRecipe`
    - `BrainrotIndex`
    - `ServerEventChanged`
- **Island Haven's loose ends.** Checked against master on 2026-09-20:
  - **The 11 systems Antigravity wrote are separate silos.** None of them
    requires another system, and each saves to its own DataStore, 9 stores in
    all. They were written without seeing each other. The 22 later ones are
    built around InventoryService (pockets kept in PlayerDataService's
    profile) and use IslandService, PlayerDataService and ItemCatalog. Nothing
    requires the other 8 Antigravity systems, so several things exist twice:
    - ItemRewardDispatcher keeps its own set of pockets.
    - WorldPropManager, ProximityInteractionService and
      CharacterAnimationService all run flotsam or driftwood gathering of their
      own, beside ForagingService.
    - NPCDialogueService and SanctuaryExhibitionService take donations, beside
      DialogueService and SanctuaryService.
    - SpawnService and PlayerLifecycleManager each build an arrival pier.

    Deciding which of each pair stays is a design call for the owner's team,
    and it should be made before the place is played.
  - Client UI exists only for the pocket bar, prompts, toasts and fishing
    (PlayerHUDView, ToolEquipManager). No client screen uses the remotes for:
    - dialogue
    - the stall
    - crates
    - crafting
    - the merchant
    - the garden
    - the sanctuary
    - the guestbook
  - The totem guestbook lives only as long as the server does.
  - Everything in the world is drawn with plain parts: crates, boats, fish
    shadows, flowers, fireflies.

## 10. House rules

These came from the owner and are not up for reinterpretation:

- Every system built and every line written must be **scalable and flexible, not
  rigid**: extensible by data, never hard-coded.
- **No fake integration.** No simulated progress, no invented logs, no build
  state that was not measured. The 3D/graph views visualise real state or they
  show nothing.
- Design is the owner's team's job. Code correctness is ours.
- Tools are installed only from official sources, and only after asking.
