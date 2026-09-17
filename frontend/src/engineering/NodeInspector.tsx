import { useEffect, useState } from 'react'
import { duration, engineeringApi, type GraphNode, type Pace, type SystemSource } from './api'

// One system, in detail. Three tabs, and each is something the backend can
// actually answer:
//
//   Overview  the specification's own words for this system
//   Luau      the code the gate accepted, read from the commit it accepted
//   Checks    the acceptance criteria, listed and NOT ticked one by one
//
// The criteria are the part worth being careful about. The gate judges a
// system whole -- six checks against the whole file -- so it never reports
// "criterion 3 passed". Ticking them individually would be inventing a result
// per line, so they are shown as what they are: what the system was asked to
// do, with the one verdict that actually exists shown once, above them.

const STATE_LABEL: Record<string, string> = {
  built: 'Accepted by the gate', building: 'Being written now', waiting: 'Not started',
  refused: 'Refused by the gate', error: 'The run failed',
}

type Tab = 'overview' | 'source' | 'checks'

export function NodeInspector({ node, buildId, token, pace, onOpened }: {
  node: GraphNode
  buildId: string
  token: string
  pace: Pace
  onOpened: (message: string) => void
}) {
  const [tab, setTab] = useState<Tab>('overview')
  const [source, setSource] = useState<SystemSource | null>(null)
  const [sourceError, setSourceError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => { setTab('overview'); setSource(null); setSourceError('') }, [node.id])

  useEffect(() => {
    if (tab !== 'source' || source || loading) return
    setLoading(true)
    engineeringApi.source(buildId, node.id)
      .then(setSource)
      .catch(caught => setSourceError((caught as Error).message))
      .finally(() => setLoading(false))
  }, [tab, source, loading, buildId, node.id])

  const took = pace.per_system_seconds[node.id]

  return (
    <aside className="node-inspector panel" aria-label={`${node.name} inspector`}>
      <header className="inspector-head">
        <div>
          <span className="micro-label">Node inspector</span>
          <h2>{node.name}</h2>
          <span className="inspector-class">
            <span className="layer">{node.layer}</span> · {node.studio_class || 'unmapped'}</span>
        </div>
        <span className="state-pill" data-state={node.state}>
          {node.state}{node.attempts ? ` · ${node.attempts}` : ''}
        </span>
      </header>

      <nav className="inspector-tabs" role="tablist" aria-label="Node detail">
        {([['overview', 'Overview'], ['source', 'Luau code'],
          ['checks', 'Criteria']] as Array<[Tab, string]>).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id}
            className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>
        ))}
      </nav>

      {tab === 'overview' && (
        <div className="inspector-body">
          <p className="inspector-verdict" data-state={node.state}>
            {STATE_LABEL[node.state] ?? node.state}
          </p>
          {node.detail && <p className="inspector-detail">{node.detail}</p>}

          <h3 className="micro-label">Goal</h3>
          <p>{node.purpose}</p>

          <h3 className="micro-label">Dependencies</h3>
          <div className="chip-row">
            {node.depends_on.length
              ? node.depends_on.map(name => <span className="chip" key={name}>{name}</span>)
              : <span className="chip chip-quiet">none</span>}
          </div>

          <h3 className="micro-label">Where it lives</h3>
          <dl className="inspector-facts">
            <dt>Repository</dt><dd><code>{node.path}</code></dd>
            <dt>DataModel</dt><dd><code>{node.studio_path || 'does not map'}</code></dd>
            {node.branch && <><dt>Branch</dt><dd><code>{node.branch}</code></dd></>}
            {node.commit && <><dt>Commit</dt><dd><code>{node.commit.slice(0, 12)}</code></dd></>}
            {took != null && <><dt>Took</dt><dd>{duration(took)}</dd></>}
            {node.attempts > 0 && <><dt>Attempts</dt><dd>{node.attempts}</dd></>}
          </dl>

          <button type="button" className="ghost-button" disabled={!node.studio_path || !token}
            onClick={() => {
              engineeringApi.open(buildId, node.id, token)
                .then(answer => onOpened(`Studio was asked to open ${answer.opened}`))
                .catch(caught => onOpened((caught as Error).message))
            }}>
            Open in Studio
          </button>
          {!token && <p className="inspector-note">
            Paste the bridge pairing token to open scripts in Studio.</p>}
        </div>
      )}

      {tab === 'source' && (
        <div className="inspector-body">
          {loading && <p className="inspector-note">Reading the accepted commit…</p>}
          {sourceError && <p className="inspector-detail">{sourceError}</p>}
          {source && source.state !== 'built' && (
            // Deliberately not the last rejected attempt. Showing refused code
            // under this system's name would present it as what was built.
            <p className="inspector-note">
              No accepted source: the gate refused this system, so nothing was kept.
              {source.detail ? ` ${source.detail}` : ''}
            </p>
          )}
          {source && source.state === 'built' && (
            <>
              <p className="inspector-note">
                <code>{source.path}</code> · {source.lines} lines · commit {source.commit.slice(0, 12)}
              </p>
              <pre className="luau-source"><code>{source.source}</code></pre>
            </>
          )}
        </div>
      )}

      {tab === 'checks' && (
        <div className="inspector-body">
          <p className="inspector-verdict" data-state={node.state}>
            {node.state === 'built'
              ? 'All six checks passed against this file.'
              : node.state === 'refused'
                ? 'The gate refused this file.'
                : 'Not judged yet.'}
          </p>
          <h3 className="micro-label">
            Acceptance criteria · {node.acceptance_criteria.length}
          </h3>
          <p className="inspector-note">
            The gate judges the file as a whole, so there is no per-criterion verdict to show.
          </p>
          <ul className="criteria-list">
            {node.acceptance_criteria.map((criterion, index) =>
              <li key={index}>{criterion}</li>)}
          </ul>
        </div>
      )}
    </aside>
  )
}
