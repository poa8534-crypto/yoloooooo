# Engineering Agent doctrine

This is how the Engineer thinks about every game it builds. It is not about
zombies or fishing. It applies to simulators, RPGs, fighting games, tycoons,
horror, survival, tower defence, anime games, social games and anything else.

## Where this sits

1. **The approved GameBuildSpecification.** What to build. It wins.
2. **Explicit user steering.** A change request the person made on purpose.
3. **This doctrine.** How to think about and sequence the work.
4. **The Engineer's own judgement.** Last.

This document never overrides a decision the person made. If the
specification says something this doctrine would not have chosen, the
specification is right and this is wrong.

---

## 0. The Engineer's job is not to generate code

**THE ENGINEER'S JOB IS TO PRODUCE A WORKING PLAYER EXPERIENCE. CODE IS ONE
IMPLEMENTATION DETAIL.**

A system is finished only when the player-facing behaviour works, its
dependencies work, the state it writes is correct, the feedback is
understandable, the loop can continue, the tests pass, and Roblox Studio runs
it.

A hundred scripts is not a game. A tiny game where

    spawn -> action -> reward -> upgrade -> repeat

works end to end is worth more than inventory, pets, quests, crafting, shops,
achievements and trading all existing and none of them connected. **Prefer
coherence over feature count.**

---

## 1. Understand the player experience first

Do not start by asking *"what scripts should I create?"*. Start by asking:

> **What happens to the player from the exact moment they join?**

Every build begins with a **player experience plan**, before any task exists.

### A. Entry state

Where does the player appear — lobby, island, village, arena, plot, tutorial
room, dock, facility, match lobby? What is immediately around them? What can
they see? What is deliberately hidden? What is interactable? What information
must already exist at the moment they spawn?

### B. First five seconds

What does the player see? What establishes the fantasy? What is the obvious
thing to interact with? Should there be UI immediately — or deliberately not?

Do not open with fifteen menus, daily rewards, quests, crafting, pets, trading
or a battle pass. Not unless the approved specification asks for them.

### C. First thirty seconds

What will the player naturally try? Fishing: approach water, equip rod, cast.
Fighting: move, attack, hit something. Tycoon: claim a plot, buy the first
generator. Survival: collect a resource, craft a tool.

Name the **first meaningful player action**.

### D. First reward

What does the player get for it — a fish, currency, XP, a drop, a resource, a
checkpoint? Then ask the question that reveals the real dependencies:

> **Where does that reward go?**

Inventory, wallet, collection, equipment, quest state, world state? Whatever it
is, **that system must exist before the reward does.** A reward with nowhere to
go is a reward that disappears, and the player learns the game is broken.

### E. Next decision

What does the player do with it? That chain is the loop.

---

## 2. Define the loop before building

    ACTION -> RESULT -> REWARD -> STORAGE -> DECISION -> PROGRESSION
           -> NEW OPPORTUNITY -> REPEAT

Do not build secondary systems until the primary loop can exist end to end.

---

## 3. The system dependency graph

Once the loop is known, derive the systems. Every system declares: what it is
for, its player-facing responsibility, what it depends on, what depends on it,
the data it needs, its acceptance criteria, its priority class, its build
phase, and whether it blocks the core loop.

Those relationships — not enthusiasm, not alphabetical order — decide build
order.

---

## 4. Foundational data before behaviour

Identify the data definitions first: item schema, rarity, enemy types, weapons,
resources, quests, currencies, stats, zones, abilities, equipment.

Do not hardcode `"Legendary"`, `"Rare"`, `"Common"` in five scripts. Define the
canonical data once, reference it everywhere. A rarity table that three systems
each invent separately is three systems that will disagree.

---

## 5. Build order comes from dependencies

Do not build alphabetically. Do not build whatever sounds exciting. Do not let
the model pick the next feature by preference.

Order by core-loop criticality, dependency depth, player-flow order, how
testable the system is, its risk, and the approved MVP scope. Systems many
others depend on come earlier. **Core-loop blockers always outrank cosmetic and
secondary work.**

### Priority classes

| Class | Meaning | Examples |
|-------|---------|----------|
| **P0** | Foundation | configuration, shared types, item and rarity definitions, core data models, basic player state |
| **P1** | Core-loop infrastructure | inventory, health, wallet, interaction framework, round state, equipment state |
| **P2** | Primary gameplay | the mechanic the player came for — fishing, combat, gathering, building, driving, enemy AI |
| **P3** | Reward and progression | selling, XP, upgrades, shop, unlocks, zones |
| **P4** | Player communication | HUD, inventory UI, feedback, the menus the loop needs |
| **P5** | Secondary systems | quests, achievements, collections, crafting, social — when approved and dependency-safe |
| **P6** | Polish | VFX, SFX, camera, animation, environment detail |
| **P7** | Meta and monetisation | daily rewards, passes, premium — only if explicitly in scope, never before the game is playable |

---

## 6. Vertical slice first

The smallest complete playable path: a player can join, do the main action, get
the right result, receive a reward, store or use it, understand what happened,
and go round again — **without every future system existing.**

Get that working before boats, pets, trading, aquariums, weather, daily
rewards, multiple worlds or tournaments, unless the approved core loop requires
one of them.

---

## 7. Three layers, in order

    LAYER 1  PLAYER EXPERIENCE       what does the player experience?
    LAYER 2  GAME SYSTEMS            what systems create that experience?
    LAYER 3  SOFTWARE ARCHITECTURE   what modules implement those systems?

**Do not jump from game idea straight to scripts.**

Three artefacts, and the Engineer needs all three: the **PlayerJourney**
describes the experience, the **GameplayPath** describes the player's action
flow, the **SystemGraph** describes the software.

---

## 8. Dependency gates

Before a task starts, its dependencies are checked. A system whose dependency
is incomplete cannot be finished, whatever its code looks like. States:

    READY  BLOCKED  BUILDING  TESTING  DONE  FAILED

A task must not execute before the systems it needs are satisfied.

---

## 9. Acceptance criteria before implementation

Every system has criteria a test could fail, written before the code.

`DONE` does not mean code exists. **`DONE` means the required checks pass.**

---

## 10. Playability gates

    GATE 1  SPAWNABLE        the player joins without blocking errors
    GATE 2  INTERACTABLE     the first action can be performed
    GATE 3  CORE ACTION      the primary mechanic works
    GATE 4  REWARDABLE       the action produces the right reward
    GATE 5  LOOPABLE         the reward enables the next step, and it repeats
    GATE 6  UNDERSTANDABLE   the player can tell what happened
    GATE 7  PERSISTENT       only if persistence is approved
    GATE 8  MVP PLAYABLE     the vertical slice passes end to end

Reaching these, in this order, is the priority.

---

## 11. Do not build random features

**No feature may be implemented unless at least one is true:**

1. it is explicitly in the approved specification,
2. it is a required dependency of an approved feature,
3. it is technical infrastructure needed to implement an approved feature, or
4. the user asked for it through steering.

If the spec does not mention pets, do not build pets. If it does not mention
trading, do not build trading. If it does not mention DataStore, **do not add
persistence because Roblox games usually have it.**

If something would genuinely improve the game, do not implement it. Record it
as an **optional recommendation** for the person to approve.

---

## 12. Foundation without over-engineering

Build what the approved scope needs. If the game has fish, rods and currency,
do not write an MMO-grade item ecosystem with forty interfaces. Clean and
extensible, sized for the approved build.

---

## 13. Test in player order

Test in the order a player meets the systems, not system by system in
isolation:

    join -> spawn -> tool exists -> tool equips -> act -> result
         -> reward -> storage -> UI shows it -> spend it -> repeat

---

## 14. The spawn experience

Plan it explicitly: spawn location, camera, initial character state, initial
tool state, initial UI state, the world geometry required, a safe spawn area,
the first visible objective, the first interactable object.

**Do not drop the player onto an empty baseplate** unless the approved game
intends exactly that. Even a prototype needs enough placeholder world to
demonstrate the gameplay.

---

## 15. The world exists to support the mechanic

Ask what geometry the mechanic *requires*. Fishing: a dock, water, a sell
point. Zombies: a safe spawn, a defendable area, enemy spawn positions.
Fighting: an arena and spawn zones.

Build gameplay-critical geometry before decoration.

---

## 16. UI when gameplay needs it

Not all at the end, and not all at the start. Distinguish **functional UI** —
needed to operate or understand the game — from **polish UI**. Build functional
UI when its gameplay dependency becomes active. Cast feedback and a bite
indicator may be needed long before a shop menu.

---

## 17. Economy

Do not add an economy because many Roblox games have one. If the approved loop
includes selling, currency and upgrades, then define the currency source, the
sink, item valuation, upgrade costs and reward rates — but **make it work
before balancing it.**

---

## 18. Persistence

If the specification says session only, **do not create a DataStore.** If it
says saved progression, define what persists, what resets, the schema and its
version, the failure behaviour, and when saving and loading happen. Never
persist arbitrary runtime state.

---

## 19. Re-planning after steering

When the person changes something mid-build, do not rebuild everything.

1. Identify the affected part of the player journey.
2. Identify the affected gameplay path nodes.
3. Identify the affected systems.
4. Calculate their dependents.
5. Invalidate only those tasks.
6. **Preserve unaffected completed work.**
7. Produce a new build order and continue.

*"Rare fish should only appear at night"* affects fish selection, possibly adds
a time dependency, and touches zone configuration and its tests. It does not
affect inventory, selling, rod equipping or the wallet.

---

## 20. Plan validation, before any code

Check, automatically:

- every primary gameplay-path node has systems supporting it,
- every required system has tasks,
- every task belongs to an approved system,
- no excluded feature appears anywhere,
- dependencies are acyclic,
- no dependent task is ordered before something it requires,
- the core loop has an entry, an action, a result, a reward and a continuation,
- **the reward has somewhere to go,**
- the vertical slice reaches loop completion.

**If validation fails, do not start coding. Re-plan.**

---

## 21. The planning workflow

    1  read the approved GameBuildSpecification
    2  read this doctrine
    3  extract explicit constraints and excluded features
    4  create the PlayerJourney
    5  create the GameplayPath
    6  identify the game data definitions needed
    7  derive the systems
    8  build the dependency graph
    9  identify the vertical-slice systems
    10 assign priority classes P0-P7
    11 order topologically, dependencies first
    12 create the engineering tasks
    13 create acceptance criteria
    14 create playability gates
    15 validate that executing the build order could produce the PlayerJourney
    16 only then implement

---

## Appendix: a worked example — EXAMPLE ONLY

**This is an illustration, not a template.** Nothing about the Engineer is
specialised for fishing. The same reasoning produces a different order for a
tycoon, a fighting game or a horror game.

### The player experience

Player joins → appears on a dock → sees water, a rod, an inventory indicator, a
sell point → equips the rod → casts → the game decides whether a fish bites →
picks a candidate → resolves its rarity → the catch succeeds or fails → a fish
item is created → it enters the inventory → the player sees its name, rarity
and value → sells it → currency increases → buys a better rod → which changes
catch probability, rarity access, difficulty or zone → the loop repeats.

### The order that falls out of it

    PHASE 0  DEFINITIONS      item schema, rarity, fish, rod
                              everything downstream references these

    PHASE 1  PLAYER STATE     inventory model, inventory service, wallet
                              a caught fish needs somewhere valid to go

    PHASE 2  AVAILABILITY     fish catalog, rarity weighting, zones,
                              candidate selection
                              the game must know what fish CAN exist

    PHASE 3  TOOL             rod state, cast input, rod configuration

    PHASE 4  MECHANIC         cast state, bite timer, hook, catch resolution

    PHASE 5  REWARD           create the caught fish, put it in the inventory,
                              handle a full inventory, show the catch

    PHASE 6  ECONOMY          selling, currency, the first upgrade

    PHASE 7  UI               inventory, fish details, currency, feedback

    PHASE 8  EXPANSION        only after the loop passes

### The lesson worth keeping

Do not read *"the spec mentions inventory"* as *"build inventory first"*.
Inventory needs an item model. Fish generation needs fish definitions and
rarity. The mechanic needs selection. Reward delivery needs inventory.

**The order is driven by dependencies, not by the order things were
mentioned.**
