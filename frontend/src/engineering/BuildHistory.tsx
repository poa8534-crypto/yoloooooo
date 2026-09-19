import type { BuildSummaryRow } from './api'
import { duration } from './api'

// Every build this machine has run, newest first.
//
// The counts come from the backend, which reads them off the build record. A
// row here is a real run with a real outcome -- a build that refused four
// systems says so, because the point of keeping history is being able to see
// that a later run did better, not to show a list of green ticks.
//
// Selecting a row does not start anything. It points the workspace at that
// build, and its graph, plan and events are the ones that were recorded then.

const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued', generating: 'Building', validating: 'Checking',
  applying: 'Syncing', succeeded: 'Done', failed: 'Failed', partial: 'Partial',
  interrupted: 'Interrupted',
}

function when(iso: string): string {
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return '—'
  const date = new Date(at)
  const today = new Date()
  const sameDay = date.toDateString() === today.toDateString()
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return sameDay ? `Today ${time}`
    : `${date.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`
}

export function BuildHistory({ builds, active, onOpen, onClose }: {
  builds: BuildSummaryRow[]
  active: string
  onOpen: (buildId: string) => void
  onClose: () => void
}) {
  return (
    <section className="build-history panel" data-testid="build-history">
      <header className="history-head">
        <div>
          <h2>Build history</h2>
          <p className="quiet-note">
            {builds.length} {builds.length === 1 ? 'run' : 'runs'} on this machine.
            Opening one shows the graph that run recorded.
          </p>
        </div>
        <button type="button" className="ghost-button" onClick={onClose}>
          Back to the graph
        </button>
      </header>

      {builds.length === 0 && (
        <p className="quiet-note">Nothing has been built here yet.</p>
      )}

      <ol className="history-list">
        {builds.map(build => {
          const refused = build.systems_refused > 0
          return (
            <li key={build.id} data-current={build.id === active}>
              <button type="button" className="history-row" onClick={() => onOpen(build.id)}>
                <span className="history-title">
                  <strong>{build.title || 'Untitled build'}</strong>
                  <code>{build.id}</code>
                </span>
                <span className="history-when">{when(build.created_at)}</span>
                <span className="history-count">
                  {build.systems_built}/{build.systems_attempted} built
                  {refused && (
                    <em className="history-refused"> · {build.systems_refused} refused</em>
                  )}
                </span>
                <span className="history-spent">
                  {build.attempts_spent} {build.attempts_spent === 1 ? 'attempt' : 'attempts'}
                  {build.duration_seconds != null && ` · ${duration(build.duration_seconds)}`}
                </span>
                <span className="history-status" data-status={build.status}>
                  {STATUS_LABEL[build.status] ?? build.status}
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
