# Roblox Senior Engineering Codex

This codex specifies the mandatory architectural patterns, language rules, and verification requirements for production Luau code written by autonomous agents in this codebase.

---

## 1. The Five Hard Rules (Gated by Python Token Guard)

These rules are enforced at the token level by `app/engineer/luau_guard.py` before any code is ever executed or passed to the Roblox toolchain.

1. **Strict Type Mode (`--!strict`)**
   - The very first line of every `.luau` file must be exactly `--!strict`.
   - Non-strict or ambiguous typing is rejected immediately.

2. **Absolute Ban on Untyped Globals**
   - **Never** write: `game`, `Game`, `workspace`, `Workspace`, `_G`, `shared`, `getfenv`, `setfenv`, or `loadstring`.
   - Even in string literals or interpolated expressions, these tokens are prohibited.
   - All Roblox engine services must be accessed through the auto-generated `Services.luau` module.

3. **Restricted `script` Identifier**
   - The token `script` is forbidden everywhere **except** inside `require(...)` paths (e.g., `require(script.Parent.X)`).
   - Never write `script.Parent:FindFirstChild(...)` or assign `local s = script`.

4. **DataModel Access via Services Cast**
   - Methods that live exclusively on the `DataModel` (such as `BindToClose`) must be accessed via `Services.DataModel:BindToClose(fn)`.
   - `DataModel` is cast to `game :: DataModel` in `Services.luau`.

5. **Rojo Sourcemap Alignment**
   - Modules must be resolved by walking `script.Parent` according to `default.project.json`:
     - From `src/server/X.luau` to `src/shared/Services.luau`:
       `require(script.Parent.Parent.Parent.ReplicatedStorage.Shared.Services)`
     - Jumping incorrectly across the virtual instance hierarchy causes `luau-lsp` to fail with `TypeError: Unknown require`.

---

## 2. Selene Linting Standards (Zero-Tolerance Warnings)

In this toolchain, **warnings count as failures**. Any warning causes `verify.ps1` to exit with code 1.

1. **Unused Variables and Parameters (`unused_variable`)**
   - Any variable or argument declared must be read.
   - If an interface requires an argument that is not used in the function body, prefix it with an underscore:
     ```luau
     function Service:Release(_player: Player) -- Correct
     function Service:Release(player: Player)  -- FAILS LINT if player is not read
     ```
2. **Manual Table Cloning (`manual_table_clone`)**
   - Never write manual `for k, v in pairs(tbl)` loops to shallow copy a table.
   - Always use `table.clone(tbl)`.
3. **Lexical Scoping & Undefined Variables (`undefined_variable`)**
   - Never declare a `local` variable inside an inner block (such as a `while` or `if` branch) and attempt to reference it outside that block.
   - Declare variables before the block if their state must persist afterwards:
     ```luau
     local lastError: any = nil
     while attempt < MAX_RETRIES do
         local success, result = pcall(...)
         if success then break end
         lastError = result
     end
     if lastError then warn(lastError) end
     ```

---

## 3. Distributed State & DataStore Laws

1. **Single Atomic Read & Claim (`UpdateAsync`)**
   - Do **not** call `UpdateAsync` to set a lock and then immediately call `GetAsync` to read the data. This doubles API cost and creates race conditions.
   - `UpdateAsync` transforms and returns the record in a single atomic transaction. Use the returned record directly.
2. **Detecting Aborted Writes (`return nil`)**
   - When another server holds an active session lock, returning `nil` inside `UpdateAsync` cancels the write.
   - `pcall` returns `true` even when `UpdateAsync` cancels.
   - **Rule**: Check if the result of `UpdateAsync` is `nil`. If `nil`, the lock was **not** acquired.
3. **Preventing Data Loss on Load Failure**
   - If a player's load fails or is locked by another server, **do not cache the fallback profile in memory**.
   - In `Save()`, verify that this server actually claimed the profile before writing:
     ```luau
     if loadedProfiles[userId] == nil then
         warn("Refusing to save: this server never claimed the profile")
         return
     end
     ```
   - This prevents overwriting a player's real progression with an empty default profile.
4. **Server Identity**
   - Live servers have `DataModel.JobId`. Local Studio test environments have `DataModel.JobId == ""`.
   - Always resolve server identity with a non-empty fallback:
     ```luau
     local function serverId(): string
         local id = DataModel.JobId
         return if id ~= "" then id else "studio"
     end
     ```
5. **Dead Lock Reclamation**
   - If `(now - lock.timestamp) >= LOCK_TIMEOUT_SECONDS`, the previous server session died or crashed.
   - Reclaim the lock by replacing `jobId` with this server's identity and refreshing the timestamp.
6. **Clean Session Teardown**
   - Save the latest snapshot and clear `sessionLock = nil` in `Release()`.
   - Wire `Players.PlayerRemoving` and `DataModel:BindToClose` to invoke `Release()`.
   - Drop the in-memory cache **after** the DataStore write completes.

---

## 4. Code Generation Rules for LLMs

1. **Comment Integrity**
   - Every comment line in Luau must start with `-- `.
   - Multi-line comments must be wrapped in `--[[ ... ]]`.
   - Never output raw English sentences outside comment blocks.
2. **Luau Type Declarations**
   - Types must use the `type` or `export type` keywords:
     ```luau
     export type Profile = {
         unlockedModules: { string },
         resources: { [string]: number },
         completedCures: { string },
     }
     ```
   - Never declare types as table variables (`local Profile = { ... }`).
3. **Response Schema**
   - Output must strictly follow the JSON structure:
     ```json
     {
       "files": [
         {
           "path": "src/server/DataService.luau",
           "content": "--!strict\n..."
         }
       ],
       "services": ["DataStoreService", "Players", "DataModel"],
       "summary": "Explanation of changes"
     }
     ```
