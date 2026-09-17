import { useState } from 'react'
import { engineeringApi, type Steering } from './api'

// Steering a build that is already running.
//
// The panel is honest about its own reach or it is worse than nothing. Three
// things make it so, and all three come from the backend:
//
//   - the systems a directive would reach are listed BEFORE you write it, and
//     the system in flight is not among them: its prompt was built already
//   - a carried directive names the systems it went into, so "applied" is
//     checkable rather than a word next to a tick
//   - when the build stops, anything never carried is shown as stale, not
//     left looking like it is about to be used
//
// There is no Preview button. What a preview would show -- the instruction, and
// what it will reach -- is already on screen before the directive is sent.

const STATUS_NOTE: Record<string, string> = {
  pending: 'Waiting to be carried into the next prompt',
  carried: 'Carried into',
  stale: 'The build ended before this reached anything',
}

export function SteeringPanel({ buildId, steering, onChanged }: {
  buildId: string
  steering: Steering
  onChanged: (next: Steering) => void
}) {
  const [text, setText] = useState('')
  const [system, setSystem] = useState('')
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)

  const reach = system ? [system] : steering.reachable
  const canSend = steering.accepting && reach.length > 0 && text.trim().length >= 8

  async function send() {
    setSending(true)
    setError('')
    try {
      const answer = await engineeringApi.steer(buildId, text, system)
      onChanged(answer)
      setText('')
      setSystem('')
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setSending(false)
    }
  }

  async function withdraw(id: string) {
    try {
      onChanged(await engineeringApi.unsteer(buildId, id))
    } catch (caught) {
      setError((caught as Error).message)
    }
  }

  return (
    <section className="steering panel" aria-label="Live steering">
      <header className="panel-head">
        <span className="micro-label">Live steering</span>
        <span className="sync-pill" data-reported={steering.accepting}>
          {steering.accepting ? `${steering.reachable.length} reachable` : 'Build not running'}
        </span>
      </header>

      <div className="steering-body">
        {steering.directives.length > 0 && (
          <ul className="directive-list">
            {steering.directives.map(entry => (
              <li key={entry.id} data-status={entry.status}>
                <p className="directive-text">{entry.text}</p>
                <p className="directive-meta">
                  <span className="directive-status" data-status={entry.status}>
                    {entry.status}
                  </span>
                  {entry.system ? ` · ${entry.system} only` : ' · every system still to write'}
                  {entry.status === 'carried'
                    ? ` · ${STATUS_NOTE.carried} ${entry.carried_into.join(', ')}`
                    : ` · ${STATUS_NOTE[entry.status]}`}
                </p>
                {entry.status === 'pending' && (
                  <button type="button" className="link-button"
                    onClick={() => void withdraw(entry.id)}>Withdraw</button>
                )}
              </li>
            ))}
          </ul>
        )}

        {steering.accepting ? (
          <>
            <label className="steering-field">
              <span className="micro-label">Instruction for the Engineer</span>
              <textarea value={text} rows={3} maxLength={600}
                placeholder="Cap every wave at eight infected, and read the cap from the config table."
                onChange={event => setText(event.target.value)} />
            </label>

            <label className="steering-field">
              <span className="micro-label">Applies to</span>
              <select value={system} onChange={event => setSystem(event.target.value)}>
                <option value="">Every system still to be written</option>
                {steering.reachable.map(name =>
                  <option key={name} value={name}>{name} only</option>)}
              </select>
            </label>

            {/* Said before the button is pressed, not after: this is the whole
                claim the panel makes, and it is the backend's answer. */}
            <p className="steering-reach">
              {reach.length
                ? <>Will be carried into: <strong>{reach.join(', ')}</strong></>
                : 'Nothing is left for a directive to reach.'}
            </p>
            <p className="steering-caveat">
              A system already written or in flight cannot be reached — its prompt
              was built before this existed.
            </p>

            <button type="button" className="primary-button" disabled={!canSend || sending}
              onClick={() => void send()}>
              {sending ? 'Sending…' : 'Add directive'}
            </button>
          </>
        ) : (
          <p className="steering-caveat">
            This build is not running, so there is no prompt left to steer. Revise the
            blueprint and build again to change what was written.
          </p>
        )}

        {error && <p className="steering-error" role="alert">{error}</p>}
      </div>
    </section>
  )
}
