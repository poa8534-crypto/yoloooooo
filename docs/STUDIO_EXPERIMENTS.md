# Studio experiments A–H

Eight questions about what a plugin can actually do. **None of them can be
answered from this repository**: they need a running Roblox Studio, and nothing
in the automated pipeline can open one. Everything below is a checklist for a
person at the machine, with the exact thing to look at and a place to write
what happened.

Do not fill these in from documentation. The documentation says
`ScriptEditorService:UpdateSourceAsync` exists; it does not say what happens
when the script is not open, and that is the question.

## Setup

```bash
python -m app.bridge.run
```

It prints a pairing token. Then in Roblox Studio:

1. Copy `plugin/VentureEngineer.server.luau` into your local Plugins folder
   (Studio: **Plugins → Plugins Folder**, then restart Studio).
2. **Plugins → Venture Engineer** opens the panel.
3. Paste the token, press **Connect**.
4. Check the bridge sees it:

```bash
python -m app.bridge.cli status --token <TOKEN>
```

`plugin_connected` must be `true` before any experiment below means anything.

Then send the deterministic build:

```bash
python -m app.bridge.cli smoke --token <TOKEN>
```

---

## A. Can the plugin create Script, LocalScript and ModuleScript?

**How to tell:** after `smoke`, look in the Explorer for
`ReplicatedStorage/GeneratedTest/Config` (ModuleScript) and
`ServerScriptService/GeneratedTestRunner` (Script).

LocalScript is not in the smoke batch; add one by hand later if A passes for
the other two.

**Result:** _not yet run_

---

## B. Does `ScriptEditorService:UpdateSourceAsync` reliably set complete source?

**How to tell:** open `GeneratedTestRunner`. Its last line must be the closing
`end)` of the `Heartbeat` connection, and `Config` must contain `bobHeight = 6`.
Truncation is the failure to look for, not absence.

**Result:** _not yet run_

---

## C. Does it work when the script is **not** open in the editor?

The interesting one. The plugin creates the script and writes to it in the same
breath, so nothing has opened it.

**How to tell:** B passing *is* C passing, because the smoke batch never opens
`Config` — only `GeneratedTestRunner` gets an `open_script`. So: does `Config`
have its source?

**Result:** _not yet run_

---

## D. What exactly does Studio ask for, to let a plugin reach localhost?

**How to tell:** the first time the plugin calls the bridge, Studio should
prompt about the plugin making HTTP requests. Record the exact wording, and
whether the experience-wide **Allow HTTP Requests** game setting also has to be
on — these are two different permissions and it matters which one this needs.

If the plugin panel shows `Http requests are not enabled`, that is the game
setting, not the plugin permission.

**Result:** _not yet run_

---

## E. Can `StudioTestService:ExecutePlayModeAsync` be called from this plugin?

**How to tell:** the last operation in the smoke batch is
`start_playtest` with `mode = "play"`. Does Studio enter Play mode on its own?

If it errors, the plugin reports the message rather than falling back silently:

```bash
python -m app.bridge.cli result --token <TOKEN> <BATCH_ID>
```

**Result:** _not yet run_

---

## F. What happens to the plugin and its localhost connection when Play starts?

The question that decides the shape of everything downstream. If the plugin
stops talking to the bridge during Play mode, the repair loop cannot read
runtime errors live and has to collect them after the test ends.

**How to tell:** with Play mode running, in another terminal:

```bash
python -m app.bridge.cli status --token <TOKEN>
```

Is `plugin_connected` still `true` more than 15 seconds into the test? Does
`studio.mode` change from `edit`?

**Result:** _not yet run_

---

## G. Can the plugin keep talking to the bridge while a playtest is active?

**How to tell:** while Play mode is running, check whether `print` output and
any errors reach the bridge:

```bash
python -m app.bridge.cli errors --token <TOKEN>
```

The runner prints `[GeneratedTest] runner started; the build is executing`. That
is an info message, not an error, so it will NOT appear here — only errors and
warnings are forwarded. To test G properly, break something on purpose: edit
`GeneratedTestRunner` in Studio to index a nil value, play again, and see
whether the error arrives.

**Result:** _not yet run_

---

## H. Can the plugin stop the test programmatically?

**How to tell:** there is no CLI command for this yet. Queue a `stop_playtest`
batch by hand, or add one; the plugin's handler calls `RunService:Stop()`.

**Result:** _not yet run_

---

## What the smoke build should look like when it works

1. `Workspace/GeneratedTest/Floor` — large grey Part
2. `Workspace/GeneratedTest/RedBlock` — red Neon Part
3. `Workspace/GeneratedTest/Spawn` — SpawnLocation
4. `ReplicatedStorage/GeneratedTest/Config` — ModuleScript mentioning `bobHeight`
5. `ServerScriptService/GeneratedTestRunner` — Script mentioning `Heartbeat`
6. `GeneratedTestRunner` opened in the script editor
7. Studio enters Play mode
8. Output shows `[GeneratedTest] runner started; the build is executing`
9. **RedBlock moves up and down** — the one that proves the place is running the
   build rather than merely containing it

Then:

```bash
python -m app.bridge.cli result --token <TOKEN> <BATCH_ID>
```

Every operation should read `OK`. Anything reading `FAIL` carries the reason
the plugin gave, which is what to paste back.
