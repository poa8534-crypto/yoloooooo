import { useCallback, useEffect, useState } from 'react'
import { blueprintApi, type StudioState } from './api'

// What Roblox Studio is really doing. Three states with three different fixes,
// so they are never collapsed into "not connected":
//
//   bridge offline          the companion is not running -> a command to run
//   bridge up, no token     it cannot be asked -> paste the pairing token
//   bridge up, no plugin    Studio has not connected -> open the panel
//
// The token lives in sessionStorage rather than localStorage: it pairs one
// machine's Studio with one bridge run, and a new bridge prints a new one.

const TOKEN_KEY = 'venture.bridgeToken'

export function useStudio(pollMs = 5000) {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) ?? '')
  const [state, setState] = useState<StudioState | null>(null)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      setState(await blueprintApi.studio(token))
      setError('')
    } catch (caught) {
      setError((caught as Error).message)
    }
  }, [token])

  useEffect(() => {
    let cancelled = false
    const tick = async () => { if (!cancelled) await refresh() }
    void tick()
    const timer = setInterval(tick, pollMs)
    return () => { cancelled = true; clearInterval(timer) }
  }, [refresh, pollMs])

  const remember = useCallback((next: string) => {
    sessionStorage.setItem(TOKEN_KEY, next)
    setToken(next)
  }, [])

  return { token, setToken: remember, state, error, refresh }
}

export function StudioConnection({ state, token, onToken, error }: {
  state: StudioState | null
  token: string
  onToken: (value: string) => void
  error?: string
}) {
  const [draft, setDraft] = useState(token)
  useEffect(() => { setDraft(token) }, [token])

  const connected = Boolean(state?.plugin_connected)
  const bridgeUp = state?.bridge === 'online'

  return (
    <section className="studio-connection" data-connected={connected}>
      <h3>Roblox Studio</h3>
      <p className="studio-state">
        <span className={connected ? 'dot dot-on' : 'dot dot-off'} aria-hidden="true" />
        {connected ? 'Connected' : 'Not connected'}
      </p>

      {connected && state?.studio && (
        <dl className="studio-detail">
          <div><dt>Place</dt><dd>{state.studio.place_name || 'unknown'}</dd></div>
          <div><dt>Mode</dt><dd>{state.studio.mode}</dd></div>
          <div><dt>Studio</dt><dd>{state.studio.studio_version || 'unknown'}</dd></div>
          <div><dt>Plugin</dt><dd>{state.studio.plugin_version || 'unknown'}</dd></div>
        </dl>
      )}

      {!connected && state && (
        <div className="studio-fix">
          {/* The backend's own sentence. It says what to do; a generic
              "not connected" does not. */}
          <p>{state.detail || 'The Studio plugin has not checked in.'}</p>
          {state.bridge === 'offline' && (
            <code className="studio-command">python -m app.bridge.run</code>
          )}
          {bridgeUp && !connected && !state.needs_token && (
            <p className="studio-hint">
              In Studio: Plugins → Venture Engineer, paste the token, press Connect.
            </p>
          )}
        </div>
      )}

      {(state?.needs_token || !token) && (
        <form className="studio-token" onSubmit={event => { event.preventDefault(); onToken(draft.trim()) }}>
          <label htmlFor="bridge-token">Bridge pairing token</label>
          <input id="bridge-token" value={draft} onChange={event => setDraft(event.target.value)}
            placeholder="printed when the bridge starts" autoComplete="off" spellCheck={false} />
          <button type="submit" className="secondary" disabled={!draft.trim()}>Use token</button>
        </form>
      )}

      {error && <p className="studio-error">{error}</p>}
    </section>
  )
}
