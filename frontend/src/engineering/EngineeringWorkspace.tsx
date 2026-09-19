import { useCallback, useEffect, useState } from 'react'
import './engineering.css'
import {
  duration, elapsedSince, engineeringApi, NO_PACE, NO_PLAN, NO_STEERING, NOT_SENT,
  type BuildGraph, type BuildSummaryRow, type GraphNode, type StudioStatus,
} from './api'
import { ArchitectureMap } from './ArchitectureMap'
import { BuildMachine } from './BuildMachine'
import { NodeInspector } from './NodeInspector'
import { BuildOrderView, PlayerFlow } from './PlayerFlow'
import { BuildHistory } from './BuildHistory'
import { UsageStrip } from './UsageStrip'
import { SteeringPanel } from './SteeringPanel'
import { StudioExplorer } from './StudioExplorer'

// The control room for a build. It answers what is being built, why, what
// remains, and what Studio is doing with it.
//
// One rule runs through the whole file: nothing on screen is inferred here.
// Node states, the current system, the counts, the timings and the event log
// all come from /api/builds/{id}/graph, which derives them from the
// specification and the build record. Where the backend does not know
// something, this shows that it does not know rather than filling the gap --
// which is why there is no countdown, no percentage bar that moves on a timer,
// and no per-criterion tick.

const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued', planning: 'Planning', generating: 'Building',
  validating: 'Validating', waiting_for_studio: 'Waiting for Studio',
  syncing: 'Syncing to Studio', building: 'Studio applying', playtesting: 'Playtesting',
  repairing: 'Repairing', succeeded: 'Complete', partial: 'Complete with refusals',
  failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted',
  draft: 'Draft', blueprinting: 'Blueprinting',
  ready_to_build: 'Ready to build',
}
const LIVE = new Set(['queued', 'planning', 'generating', 'validating',
  'waiting_for_studio', 'syncing', 'building', 'playtesting', 'repairing'])

const MARK: Record<string, string> = {
  built: '✓', building: '●', refused: '!', error: '!', waiting: '○', existing: '=',
}

export function EngineeringWorkspace({ buildId, onNavigate }: {
  buildId?: string
  onNavigate: (page: string) => void
}) {
  const [builds, setBuilds] = useState<BuildSummaryRow[]>([])
  const [active, setActive] = useState(buildId ?? '')
  const [graph, setGraph] = useState<BuildGraph | null>(null)
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [studio, setStudio] = useState<StudioStatus | null>(null)
  const [error, setError] = useState('')
  // The same build seen four ways: the software, what the player does, the
  // order the work happens in, and what is waiting on what. They answer
  // different questions, so they are views rather than one crowded diagram.
  const [view, setView] = useState<'systems' | 'flow' | 'order'>('systems')
  // History is a detour, not a fourth view: the graph it replaces stays mounted
  // in state, so coming back shows the build exactly as it was left rather than
  // refetching it.
  const [history, setHistory] = useState(false)
  // Whether the build list has come back yet. Without it, "no build yet" is
  // also what the first paint says, so the empty state flashes on every visit
  // and the panels below it mount twice.
  const [listed, setListed] = useState(false)
  // A message and whether it is a failure. They were one string, so the
  // bridge refusing to open a script appeared in the box that means "done".
  const [notice, setNotice] = useState<{ text: string; failed: boolean } | null>(null)
  const token = typeof sessionStorage === 'undefined'
    ? '' : sessionStorage.getItem('venture.bridgeToken') ?? ''

  useEffect(() => {
    engineeringApi.builds()
      .then(answer => {
        setBuilds(answer.builds)
        if (!active && answer.builds.length) setActive(answer.builds[0].id)
      })
      .catch(caught => setError((caught as Error).message))
      .finally(() => setListed(true))
  }, [active])

  const refresh = useCallback(async () => {
    if (!active) return
    try {
      const next = await engineeringApi.graph(active)
      setGraph(next)
      setSelected(current => current
        ? next.nodes.find(node => node.id === current.id) ?? current
        : current)
      setError('')
    } catch (caught) {
      setError((caught as Error).message)
    }
  }, [active])

  useEffect(() => { void refresh() }, [refresh])

  // Polled only while the build is live. A finished build does not change,
  // and asking anyway would be work with a known answer. The interval depends
  // on whether the build is live, never on the graph itself: every answer is a
  // new object, so depending on it re-ran this effect on every answer, and a
  // finished build was requested ten times a second while the page was open.
  const polling = !graph || LIVE.has(graph.status)
  useEffect(() => {
    if (!polling) return
    const timer = setInterval(() => { void refresh() }, 3000)
    return () => clearInterval(timer)
  }, [refresh, polling])

  useEffect(() => {
    const ask = () => engineeringApi.studio(token).then(setStudio).catch(() => setStudio(null))
    void ask()
    const timer = setInterval(ask, 5000)
    return () => clearInterval(timer)
  }, [token])

  if (!active && !error && listed) {
    return (
      <div className="engineering-workspace">
        <div className="engineering-empty panel">
          <h1>Engineering Agent</h1>
          <p>No build yet. Choose an idea in Venture Scout and turn it into a blueprint
            to start one.</p>
          <button type="button" className="primary" onClick={() => onNavigate('scout')}>
            Open Venture Scout
          </button>
        </div>
        {/* Worth knowing here above all: this is where a first build is
            started, and it is the moment the machine's readiness decides
            whether the button will work. */}
        <BuildMachine />
      </div>
    )
  }

  const counts = graph?.counts ?? {}
  const done = counts.built ?? 0
  const total = counts.total ?? 0
  const live = Boolean(graph && LIVE.has(graph.status))
  const current = graph?.nodes.find(node => node.id === graph.current) ?? null
  const pace = graph?.pace ?? NO_PACE
  const elapsed = pace.completed_at
    ? (Date.parse(pace.completed_at) - Date.parse(pace.started_at)) / 1000
    : pace.started_at ? elapsedSince(pace.started_at) : null

  return (
    <div className="engineering-workspace" data-testid="engineering-workspace">
      <header className="command-bar">
        <span className="agent-badge">Engineering Agent</span>
        <div className="build-target">
          <span className="micro-label">Build target</span>
          <strong>{graph?.title ?? 'Loading'}</strong>
        </div>
        {graph && <code className="build-id">{graph.build_id}</code>}

        {builds.length > 1 && (
          <label className="build-selector">
            <span className="micro-label">Build</span>
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

        <button type="button" className="ghost-button history-button"
          aria-pressed={history} onClick={() => setHistory(open => !open)}>
          History{builds.length ? ` · ${builds.length}` : ''}
        </button>

        <UsageStrip />

        {graph && (
          <span className="status-pill" data-status={graph.status} data-live={live}>
            <span className="dot" aria-hidden="true" />
            {STATUS_LABEL[graph.status] ?? graph.status}
            {live && counts.building ? ` · ${counts.building} in flight` : ''}
          </span>
        )}
      </header>

      {/* Everything on this strip is measured: the latency is the round trip
          the backend just made to the bridge, the version is what the plugin
          reported on its last heartbeat. */}
      <div className="studio-strip" data-connected={Boolean(studio?.plugin_connected)}>
        <span className="micro-label">Roblox Studio</span>
        <strong>{studio?.plugin_connected ? 'Connected' : 'Not connected'}</strong>
        {studio?.plugin_connected && studio.studio && <>
          <span>Place: {studio.studio.place_name || 'unnamed'}</span>
          <span>Mode: {studio.studio.mode}</span>
          {studio.studio.plugin_version && <span>Plugin {studio.studio.plugin_version}</span>}
        </>}
        {studio?.latency_ms != null && <span>Bridge {studio.latency_ms}ms</span>}
        {studio?.queued_batches ? <span>{studio.queued_batches} queued</span> : null}
        {!studio?.plugin_connected && studio?.detail &&
          <span className="strip-detail">{studio.detail}</span>}
      </div>

      <BuildMachine />

      <div className="context-strip">
        <span className="micro-label">Active generation context</span>
        {current
          ? <>
            <code>{current.studio_path || current.path}</code>
            <span className="context-note">
              the Engineer is writing this now{current.attempts
                ? ` · ${current.attempts} attempts so far` : ''}
            </span>
          </>
          : <span className="context-note">
            {live ? 'Between systems' : 'Not running'}
          </span>}
        {graph && <span className="context-right">
          Elapsed {duration(elapsed)} · {pace.attempts_spent} attempts spent
        </span>}
      </div>

      {error && <div className="warning-box" role="alert"><p>{error}</p></div>}
      {notice && <div className="notice-box" data-failed={notice.failed}
        role={notice.failed ? 'alert' : 'status'}>
        <p>{notice.text}</p>
        <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss">×</button>
      </div>}

      <div className="engineering-grid">
        <aside className="build-plan panel">
          <header className="panel-head">
            <span className="micro-label">System pipeline</span>
            <span className="panel-count">{done} / {total}</span>
          </header>
          {/* The specification's build order, which is the order the Engineer
              actually works in. */}
          <ol className="plan-list">
            {(graph?.nodes ?? []).map(node => (
              <li key={node.id} data-state={node.state}
                data-selected={selected?.id === node.id}>
                <button type="button" onClick={() => setSelected(node)}>
                  <span className="plan-mark" aria-hidden="true">{MARK[node.state]}</span>
                  <span className="plan-body">
                    <span className="plan-name">{node.name}</span>
                    <span className="plan-sub">
                      {node.state === 'built' && node.branch
                        ? node.branch.replace('engineer/', '')
                        : node.state === 'refused' ? node.detail || 'refused'
                          : node.state === 'building' ? 'in flight'
                            : node.state === 'existing' ? 'already in the project'
                              : `${node.acceptance_criteria.length} criteria`}
                    </span>
                  </span>
                  <span className="plan-state" data-state={node.state}>{node.state}</span>
                </button>
              </li>
            ))}
          </ol>
          {graph && (
            <p className="plan-progress">
              {done} of {total} systems built
              {counts.refused ? ` · ${counts.refused} refused` : ''}
            </p>
          )}
        </aside>

        <section className="agent-performance panel">
          <span className="micro-label">Agent performance</span>
          {/* Counted from the build record. No rate is shown for a build that
              has produced nothing to divide by. */}
          <div className="metric-row">
            <div className="metric">
              <strong>{pace.systems_accepted}
                <em>/{pace.attempts_spent}</em></strong>
              <span>accepted / attempts</span>
            </div>
            <div className="metric">
              <strong>{duration(pace.average_system_seconds)}</strong>
              <span>average per system
                {pace.systems_measured ? ` (${pace.systems_measured} measured)` : ''}</span>
            </div>
            <div className="metric">
              <strong>{duration(pace.slowest_system_seconds)}</strong>
              <span>slowest system</span>
            </div>
          </div>
        </section>

        {history ? (
          <BuildHistory builds={builds} active={active}
            onOpen={id => {
              // A different build is a different graph and a different event
              // history; they are never merged.
              if (id !== active) { setActive(id); setGraph(null); setSelected(null) }
              setHistory(false)
            }}
            onClose={() => setHistory(false)} />
        ) : (
        <section className="architecture panel">
          <header className="panel-head">
            <span className="micro-label">Architecture</span>
            <nav className="view-tabs" role="tablist" aria-label="Map view">
              {([['systems', 'Systems'], ['flow', 'Player flow'],
                ['order', 'Build order']] as const).map(([id, label]) => (
                <button key={id} type="button" role="tab" aria-selected={view === id}
                  className={view === id ? 'active' : ''}
                  onClick={() => setView(id)}>{label}</button>
              ))}
            </nav>
            {graph?.current
              ? <span className="panel-current">Working on {graph.current}</span>
              : <span className="panel-current quiet">
                {live ? 'Between systems' : 'Not running'}</span>}
          </header>
          {!graph ? <p className="quiet-note">Reading the build…</p>
            : view === 'systems'
              ? <ArchitectureMap graph={graph} selected={selected?.id ?? null}
                onSelect={setSelected} />
              : view === 'flow'
                ? <PlayerFlow plan={graph.plan ?? NO_PLAN}
                  onSelect={name => setSelected(
                    graph.nodes.find(node => node.id === name) ?? null)} />
                : <BuildOrderView plan={graph.plan ?? NO_PLAN} current={graph.current}
                  onSelect={name => setSelected(
                    graph.nodes.find(node => node.id === name) ?? null)} />}
        </section>
        )}

        <section className="activity panel">
          <header className="panel-head">
            <span className="micro-label">Live autonomous log</span>
            <span className="panel-count">{graph?.events.length ?? 0} events</span>
          </header>
          {/* Straight from the build record. Every line was written when
              something happened; there is no client-side timer. */}
          <ol className="activity-log">
            {(graph?.events ?? []).slice().reverse().map((event, index) => (
              <li key={index} data-stage={event.stage}>
                <span className="activity-time">{event.at.slice(11, 19)}</span>
                <span className="activity-stage">{event.stage}</span>
                <span className="activity-detail">{event.detail}</span>
              </li>
            ))}
          </ol>
          {graph && graph.events.length === 0 && <p className="quiet-note">
            No activity recorded yet.</p>}
        </section>

        {selected && graph
          ? <NodeInspector node={selected} buildId={graph.build_id} token={token}
            pace={pace}
            onOpened={(text, failed = false) => setNotice({ text, failed })} />
          : (
            <aside className="node-inspector panel" aria-label="Build goal">
              <header className="inspector-head">
                <div>
                  <span className="micro-label">Node inspector</span>
                  <h2>Goal</h2>
                  <span className="inspector-class">
                    select a system to inspect it</span>
                </div>
              </header>
              <div className="inspector-body">
                {graph ? <>
                  <p>{graph.goal.intent || graph.goal.title}</p>

                  <h3 className="micro-label">Progress</h3>
                  <p>{done} of {total} systems built
                    {counts.building ? `, ${counts.building} building` : ''}
                    {counts.waiting ? `, ${counts.waiting} waiting` : ''}
                    {counts.refused ? `, ${counts.refused} refused` : ''}.</p>

                  <h3 className="micro-label">Included</h3>
                  <div className="chip-row">
                    {graph.goal.included_features.map(name =>
                      <span className="chip" key={name}>{name}</span>)}
                  </div>

                  {graph.goal.excluded_features.length > 0 && <>
                    <h3 className="micro-label">Excluded</h3>
                    <div className="chip-row">
                      {graph.goal.excluded_features.map(name =>
                        <span className="chip chip-quiet" key={name}>{name}</span>)}
                    </div>
                  </>}

                  <h3 className="micro-label">Constraints</h3>
                  <ul className="criteria-list">
                    {graph.goal.constraints.map((line, index) => <li key={index}>{line}</li>)}
                  </ul>
                </> : <p className="quiet-note">Reading the build…</p>}
              </div>
            </aside>
          )}

        {graph && <SteeringPanel buildId={graph.build_id}
          steering={graph.steering ?? NO_STEERING}
          onChanged={next => setGraph(current => current ? { ...current, steering: next } : current)} />}

        {graph && <StudioExplorer explorer={graph.explorer ?? []}
          sync={graph.sync ?? NOT_SENT} />}
      </div>
    </div>
  )
}
