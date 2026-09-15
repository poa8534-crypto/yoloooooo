# Local discovery search

Discovery asks three sources for Roblox games and keeps whatever answers. Only
one of them needs a third-party key, and the run no longer depends on it.

| Source | Key | Daily limit | Returns |
| --- | --- | --- | --- |
| Roblox omni-search | none | none observed | universe IDs directly |
| SearxNG (local) | none | none | web pages, place IDs extracted |
| Tavily | yes | 50/day on the free tier | web pages, place IDs extracted |

A source that is absent, disabled or unreachable is recorded as an abstention
and the round continues on the others. Only *every* source failing is an error.

Discovery results are discovery-tier whatever the source: they point at a game,
they never testify about one. Every measurement still comes from Roblox's own
API and is hashed into the ledger.

## Roblox omni-search

`apis.roblox.com/search-api/omni-search` is first-party and needs no
credential. It answers with `universeId` directly, so the resolution call that
each scraped place ID needs is skipped entirely.

It is undocumented, so it can change shape without notice. That is exactly why
it is one of several sources rather than the only one — if it breaks, discovery
degrades instead of stopping. Disable it with `ROBLOX_SEARCH_ENABLED=false`.

## SearxNG

SearxNG is a metasearch front end: it queries public search engines and returns
JSON. Running it locally removes both the API key and the daily allowance.

### What is installed

* Source at `C:\Users\tcgxu\searxng`, with its own virtual environment.
* Settings at `C:\Users\tcgxu\searxng\settings-local.yml`, bound to
  `127.0.0.1:8888` and not reachable from the network.

### Starting it

```
powershell -ExecutionPolicy Bypass -File scripts\start_searxng.ps1
```

The first start generates a secret key in place of the checked-in placeholder.
Stop it with Ctrl+C or by closing its window.

### Two local modifications

Both are recorded here rather than left as silent edits to third-party code.

1. **Four template files were not extracted.** Their names contain `:`, which
   is not a legal character in a Windows filename. They are nginx and uwsgi
   deployment templates and are not used when running the app directly.

2. **`searx/valkeydb.py` guards its `pwd` import.** `pwd` is Unix-only and the
   module imports it at load time, so SearxNG's current source cannot import at
   all on Windows. It is used in exactly one place: naming the OS user in a log
   line when a Valkey connection fails. No Valkey URL is configured here, so
   that branch cannot run. The import is wrapped in `try`/`except
   ModuleNotFoundError` and the log line falls back to a message without the
   user name.

Re-apply both after updating SearxNG.

### If SearxNG is not running

Nothing breaks. The source is recorded as unavailable for that round and
discovery continues on Roblox's own search. The Health page reports the local
instance separately from the others, with when it was last observed.

## Ranking leads before spending the budget

A search can answer with forty titles and a run can inspect thirty games. The
order matters, so leads are ranked by how far the niche's words reach into the
title and the description before any are inspected.

Three rules keep the ranker from becoming a second, unaudited filter:

* **A source that answered is never emptied.** If nothing matches the niche,
  every lead is kept at the lowest rank instead of being dropped. "toilet
  simulator" is answered with "Skibidi Battle" and "Bathroom Escape Obby",
  neither of which contains either word; dropping unmatched leads meant a run
  where every source answered, nothing failed, and no candidate was found.
* **A lead with no title or description is kept, not judged.** Absent metadata
  is not evidence of irrelevance.
* **Sources take turns.** One large native answer cannot consume the whole
  inspection budget before web results are considered.

Ranking decides order, never truth. A lead is a nomination: the game is still
captured from Roblox's own API and every measurement still goes through the
association pipeline. Nothing a search engine says about a game becomes a fact.

The per-source counts -- searches made, leads kept, leads used, and how many
upstream engines failed -- are recorded per round and shown on the Meta Hunter
page, so a source that answered with nothing usable does not look the same as
a source that was never asked.

## Engine availability is the whole game

`site:roblox.com/games <terms>` is the right query form. Measured on one query
with Brave answering, SearxNG returned **14 Roblox games against Tavily's 15**.
Alternatives without the operator -- `roblox toilet simulator game`,
`"toilet simulator" roblox experience` -- returned **zero**.

The difficulty is that public engines suspend a local instance that queries
them in bursts, and Bing, the one that stays available longest, ignores the
site operator and returns unrelated pages. So the tuning targets request
volume, not query wording:

* **Pacing.** Searches are spaced by `SEARCH_MIN_INTERVAL_SECONDS` (default 4).
* **Caching.** A repeated query is answered from the ledger for
  `SEARCH_CACHE_SECONDS` (default 24 hours) and costs no outbound request.
* **A wider pool.** Brave, Google, DuckDuckGo, Startpage, Mojeek and Qwant all
  honour the operator, so several being suspended still leaves one answering.
* **A longer engine timeout.** Mojeek and Qwant regularly need more than six
  seconds and were being recorded as unavailable on every search.

A suspension is temporary. If a search returns nothing, the instance is most
likely rate-limited rather than broken; `/search?q=...&format=json` reports
`unresponsive_engines` with the reason.

The tuned settings are kept at `config/searxng-settings.yml` with the secret
removed. Copy it over `settings-local.yml` to restore them.

## The supported alternative

SearxNG's own supported deployment is Docker, which is not installed on this
machine. A Docker or WSL install would avoid both local modifications above,
at the cost of a heavier dependency and, for WSL, a reboot.
