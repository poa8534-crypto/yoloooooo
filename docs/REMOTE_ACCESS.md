# Reaching the dashboard from anywhere

Two routes. They are not equivalent, and the difference is not convenience —
it is whether the agents can run at all.

## What lives on this machine

| Piece | Where | Reachable from a cloud container? |
| --- | --- | --- |
| Ollama (`qwen3:14b`) | `127.0.0.1:11434`, this GPU | **No** |
| SearxNG | `127.0.0.1:8888` | **No** |
| The ledger | `data/venture_agents.db` + `data/artifacts/` | Only if copied, and then it diverges |
| Market sampler | this process, every 30 min | Would run in both places and double-write |

Every agent in this system calls the local model. A container in Frankfurt
cannot reach a model on a desktop in India, and no amount of configuration
changes that.

## Route A — tunnel to this machine (recommended)

The app keeps running here. Everything above keeps working. You get a URL that
works from a phone on mobile data.

**Cloudflare Tunnel**

```
cloudflared tunnel --url http://127.0.0.1:8742
```

That alone publishes it to the internet with **no authentication**, which is
not acceptable for this service — see below. Put Cloudflare Access in front of
the hostname, or use a named tunnel with an Access policy, so Cloudflare asks
for your identity before anything reaches the app.

**Tailscale** (simpler, and nothing is public)

```
tailscale serve --bg 8742
```

The dashboard is then reachable at your machine's Tailscale name from any
device signed into your tailnet. No public URL exists, so there is nothing for
anyone else to find.

With either tunnel, leave `HOST=127.0.0.1`. The tunnel connects outward; the
app never listens on a public interface.

## Route B — deploy to Render

This is a re-architecture, not a deployment. To work it needs:

1. **A hosted model in place of Ollama.** `app/llm.py` talks to
   `/api/chat` with JSON-schema-constrained output. A hosted API can do that,
   but it costs money per run and ends the "local and private" property.
2. **Postgres in place of SQLite.** Render's filesystem is ephemeral: the
   ledger would be wiped on every deploy. The append-only triggers in
   `app/migrations.py` are SQLite syntax and need porting.
3. **Object storage in place of `data/artifacts/`.** Same reason.
4. **`DASHBOARD_TOKEN`.** Now enforced — see below.
5. **A decision about the sampler.** Running it both here and in the cloud
   writes two censuses into two different ledgers.

`render.yaml` in the repository root covers points 4 and 5 and nothing else.
Points 1 to 3 are real work and are not done.

## Authentication

The service used to refuse any non-loopback bind outright, because it has no
authentication of its own: the ledger, the review queue and the override
controls are open to whoever reaches the port.

That refusal now has one exception. `DASHBOARD_TOKEN` unlocks a wider bind:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it in the environment. The service will then require it on every request —
the frontend included, because serving the app shell to a stranger and leaving
the token as the only barrier is not a meaningful gate.

Present it as `Authorization: Bearer <token>`, `X-Dashboard-Token: <token>`, or
a `dashboard_token` cookie. It is **not** read from the query string: URLs end
up in logs, proxy records and browser history, and a credential in one of those
outlives every attempt to remove it.

`/api/health/live` stays open so a platform health probe, which holds no token,
does not take the service down.

Without the token, a non-loopback bind still fails at startup with the reason.
A token shorter than 24 characters is refused too — `admin` is not a credential,
and accepting it would make the guard a formality.
