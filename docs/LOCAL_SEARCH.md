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

## The supported alternative

SearxNG's own supported deployment is Docker, which is not installed on this
machine. A Docker or WSL install would avoid both local modifications above,
at the cost of a heavier dependency and, for WSL, a reboot.
