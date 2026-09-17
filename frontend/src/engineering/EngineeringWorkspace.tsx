import { useCallback, useEffect, useState } from 'react'
import './engineering.css'
import { engineeringApi, type BuildGraph, type BuildSummaryRow, type GraphNode } from './api'
import { ArchitectureMap } from './ArchitectureMap'

// The control room for a build. It answers what is being built, why, what
// remains, and what Studio is doing with it.
//
// One rule runs through the whole file: nothing on screen is inferred here.
// Node states, the current system, the counts and the event log all come from
// /api/builds/{id}/graph, which derives them from the specification and the
// build record. If the backend does not know something, this shows that it
// does not know rather than filling the gap.

const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued', planning: 'Planning', generating: 'Building',
  validating: 'Validating', waiting_for_studio: 'Waiting for Studio',
  syncing: 'Syncing to Studio', building: 'Studio applying', playtesting: 'Playtesting',
  repairing: 'Repairing', succeeded: 'Complete', partial: 'Complete with refusals',
  failed: 'Failed', cancelled: 'Cancelled', draft: 'Draft', blueprinting: 'Blueprinting',
  ready_to_build: 'Ready to build',
}
const LIVE = new Set(['queued', 'planning', 'generating', 'validating',
  'waiting_for_studio', 'syncing', 'building', 'playtesting', 'repairing'])

export function EngineeringWorkspace({ buildId, onNavigate }: {
  buildId?: string
  onNavigate: (page: string) => void
}) {
  const [builds, setBuilds] = useState<BuildSummaryRow[]>([])
  const [active, setActive] = useState(buildId ?? '')
  const [graph, setGraph] = useState<BuildGraph | null>(null)
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [studio, setStudio] = useState<{ plugin_connected: boolean; detail?: string;
    studio?: Record<string, string> | null } | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    engineeringApi.builds()
      .then(answer => {
        setBuilds(answer.builds)
        if (!active && answer.builds.length) setActive(answer.builds[0].id)
      })
      .catch(caught => setError((caught as Error).message))
  }, [active])

  const refresh = useCallback(async () => {
    if (!active) return
    try {
      setGraph(await engineeringApi.graph(active))
      setError('')
    } catch (caught) {
      setError((caught as Error).message)
    }
  }, [active])

  useEffect(() => {
    void refresh()
    // Polled only while the build is live. A finished build does not change,
    // and asking anyway would be work with a known answer.
    const timer = setInterval(() => {
      if (graph && !LIVE.has(graph.status)) return
      void refresh()
    }, 3000)
    return () => clearInterval(timer)
  }, [refresh, graph])

  useEffect(() => {
    const token = sessionStorage.getItem('venture.bridgeToken') ?? ''
    const ask = () => engineeringApi.studio(token).then(setStudio).catch(() => setStudio(null))
    void ask()
    const timer = setInterval(ask, 5000)
    return () => clearInterval(timer)
  }, [])

  if (!active && !error) {
    return (
      <div className="engineering-empty glass-panel">
        <h1>Engineering Agent</h1>
        <p>No build yet. Choose an idea in Venture Scout and turn it into a blueprint
          to start one.</p>
        <button type="button" className="primary" onClick={() => onNavigate('scout')}>
          Open Venture Scout
        </button>
      </div>
    )
  }

  const counts = graph?.counts ?? {}
  const done = counts.built ?? 0
  const total = counts.total ?? 0

  return (
    <div className="engineering-workspace" data-testid="engineering-workspace">
      <header className="engineering-header glass-panel">
        <div className="engineering-identity">
          <span className="engineering-eyebrow">Engineering Agent</span>
          <strong>{graph?.title ?? 'Loading'}</strong>
          {graph && <span className="engineering-build-id">
            {graph.build_id} · spec revision {graph.spec_revision}</span>}
        </div>

        {builds.length > 1 && (
          <label className="build-selector">
            <span>Build</span>
            <select value={active} onChange={event => {
              // Each build has its own graph and its own event history; they are
              // never merged.
              setActive(event.target.value); setGraph(null); setSelected(null)
            }}>
              {builds.map(build => (
                <option key={build.id} value={build.id}>
                  {build.id.slice(0, 14)} · {STATUS_LABEL[build.status] ?? build.status}
                </option>
              ))}
            </select>
          </label>
        )}

        {graph && (
          <div className="engineering-status" data-status={graph.status}>
            <span className="dot" aria-hidden="true" />
            {STATUS_LABEL[graph.status] ?? graph.status}
          </div>
        )}

        <div className="engineering-studio" data-connected={Boolean(studio?.plugin_connected)}>
          <span className="engineering-eyebrow">Roblox Studio</span>
          <strong>{studio?.plugin_connected ? 'Connected' : 'Not connected'}</strong>
          {studio?.plugin_connected && studio.studio && (
            <span>{studio.studio.place_name} · {studio.studio.mode}</span>
          )}
          {!studio?.plugin_connected && studio?.detail && <span>{studio.detail}</span>}
        </div>
      </header>

      {error && <div className="warning-box" role="alert"><p>{error}</p></div>}

      <div className="engineering-grid">
        <aside className="build-timeline glass-panel">
          <h2>Build timeline</h2>
          {/* The specification's build order, which is the order the Engineer
              actually works in. */}
          <ol>
            {(graph?.nodes ?? []).map(node => (
              <li key={node.id} data-state={node.state}
                data-selected={selected?.id === node.id}>
                <button type="button" onClick={() => setSelected(node)}>
                  <span className="timeline-mark" aria-hidden="true">
                    {node.state === 'built' ? '✓'
                      : node.state === 'building' ? '●'
                        : node.state === 'refused' || node.state === 'error' ? '!' : '○'}
                  </span>
                  <span className="timeline-name">{node.name}</span>
                </button>
              </li>
            ))}
          </ol>
          {graph && (
            <p className="timeline-progress">
              {done} of {total} systems built
              {counts.refused ? ` · ${counts.refused} refused` : ''}
            </p>
          )}
        </aside>

        <section className="engineering-map glass-panel">
          <div className="map-header">
            <h2>Architecture</h2>
            {graph?.current
              ? <span className="map-current">Working on {graph.current}</span>
              : <span className="map-current map-idle">
                {graph && LIVE.has(graph.status) ? 'Between systems' : 'Not running'}</span>}
          </div>
          {graph
            ? <ArchitectureMap graph={graph} selected={selected?.id ?? null}
              onSelect={setSelected} />
            : <p className="drawer-loading">Reading the build…</p>}
        </section>

        <aside className="engineering-inspector glass-panel">
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <p className="inspector-state" data-state={selected.state}>
                {STATUS_LABEL[selected.state] ?? selected.state}
                {selected.state === 'building' && ' — the Engineer is writing this now'}
              </p>
              <h3>Goal</h3>
              <p>{selected.purpose}</p>
              <h3>Acceptance criteria</h3>
              {/* Not ticked one by one: the gate judges a system as a whole, so
                  a per-criterion tick would be invented. */}
              <ul className="inspector-criteria">
                {selected.acceptance_criteria.map((criterion, index) =>
                  <li key={index}>{criterion}</li>)}
              </ul>
              <h3>Depends on</h3>
              <p>{selected.depends_on.length ? selected.depends_on.join(', ') : 'nothing'}</p>
              <h3>File</h3>
              <p><code>{selected.path}</code></p>
              {selected.branch && <><h3>Branch</h3><p><code>{selected.branch}</code></p></>}
              {selected.detail && <><h3>Why it is not built</h3>
                <p className="inspector-detail">{selected.detail}</p></>}
            </>
          ) : (
            <>
              <h2>Goal</h2>
              {graph ? <>
                <p>{graph.goal.intent || graph.goal.title}</p>
                <h3>Progress</h3>
                <p>{done} of {total} systems built
                  {counts.building ? `, ${counts.building} building` : ''}
                  {counts.waiting ? `, ${counts.waiting} waiting` : ''}
                  {counts.refused ? `, ${counts.refused} refused` : ''}.</p>
                <h3>Included</h3>
                <ul>{graph.goal.included_features.map(name => <li key={name}>{name}</li>)}</ul>
                {graph.goal.excluded_features.length > 0 && <>
                  <h3>Excluded</h3>
                  <ul>{graph.goal.excluded_features.map(name => <li key={name}>{name}</li>)}</ul>
                </>}
                <h3>Constraints</h3>
                <ul>{graph.goal.constraints.map((line, index) => <li key={index}>{line}</li>)}</ul>
              </> : <p className="drawer-loading">Reading the build…</p>}
            </>
          )}
        </aside>

        <section className="engineering-activity glass-panel">
          <h2>Live activity</h2>
          {/* Straight from the build record. Every line was written when
              something happened; there is no client-side timer. */}
          <ol className="activity-log">
            {(graph?.events ?? []).slice().reverse().map((event, index) => (
              <li key={index} data-stage={event.stage}>
                <span className="activity-time">{event.at.slice(11, 19)}</span>
                <span className="activity-stage">{event.stage}</span>
                <span>{event.detail}</span>
              </li>
            ))}
          </ol>
          {graph && graph.events.length === 0 && <p>No activity recorded yet.</p>}
        </section>
      </div>
    </div>
  )
}
