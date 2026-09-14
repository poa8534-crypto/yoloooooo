import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from 'react'

type Fact = { id: string; text: string; source_ids: string[]; freshness: string; verification_state: string }
type Proposal = {
  concept_title: string; core_loop: string; differentiator: string; build_steps: string[]
  risks: string[]; questions: string[]; supporting_fact_ids: string[]
}
type Candidate = {
  id: string; external_id: string; display_name: string; facts: Fact[]; proposal: Proposal | null
  decision: string; decision_id: string | null; score: number | null; confidence: number | null
}
type Run = {
  id: string; niche: string; status: string; message: string; created_at: string; completed_at: string | null
  candidates: Candidate[]; passing_results: Candidate[]
}
type Calibration = {
  phase: string; complete_clusters: number; required_clusters: number; scoring_active: boolean
  model_version: string | null; heldout_precision: number | null; heldout_recommendations: number | null; reason: string
}
type Health = {
  status: string; database: string
  ollama: { available: boolean; primary_present: boolean; fallback_present: boolean; base_url: string }
  embeddings: { package_installed: boolean; model_name: string; last_association_used_embeddings: boolean | null }
  connectors: { tavily_configured: boolean; youtube_configured: boolean }
  scheduler: { timezone: string; daily_at: string; last_snapshot: null | { completed_at?: string; counts?: Record<string, number> } }
  calibration: Calibration
}
type DashboardSummary = {
  service_started_at: string; uptime_seconds: number; collection_started_at: string | null
  collection_age_seconds: number; latest_capture_at: string | null
  counts: {
    unique_publishers: number; source_artifacts: number; artifact_bytes: number; observations: number
    verified_facts: number; candidate_clusters: number; proposals: number; associations: number
    conflicts: number; research_runs: number; unmeasured_artifacts: number
  }
  source_tiers: Record<string, number>
  activity: Array<{ kind: string; actor: string; message: string; status: string; at: string; target_id: string }>
}
type TimelinePoint = { day: string; artifacts: number; observations: number; facts: number; associations: number }
type AuditGate = { label: string; passed: boolean; detail: string }
type AuditReadiness = { candidate_id: string; ready: boolean; gates: AuditGate[]; invariants: AuditGate[] }
type Source = {
  id: string; url: string; publisher_owner: string; retrieval_method: string; captured_at: string
  sha256: string; content_type: string; source_tier: string; is_discovery_only: boolean
  raw_size: number; observation_count: number
}
type SourceDetail = {
  artifact: Source
  observations: Array<{
    id: string; candidate_id: string | null; metric: string; value: unknown; unit: string | null
    extraction_method: string; pointer: string; observed_at: string; association_id: string | null
  }>
  facts: Array<{ id: string; text: string; template_id: string; verification_state: string; freshness: string }>
}
type MatchingCandidate = {
  candidate_id: string; universe_id: string; display_name: string; score: number
  exact_id_evidence: boolean; hard_negative: boolean; retrieval_methods: string[]
}
type MatchingArtifact = {
  id: string; url: string; publisher_owner: string; sha256: string
  source_tier: string; retrieval_method: string; captured_at: string
}
type MatchingReview = {
  association_id: string; created_at: string; outcome: string; rationale_codes: string[]
  subject_id: string; subject_type: string; subject_external_id: string; subject_title: string
  subject_description: string; subject_url: string; subject_creator: string; niche: string
  untrusted_codes: string[]; duplicate_of_subject_id: string | null
  candidate: MatchingCandidate | null; runner_up: MatchingCandidate | null; alternatives: MatchingCandidate[]
  features: Record<string, number>; feature_availability: Record<string, boolean>; feature_order: string[]
  top_score: number; runner_up_score: number; margin: number; required_coverage: number
  conflict_warnings: string[]; matcher_version: string; feature_schema_version: string
  normalization_version: string; threshold_version: string; embedding_model: string
  embedding_model_hash: string; embedding_available: boolean; shadow_mode: boolean
  validated_matcher: boolean; usable_downstream: boolean; artifacts: MatchingArtifact[]
  review_verdict: string | null; review_reason: string | null
  review_selected_candidate_id: string | null; reviewed_at: string | null
}
type MatchingStatus = {
  matcher_version: string; feature_schema_version: string; normalization_version: string
  threshold_version: string; weights_version: string; shadow_mode: boolean
  fuzzy_auto_enabled: boolean; validated: boolean; high_threshold: number; low_threshold: number
  margin_threshold: number; min_required_coverage: number; heldout_precision: number | null
  heldout_decisions: number | null; dataset_hash: string; embedding_model: string
  benchmark_reason: string; artifact_error: string; pending_reviews: number; total_associations: number
  outcome_counts: Record<string, number>
}

type PageId = 'home' | 'ideas' | 'sources' | 'matching' | 'meta' | 'scout' | 'calibration' | 'health'
type Tone = 'verified' | 'proposal' | 'inference' | 'override' | 'conflict' | 'insufficient'

const navigation: Array<{ group: string; items: Array<[PageId, string, string]> }> = [
  { group: 'Intelligence', items: [['home', '⌂', 'Command Center'], ['ideas', '◉', 'Idea Panel'], ['sources', '▦', 'Sources']] },
  { group: 'Engine', items: [['matching', '⌘', 'Matching Engine'], ['meta', '◎', 'Meta Hunter'], ['scout', '◈', 'Venture Scout']] },
  { group: 'System', items: [['calibration', '☷', 'Collection & Calibration'], ['health', '⌁', 'System Health']] },
]

const pageNames: Record<PageId, string> = {
  home: 'Command Center', ideas: 'Idea Panel', sources: 'Sources', matching: 'Matching Engine',
  meta: 'Meta Hunter', scout: 'Venture Scout', calibration: 'Collection & Calibration', health: 'System Health',
}

function describeError(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail) return detail
  // FastAPI validation errors arrive as a list of objects; stringifying the
  // array renders "[object Object]".
  if (Array.isArray(detail)) {
    const parts = detail.map(item => {
      const field = Array.isArray(item?.loc) ? item.loc.filter((p: unknown) => p !== 'body').join('.') : ''
      return [field, item?.msg].filter(Boolean).join(': ')
    }).filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return `Request failed: ${status}`
}

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(describeError(body.detail, response.status))
  }
  return response.json()
}

function formatCount(value: number | null | undefined) {
  if (value == null) return '—'
  return new Intl.NumberFormat('en', { notation: value >= 10_000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(value)
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 ** 2).toFixed(1)} MB`
}

function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`
}

function formatDate(value: string | null | undefined, includeTime = true) {
  if (!value) return 'Not yet available'
  return new Intl.DateTimeFormat('en', {
    month: 'short', day: 'numeric', year: includeTime ? undefined : 'numeric',
    hour: includeTime ? '2-digit' : undefined, minute: includeTime ? '2-digit' : undefined,
  }).format(new Date(value))
}

function toneFor(value: string): Tone {
  if (['complete', 'verified', 'auto_associate', 'approved', 'recommend', 'active'].includes(value)) return 'verified'
  if (['proposal', 'model'].includes(value)) return 'proposal'
  if (['override', 'reassigned'].includes(value)) return 'override'
  if (['failed', 'blocked_conflict', 'conflict', 'rejected'].includes(value)) return 'conflict'
  if (['running', 'queued', 'review_required'].includes(value)) return 'inference'
  return 'insufficient'
}

function Badge({ children, tone = 'insufficient' }: { children: ReactNode; tone?: Tone }) {
  return <span className={`evidence-badge ${tone}`}><i />{children}</span>
}

function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-glyph">◇</span><div><strong>{title}</strong><p>{children}</p>{action}</div></div>
}

function Metric({ label, value, note, tone }: { label: string; value: ReactNode; note: ReactNode; tone?: string }) {
  return <div className="metric-cell"><span>{label}</span><strong className={tone || ''}>{value}</strong><small>{note}</small></div>
}

function TimelineChart({ points }: { points: TimelinePoint[] }) {
  const series = [
    ['artifacts', 'Artifacts', '#111827'], ['observations', 'Observations', '#3157c8'],
    ['facts', 'Verified facts', '#21986b'], ['associations', 'Associations', '#7186ff'],
  ] as const
  if (!points.length) return <EmptyState title="No collection history yet">The first captured artifact will start this timeline.</EmptyState>
  const max = Math.max(1, ...points.flatMap(point => series.map(([key]) => point[key])))
  const width = 760
  const height = 250
  const x = (index: number) => points.length === 1 ? width / 2 : 42 + index * (width - 70) / (points.length - 1)
  const y = (value: number) => height - 28 - value * (height - 58) / max
  return <div className="chart-wrap">
    <div className="chart-legend">{series.map(([, label, color]) => <span key={label}><i style={{ background: color }} />{label}</span>)}</div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Cumulative evidence collection timeline">
      {[0, .25, .5, .75, 1].map(value => <g key={value}><line x1="42" x2={width - 28} y1={y(max * value)} y2={y(max * value)} /><text x="36" y={y(max * value) + 4} textAnchor="end">{formatCount(Math.round(max * value))}</text></g>)}
      {series.map(([key, label, color]) => <polyline key={key} aria-label={label} fill="none" stroke={color} strokeWidth="2.4" points={points.map((point, index) => `${x(index)},${y(point[key])}`).join(' ')} />)}
      <text x="42" y={height - 5}>{points[0].day}</text><text x={width - 28} y={height - 5} textAnchor="end">{points.at(-1)?.day}</text>
    </svg>
  </div>
}

function AppSidebar({ page, onNavigate, health, metaRunning, scoutReady }: { page: PageId; onNavigate: (page: PageId) => void; health: Health | null; metaRunning: boolean; scoutReady: boolean }) {
  return <aside className="app-sidebar">
    <div className="brand"><span className="brand-symbol">V</span><div><strong>Venture Agents</strong><small>Analyst workstation</small></div></div>
    <div className="node-status"><span>Node instance</span><strong>LOCAL NODE · {health?.status === 'ok' ? 'ONLINE' : 'CHECKING'}</strong><i className={health?.status === 'ok' ? 'online' : ''} /></div>
    <nav>{navigation.map(section => <div className="nav-group" key={section.group}><span>{section.group}</span>{section.items.map(([id, icon, label]) => <button key={id} className={page === id ? 'active' : ''} onClick={() => onNavigate(id)}><b>{icon}</b>{label}{id === 'meta' && <em>{metaRunning ? 'Running' : 'Idle'}</em>}{id === 'scout' && <em>{scoutReady ? 'Ready' : 'Waiting'}</em>}</button>)}</div>)}</nav>
    <div className="sidebar-footer"><div><span>SQLite WAL</span><i className={health?.database === 'connected' ? 'online' : ''} /></div><div>{health ? (health.embeddings.package_installed ? (health.embeddings.last_association_used_embeddings === false ? 'Embeddings installed · last run fell back' : 'Embeddings installed') : 'Embeddings unavailable') : 'Embeddings unknown'}</div><button disabled>▣ Collapse pane</button></div>
  </aside>
}

function TopBar({ page, health, calibration, refreshing, onRefresh, metaRunning, scoutReady, theme, onToggleTheme, zoom, onZoomChange }: {
  page: PageId; health: Health | null; calibration: Calibration | null; refreshing: boolean; onRefresh: () => void; metaRunning: boolean; scoutReady: boolean
  theme: 'dark' | 'light'; onToggleTheme: () => void; zoom: number; onZoomChange: (newZoom: number) => void
}) {
  return <><header className="topbar">
    <div className="phase-block"><span>Phase</span><strong>{calibration?.phase.replaceAll('_', ' ') || 'Loading'}</strong></div>
    <button className="topbar-action" onClick={onRefresh} disabled={refreshing}>↻ <span>{refreshing ? 'Refreshing' : 'Refresh data'}</span></button>
    <div className="agent-chip"><span>Meta Hunter</span><b>{metaRunning ? 'Running' : 'Idle'}</b></div>
    <div className="agent-chip blue"><span>Scout</span><b>{scoutReady ? 'Ready' : 'Waiting'}</b></div>
    <div className="telemetry"><span>System</span><b>{health?.status === 'ok' ? 'Healthy' : 'Checking'}</b></div>
    <div className="zoom-widget" title="Adjust layout zoom / scale">
      <button onClick={() => onZoomChange(Math.max(0.9, Number((zoom - 0.1).toFixed(2))))} disabled={zoom <= 0.9} title="Zoom out">−</button>
      <span>{Math.round(zoom * 100)}%</span>
      <button onClick={() => onZoomChange(Math.min(1.5, Number((zoom + 0.1).toFixed(2))))} disabled={zoom >= 1.5} title="Zoom in">+</button>
    </div>
    <button className="theme-switch-btn" onClick={onToggleTheme} title={`Switch to ${theme === 'dark' ? 'Light' : 'Dark'} mode`}>
      <i>{theme === 'dark' ? '☀️' : '🌙'}</i>
      <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
    </button>
    <button className="primary compact" onClick={() => document.getElementById('primary-workspace')?.scrollIntoView()}>▶ Open workspace</button>
  </header>
  <div className="firewall-strip"><span>Evidence firewall:</span><Badge tone="verified">[VF] Verified Fact</Badge><Badge tone="proposal">[MP] Model Proposal</Badge><Badge tone="inference">[AI] Analyst Inference</Badge><Badge tone="override">[HO] Human Override</Badge><Badge tone="conflict">[EC] Conflict</Badge><Badge tone="insufficient">[IE] Insufficient</Badge><strong>Firewall active</strong></div>
  <div className="page-title"><div><span>Local research system / {pageNames[page]}</span><h1>{pageNames[page]}</h1></div><Badge tone={calibration?.scoring_active ? 'verified' : 'insufficient'}>{calibration?.scoring_active ? 'Scoring active' : 'Collection mode'}</Badge></div></>
}

type TimelineRange = '7' | '30' | 'all'

function CommandCenter({ summary, timeline, sources, runs, health, calibration, onSource, onNavigate }: {
  summary: DashboardSummary | null; timeline: TimelinePoint[]; sources: Source[]; runs: Run[]; health: Health | null
  calibration: Calibration | null; onSource: (source: Source) => void; onNavigate: (page: PageId) => void
}) {
  const counts = summary?.counts
  const [range, setRange] = useState<TimelineRange>('30')
  const visibleTimeline = range === 'all' ? timeline : timeline.slice(-Number(range))
  const candidates = runs.flatMap(run => run.candidates.map(candidate => ({ ...candidate, niche: run.niche })))
  return <section className="workspace command-center">
    <div className="context-banner"><div className="context-icon">⬡</div><div><strong>Evidence Ledger Intelligence Console</strong><p>Every trusted value below resolves to a stored source artifact. Missing history remains visibly missing.</p></div><div className="connector-steps"><Badge tone={health?.connectors.tavily_configured ? 'verified' : 'conflict'}>Tavily</Badge><Badge tone={health?.connectors.youtube_configured ? 'verified' : 'conflict'}>YouTube</Badge><Badge tone="verified">Roblox public API</Badge></div></div>
    <div className="metrics-grid wide">
      <Metric label="Unique publishers" value={formatCount(counts?.unique_publishers)} note="Independent owners" />
      <Metric label="Source artifacts" value={formatCount(counts?.source_artifacts)} note="Hashed captures" />
      <Metric label="Observations" value={formatCount(counts?.observations)} note="Typed extractions" />
      <Metric label="Verified facts" value={formatCount(counts?.verified_facts)} note="Template compiled" tone="positive" />
      <Metric label="Candidate clusters" value={formatCount(counts?.candidate_clusters)} note="Tracked markets" />
      <Metric label="Associations" value={formatCount(counts?.associations)} note="Versioned matcher" />
      <Metric label="Conflicts" value={formatCount(counts?.conflicts)} note="Require review" tone={counts?.conflicts ? 'negative' : ''} />
      <Metric label="Evidence storage" value={counts ? formatBytes(counts.artifact_bytes) : '—'} note={counts?.unmeasured_artifacts ? `${counts.unmeasured_artifacts} capture(s) predate size recording` : "Raw captured bytes"} />
      <Metric label="Service uptime" value={summary ? formatDuration(summary.uptime_seconds) : '—'} note={`Started ${formatDate(summary?.service_started_at)}`} />
      <Metric label="Collection age" value={summary?.collection_started_at ? formatDuration(summary.collection_age_seconds) : '—'} note={summary?.collection_started_at ? `Since ${formatDate(summary.collection_started_at)}` : 'No captures yet'} />
    </div>
    <div className="dashboard-columns">
      <article className="data-panel chart-panel"><div className="panel-heading"><div><span>Continuous lineage tracker</span><h2>Evidence collected over time</h2></div><div className="segmented">{([['7', '7D'], ['30', '30D'], ['all', 'All']] as const).map(([value, label]) => <button key={value} className={range === value ? 'active' : ''} onClick={() => setRange(value)}>{label}</button>)}</div></div><TimelineChart points={visibleTimeline} />
        <div className="coverage-strip"><span>Primary <b>{summary?.source_tiers.primary || 0}</b></span><span>Secondary <b>{summary?.source_tiers.secondary || 0}</b></span><span>Discovery only <b>{summary?.source_tiers.discovery || 0}</b></span><span>Last capture <b>{formatDate(summary?.latest_capture_at)}</b></span></div>
      </article>
      <article className="data-panel activity-panel"><div className="panel-heading"><div><span>Append-only events</span><h2>Live system activity</h2></div></div>{summary?.activity.length ? <div className="activity-list">{summary.activity.map(item => <div key={`${item.kind}-${item.target_id}-${item.at}`}><time>{formatDate(item.at)}</time><strong>[{item.actor}]</strong><p>{item.message}</p><Badge tone={toneFor(item.status)}>{item.status.replaceAll('_', ' ')}</Badge></div>)}</div> : <EmptyState title="No activity recorded">Research, captures, matches and agent events will appear here.</EmptyState>}</article>
    </div>
    <article className="data-panel"><div className="panel-heading"><div><span>Candidate intelligence</span><h2>Tracked market niches</h2></div><button className="secondary" onClick={() => onNavigate('ideas')}>Open Idea Panel</button></div>
      {candidates.length ? <div className="table-scroll"><table><thead><tr><th>Candidate / niche</th><th>State</th><th>Facts</th><th>Confidence</th><th>Score</th><th>Action</th></tr></thead><tbody>{candidates.slice(0, 12).map(item => <tr key={item.id}><td><strong>{item.display_name}</strong><small>{item.niche} · ID {item.external_id}</small></td><td><Badge tone={toneFor(item.decision)}>{item.decision.replaceAll('_', ' ')}</Badge></td><td className="numeric">{item.facts.length}</td><td className="numeric">{item.confidence == null ? '—' : `${item.confidence.toFixed(1)}%`}</td><td className="numeric">{item.score == null ? 'Locked' : item.score.toFixed(1)}</td><td><button className="text-button" onClick={() => onNavigate('ideas')}>Inspect →</button></td></tr>)}</tbody></table></div> : <EmptyState title="No tracked market data yet">The first successful Meta Hunter run will populate candidates here. No preview records are shown as real evidence.</EmptyState>}
    </article>
    <article className="data-panel"><div className="panel-heading"><div><span>Recent captures</span><h2>Evidence ingestion stream</h2></div><button className="secondary" onClick={() => onNavigate('sources')}>View all sources</button></div>
      {sources.length ? <div className="table-scroll"><table><thead><tr><th>Publisher / endpoint</th><th>Tier</th><th>Captured</th><th>Yield</th><th>SHA256</th><th /></tr></thead><tbody>{sources.slice(0, 8).map(source => <tr key={source.id}><td><strong>{source.publisher_owner}</strong><small>{source.retrieval_method.replaceAll('_', ' ')}</small></td><td><Badge tone={source.is_discovery_only ? 'insufficient' : 'verified'}>{source.source_tier}</Badge></td><td>{formatDate(source.captured_at)}</td><td className="numeric">{source.observation_count} obs</td><td><code>{source.sha256.slice(0, 10)}…{source.sha256.slice(-6)}</code></td><td><button className="text-button" onClick={() => onSource(source)}>Inspect</button></td></tr>)}</tbody></table></div> : <EmptyState title="Evidence library is ready">Sources will appear after research captures the first API response.</EmptyState>}
    </article>
    {!counts?.source_artifacts && <div className="setup-state"><div><span>01</span><strong>{health?.connectors.tavily_configured && health?.connectors.youtube_configured ? 'Connectors authenticated' : 'Connector setup incomplete'}</strong><small>{health?.connectors.tavily_configured && health?.connectors.youtube_configured ? 'Tavily and YouTube keys are loaded locally.' : 'Check System Health before starting a run.'}</small></div><div><span>02</span><strong>Matching in shadow mode</strong><small>Unvalidated fuzzy matches cannot reach scoring.</small></div><div><span>03</span><strong>Start first research run</strong><small>Choose a niche in the Meta Hunter workspace.</small><button className="primary" onClick={() => onNavigate('meta')}>Open Meta Hunter</button></div><div><span>04</span><strong>Calibration remains locked</strong><small>{calibration?.reason || 'Awaiting complete evidence windows.'}</small></div></div>}
  </section>
}

function IdeasPanel({ runs, selectedId, onSelect, onInspectFact, onAudit, audit, busy }: {
  runs: Run[]; selectedId: string; onSelect: (id: string) => void; onInspectFact: (id: string) => void
  onAudit: (candidate: Candidate) => void; audit: { proposal?: Proposal; risks?: string[]; note?: string } | null; busy: boolean
}) {
  const [filter, setFilter] = useState<'all' | 'research_more' | 'recommend' | 'blocked_conflict'>('all')
  const allIdeas = runs.flatMap(run => run.candidates.map(candidate => ({ candidate, run })))
  const ideas = filter === 'all' ? allIdeas : allIdeas.filter(item => item.candidate.decision === filter)
  const active = allIdeas.find(item => item.candidate.id === selectedId)
  if (active) {
    const { candidate, run } = active
    const proposal = audit?.proposal || candidate.proposal
    return <section className="workspace analyst-brief"><div className="brief-toolbar"><button className="text-button" onClick={() => onSelect('')}>← Back to ideas</button><div><Badge tone={toneFor(candidate.decision)}>{candidate.decision.replaceAll('_', ' ')}</Badge><Badge tone="verified">{candidate.facts.length} verified facts</Badge></div><button className="primary" disabled={busy} onClick={() => onAudit(candidate)}>{busy ? 'Auditing…' : 'Run Venture Scout audit'}</button></div>
      <article className="brief-hero"><span>Research dossier / {run.niche}</span><h2>{proposal?.concept_title || candidate.display_name}</h2><p>{proposal?.core_loop || 'A detailed proposal will appear after the constrained local model completes a schema-valid run.'}</p><div className="metrics-grid compact"><Metric label="Engine decision" value={candidate.decision.replaceAll('_', ' ')} note="Deterministic" /><Metric label="Evidence facts" value={candidate.facts.length} note="Clickable provenance" /><Metric label="Confidence" value={candidate.confidence == null ? '—' : `${candidate.confidence.toFixed(1)}%`} note={candidate.confidence == null ? 'Not computed' : 'Evidence quality'} /><Metric label="Market score" value={candidate.score == null ? 'Locked' : candidate.score.toFixed(1)} note={candidate.score == null ? 'Calibration inactive' : 'Frozen artifact'} /></div></article>
      <div className="brief-layout"><nav className="brief-index"><span>Research brief</span>{['Executive summary', 'Why this idea', 'Demand history', 'Competitors', 'Opportunity gap', 'Twist lab', '72-hour MVP', 'Risk register', 'Provenance'].map((item, index) => <a key={item} href={`#brief-${index + 1}`}>{String(index + 1).padStart(2, '0')}. {item}</a>)}</nav>
        <div className="brief-sections"><article id="brief-1" className="data-panel"><div className="section-number">01</div><h2>Executive summary</h2>{proposal ? <><div className="classified verified"><Badge tone="verified">Verified context</Badge><p>{candidate.facts.length} facts are available from captured evidence. Use the provenance section to inspect each exact source chain.</p></div><div className="classified proposal"><Badge tone="proposal">Model proposal</Badge><p>{proposal.core_loop}</p></div><div className="classified inference"><Badge tone="inference">Analyst interpretation</Badge><p>The proposal is a design hypothesis. It is not evidence that the market or game will succeed.</p></div></> : <EmptyState title="Proposal not generated">Run Venture Scout after evidence and matching gates allow an audit.</EmptyState>}</article>
          <article id="brief-2" className="data-panel"><div className="section-number">02</div><h2>Why this idea was chosen</h2><p className="body-copy">The engine state is <strong>{candidate.decision.replaceAll('_', ' ')}</strong>. Selection reasons must come from deterministic decision records; the local model cannot author a verdict.</p><div className="fact-stack">{candidate.facts.map(fact => <button key={fact.id} onClick={() => onInspectFact(fact.id)}><Badge tone="verified">Verified fact</Badge><span>{fact.text}</span><code>{fact.id}</code></button>)}</div></article>
          <article id="brief-3" className="data-panel"><div className="section-number">03</div><h2>Demand and trend history</h2><EmptyState title="Historical window incomplete">Trend lines require at least seven days of snapshots. Missing dates will remain gaps and will never be interpolated.</EmptyState></article>
          <article id="brief-4" className="data-panel"><div className="section-number">04</div><h2>Competitor landscape</h2><EmptyState title="Competitor history not ready">Competitor comparisons will appear only after approved associations and complete snapshots exist.</EmptyState></article>
          <article id="brief-5" className="data-panel"><div className="section-number">05</div><h2>Opportunity gap</h2><div className="classified inference"><Badge tone="inference">Inference pending</Badge><p>No gap claim has enough evidence to display yet.</p></div></article>
          <article id="brief-6" className="data-panel"><div className="section-number">06</div><h2>Twist lab</h2>{proposal ? <div className="twist-grid"><div><Badge tone="proposal">Core loop</Badge><h3>{proposal.concept_title}</h3><p>{proposal.core_loop}</p></div><div><Badge tone="proposal">Differentiator</Badge><h3>Distinctive layer</h3><p>{proposal.differentiator}</p></div></div> : <EmptyState title="No model proposal">A schema-valid proposal has not been stored.</EmptyState>}</article>
          <article id="brief-7" className="data-panel"><div className="section-number">07</div><h2>72-hour MVP plan</h2>{proposal ? <div className="milestone-grid">{proposal.build_steps.map((step, index) => <div key={step}><span>Phase {index + 1}</span><strong>{step}</strong><small>Scope and timing require human confirmation.</small></div>)}</div> : <EmptyState title="MVP plan unavailable">Run the Venture Scout audit to generate a constrained proposal.</EmptyState>}</article>
          <article id="brief-8" className="data-panel"><div className="section-number">08</div><h2>Risk register</h2>{proposal?.risks.length ? <div className="risk-list">{proposal.risks.map(risk => <div key={risk}><Badge tone="proposal">Model risk</Badge><p>{risk}</p><span>Requires human assessment</span></div>)}</div> : <EmptyState title="No risk record">The audit has not produced a valid risk checklist.</EmptyState>}</article>
          <article id="brief-9" className="data-panel"><div className="section-number">09</div><h2>Provenance and audit trail</h2><div className="provenance-grid"><div><span>Supporting facts</span>{candidate.facts.map(fact => <button className="text-button" key={fact.id} onClick={() => onInspectFact(fact.id)}>{fact.id}</button>)}</div><div><span>Source artifacts</span><strong>{new Set(candidate.facts.flatMap(fact => fact.source_ids)).size}</strong></div><div><span>Research run</span><code>{run.id}</code><small>{formatDate(run.created_at)}</small></div></div></article>
        </div></div>
    </section>
  }
  return <section className="workspace"><div className="section-intro"><div><span>Senior analyst dossiers</span><h2>Idea Panel</h2><p>Every idea is separated into verified evidence, deterministic decisions and model proposals.</p></div><div className="filter-row">{([['all', 'All'], ['research_more', 'Research only'], ['recommend', 'Recommended'], ['blocked_conflict', 'Blocked']] as const).map(([value, label]) => <button key={value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div></div>
    {ideas.length ? <div className="ideas-list">{ideas.map(({ candidate, run }) => <button key={candidate.id} onClick={() => onSelect(candidate.id)}><div><span>{run.niche}</span><h3>{candidate.proposal?.concept_title || candidate.display_name}</h3><p>{candidate.proposal?.core_loop || 'Evidence collected; proposal not yet generated.'}</p></div><div><Badge tone={toneFor(candidate.decision)}>{candidate.decision.replaceAll('_', ' ')}</Badge><span>{candidate.facts.length} facts</span><span>{formatDate(run.created_at)}</span><b>Open full brief →</b></div></button>)}</div> : <EmptyState title={filter === 'all' ? "No ideas yet" : "No candidates in this state"}>{filter === 'all' ? 'Meta Hunter has not produced a source-backed candidate. Start a research run from its workspace.' : 'No tracked candidate currently holds that engine decision.'}</EmptyState>}
  </section>
}

function SourcesPage({ sources, onSource }: { sources: Source[]; onSource: (source: Source) => void }) {
  const [query, setQuery] = useState('')
  const filtered = sources.filter(source => `${source.publisher_owner} ${source.retrieval_method} ${source.url}`.toLowerCase().includes(query.toLowerCase()))
  return <section className="workspace"><div className="section-intro"><div><span>Immutable provenance</span><h2>Sources and evidence library</h2><p>Inspect exactly where every trusted observation came from.</p></div><input className="search-input" value={query} onChange={event => setQuery(event.target.value)} placeholder="Filter publisher, endpoint or URL" /></div>
    <div className="metrics-grid compact"><Metric label="Captured artifacts" value={sources.length} note="Current result set" /><Metric label="Primary" value={sources.filter(item => item.source_tier === 'primary').length} note="Authoritative APIs" tone="positive" /><Metric label="Discovery only" value={sources.filter(item => item.is_discovery_only).length} note="Cannot create facts" /><Metric label="Stored bytes" value={formatBytes(sources.reduce((sum, item) => sum + item.raw_size, 0))} note="Hashed raw payloads" /></div>
    <article className="data-panel">{filtered.length ? <div className="table-scroll"><table><thead><tr><th>Publisher / resource</th><th>Tier</th><th>Captured</th><th>Type</th><th>Yield</th><th>Size</th><th>SHA256</th><th /></tr></thead><tbody>{filtered.map(source => <tr key={source.id}><td><strong>{source.publisher_owner}</strong><small>{source.retrieval_method.replaceAll('_', ' ')}</small></td><td><Badge tone={source.is_discovery_only ? 'insufficient' : 'verified'}>{source.source_tier}</Badge></td><td>{formatDate(source.captured_at)}</td><td>{source.content_type}</td><td className="numeric">{source.observation_count}</td><td className="numeric">{formatBytes(source.raw_size)}</td><td><code>{source.sha256.slice(0, 12)}…</code></td><td><button className="text-button" onClick={() => onSource(source)}>Inspect →</button></td></tr>)}</tbody></table></div> : <EmptyState title="No matching sources">Try another filter or run research to capture evidence.</EmptyState>}</article>
  </section>
}

function MatchingEnginePage({ status, reviews, selectedId, onSelect, onReload, onReview, busy }: {
  status: MatchingStatus | null; reviews: MatchingReview[]; selectedId: string; onSelect: (id: string) => void
  onReload: () => void; onReview: (review: MatchingReview, verdict: string, reason: string, candidateId: string | null) => void; busy: boolean
}) {
  const review = reviews.find(item => item.association_id === selectedId) || reviews[0]
  const [reason, setReason] = useState('')
  const [candidateId, setCandidateId] = useState('')
  useEffect(() => { setReason(''); setCandidateId(review?.candidate?.candidate_id || '') }, [review?.association_id])
  return <section className="workspace"><div className="engine-banner"><div><span>Deterministic linkage workspace</span><h2>Matching Engine</h2><p>No LLM participates in entity association or threshold decisions.</p></div><Badge tone={status?.shadow_mode ? 'insufficient' : 'verified'}>{status?.shadow_mode ? 'Shadow mode' : 'Active mode'}</Badge><button className="secondary" onClick={onReload}>Refresh engine</button></div>
    {status?.artifact_error ? <div className="warning-box"><strong>Frozen policy artifact refused</strong><span>{status.artifact_error}</span></div> : null}
    <div className="metrics-grid compact"><Metric label="Matcher version" value={status?.matcher_version || '—'} note={status?.validated ? 'Validated' : 'Unvalidated'} /><Metric label="Feature schema" value={status?.feature_schema_version || '—'} note="Frozen ordering" /><Metric label="Recorded matches" value={formatCount(status?.total_associations)} note="Append only" /><Metric label="Review queue" value={formatCount(status?.pending_reviews)} note="Abstentions" /><Metric label="Held-out precision" value={status?.heldout_precision == null ? '—' : `${(status.heldout_precision * 100).toFixed(2)}%`} note={status?.benchmark_reason || 'Benchmark pending'} /></div>
    <div className="engine-tabs"><button className="active">Review queue</button><button disabled>Threshold laboratory</button><button disabled>Feature inspector</button><button disabled>Match sandbox</button><button disabled>Versions</button></div>
    <div className="review-layout"><article className="data-panel review-queue"><div className="panel-heading"><div><span>Human decisions</span><h2>Pending review</h2></div><Badge tone="inference">{reviews.length} items</Badge></div>{reviews.length ? reviews.map(item => <button className={review?.association_id === item.association_id ? 'active' : ''} key={item.association_id} onClick={() => onSelect(item.association_id)}><strong>{item.subject_title || 'Untitled captured source'}</strong><span>{item.candidate?.display_name || 'No candidate'} · margin {item.margin.toFixed(3)}</span><Badge tone={toneFor(item.outcome)}>{item.outcome.replaceAll('_', ' ')}</Badge></button>) : <EmptyState title="Review queue is empty">Ambiguous or conflicting associations will appear here instead of being guessed.</EmptyState>}</article>
      <div className="review-detail">{review ? <><article className="data-panel"><div className="panel-heading"><div><span>Captured source · {review.subject_type.replaceAll('_', ' ')}</span><h2>{review.subject_title || 'Untitled source'}</h2></div><Badge tone={toneFor(review.outcome)}>{review.outcome.replaceAll('_', ' ')}</Badge></div><p className="captured-copy">{review.subject_description || 'No description was captured.'}</p><div className="meta-line"><span>{review.subject_creator || 'Unknown creator'}</span><code>{review.subject_external_id || 'No external ID'}</code></div>{review.conflict_warnings.length ? <div className="warning-box"><strong>Contradiction guards</strong>{review.conflict_warnings.map(item => <span key={item}>{item.replaceAll('_', ' ')}</span>)}</div> : null}</article>
        <article className="data-panel"><div className="candidate-compare"><div><span>Top candidate</span><strong>{review.candidate?.display_name || 'None'}</strong><b>{review.top_score.toFixed(4)}</b></div><div><span>Runner-up</span><strong>{review.runner_up?.display_name || 'None'}</strong><b>{review.runner_up_score.toFixed(4)}</b></div><div><span>Margin</span><strong>{review.margin.toFixed(4)}</strong><b>{(review.required_coverage * 100).toFixed(0)}% coverage</b></div></div>{Object.keys(review.features).length ? <div className="feature-bars">{(review.feature_order.length ? review.feature_order : Object.keys(review.features)).map(name => <div key={name}><span>{name.replaceAll('_', ' ')}</span><div><i style={{ width: `${Math.max(0, Math.min(100, (review.features[name] || 0) * 100))}%` }} /></div><code>{(review.features[name] || 0).toFixed(4)}</code></div>)}</div> : <EmptyState title="No features were computed">A hard rule stopped this source before any candidate was scored, so there is nothing to weigh. The candidates below are still listed so you can choose one by hand.</EmptyState>}</article>
        <article className="data-panel"><div className="panel-heading"><div><span>Append-only annotation</span><h2>Reviewer decision</h2></div></div><label>Candidate<select value={candidateId} onChange={event => setCandidateId(event.target.value)}><option value="">No candidate</option>{review.alternatives.map(item => <option key={item.candidate_id} value={item.candidate_id}>{item.display_name} — {item.score.toFixed(4)}</option>)}</select></label><label>Required reason<textarea value={reason} onChange={event => setReason(event.target.value)} placeholder="Explain why the evidence supports or rejects this association" /></label><div className="button-row"><button className="primary" disabled={busy || reason.trim().length < 10 || !candidateId} onClick={() => onReview(review, 'approved', reason, candidateId)}>Approve</button><button className="danger" disabled={busy || reason.trim().length < 10} onClick={() => onReview(review, 'rejected', reason, null)}>Reject</button><button className="secondary" disabled={busy || reason.trim().length < 10 || !candidateId || candidateId === review.candidate?.candidate_id} onClick={() => onReview(review, 'reassigned', reason, candidateId)}>Reassign</button></div></article></> : <EmptyState title="No association selected">Run research or wait for an ambiguous match to enter review.</EmptyState>}</div>
    </div>
  </section>
}

function MetaHunterPage({ runs, health, onStart, busy }: { runs: Run[]; health: Health | null; onStart: (niche: string) => void; busy: boolean }) {
  const [niche, setNiche] = useState('')
  const active = runs.find(run => ['queued', 'running'].includes(run.status))
  function submit(event: FormEvent) { event.preventDefault(); onStart(niche) }
  // Reference documentation of the pipeline, not per-stage telemetry. The
  // backend reports one status and one message per run; claiming to know which
  // of eight stages is live would be invented.
  const stages = ['Discovering source leads', 'Resolving Roblox universe IDs', 'Capturing primary artifacts', 'Extracting typed observations', 'Running deterministic matching', 'Waiting for review gates', 'Generating constrained proposals', 'Compiling results']
  const latest = runs[0]
  return <section className="workspace"><div className="agent-boundary"><Badge tone="proposal">Agent authority: proposal only</Badge><p>Meta Hunter can suggest concepts and search heuristics. It cannot create URLs, platform metrics, scores, confidence values or verdicts.</p><strong>Invariant enforced</strong></div>
    <div className="agent-columns"><article className="data-panel"><div className="panel-heading"><div><span>Research configuration</span><h2>Meta Hunter parameters</h2></div><Badge tone={active ? 'inference' : 'insufficient'}>{active ? active.status : 'Idle'}</Badge></div><form onSubmit={submit}><label>Research niche or question<input value={niche} minLength={3} required onChange={event => setNiche(event.target.value)} placeholder="e.g. cooperative cozy farming" /></label><div className="form-grid"><label>Region<input value="Global" disabled /></label><label>Corpus language<input value="English" disabled /></label><label>Candidate cap<input value="5 verified IDs" disabled /></label><label>Search policy<input value="Discovery → primary evidence" disabled /></label></div><button className="primary" disabled={busy || Boolean(active)}>{busy ? 'Starting…' : active ? 'Research already running' : 'Start research run'}</button></form></article>
      <article className="data-panel"><div className="panel-heading"><div><span>Immutable safety rules</span><h2>Protected invariants</h2></div></div><div className="invariant-list">{[['Evidence firewall', true], ['URLs from model', false], ['Platform metrics from model', false], ['Model-authored verdicts', false], ['Strict JSON schema', true], ['Fail-closed mode', true]].map(([label, enabled]) => <div key={String(label)}><span>{label}</span><Badge tone={enabled ? 'verified' : 'conflict'}>{enabled ? 'Enabled · locked' : 'Disabled · locked'}</Badge></div>)}</div></article></div>
    <article className="data-panel"><div className="panel-heading"><div><span>Reported run state</span><h2>Pipeline</h2></div>{latest && <Badge tone={toneFor(latest.status)}>{latest.status}</Badge>}</div>
      {latest ? <div className="run-state"><strong>{latest.niche}</strong><p className="mono">{latest.message}</p><small>{active ? 'This run is still in progress.' : `Finished ${formatDate(latest.completed_at || latest.created_at)}`}</small></div> : <EmptyState title="No run reported yet">Start a research run to see its reported state.</EmptyState>}
      <div className="pipeline-list reference">{stages.map((stage, index) => <div key={stage}><b>{index + 1}</b><span>{stage}</span></div>)}</div>
      <small className="gate-hint">Stage list is documentation of the pipeline order. Per-stage progress is not reported by the backend and is not inferred here.</small></article>
    <article className="data-panel"><div className="panel-heading"><div><span>Append-only run records</span><h2>Run history</h2></div></div>{runs.length ? <div className="table-scroll"><table><thead><tr><th>Niche</th><th>Status</th><th>Started</th><th>Candidates</th><th>Result</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td><strong>{run.niche}</strong><small><code>{run.id}</code></small></td><td><Badge tone={toneFor(run.status)}>{run.status}</Badge></td><td>{formatDate(run.created_at)}</td><td className="numeric">{run.candidates.length}</td><td>{run.message}</td></tr>)}</tbody></table></div> : <EmptyState title="No Meta Hunter runs">Submit the first niche above when you are ready to collect evidence.</EmptyState>}</article>
    <div className="api-readiness"><span>Tavily <b>{health?.connectors.tavily_configured ? 'Authenticated' : 'Missing key'}</b></span><span>YouTube <b>{health?.connectors.youtube_configured ? 'Authenticated' : 'Missing key'}</b></span><span>Roblox <b>Public interface</b></span><span>Qwen <b>{health?.ollama.primary_present ? '14B ready' : 'Unavailable'}</b></span></div>
  </section>
}

function VentureScoutPage({ candidates, onAudit, audit, busy }: { candidates: Candidate[]; onAudit: (candidate: Candidate) => void; audit: { proposal?: Proposal; risks?: string[]; note?: string } | null; busy: boolean }) {
  const [candidateId, setCandidateId] = useState(candidates[0]?.id || '')
  const [readiness, setReadiness] = useState<AuditReadiness | null>(null)
  const [readinessError, setReadinessError] = useState('')
  useEffect(() => { if (!candidates.some(item => item.id === candidateId)) setCandidateId(candidates[0]?.id || '') }, [candidates, candidateId])
  const candidate = candidates.find(item => item.id === candidateId)

  // Every gate below is computed server-side against the ledger. This panel
  // used to mark all of them green as soon as a candidate was selected.
  useEffect(() => {
    let cancelled = false
    setReadiness(null); setReadinessError('')
    if (!candidateId) return
    api<AuditReadiness>(`/api/candidates/${candidateId}/audit-readiness`)
      .then(result => { if (!cancelled) setReadiness(result) })
      .catch(caught => { if (!cancelled) setReadinessError((caught as Error).message) })
    return () => { cancelled = true }
  }, [candidateId])
  return <section className="workspace"><div className="scout-guard"><div><strong>Venture Scout audit guardrail</strong><span>Fail-closed enforced</span></div><p>Audits are scoped to a solo beginner and a 72-hour Roblox MVP. The model cannot invent facts or override deterministic decisions.</p></div>
    <div className="agent-columns"><article className="data-panel"><div className="panel-heading"><div><span>Solo MVP configuration</span><h2>Candidate audit</h2></div></div>{candidates.length ? <><label>Candidate<select value={candidateId} onChange={event => setCandidateId(event.target.value)}>{candidates.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label><div className="form-grid"><label>Builder experience<input value="Beginner" disabled /></label><label>Team capacity<input value="Solo" disabled /></label><label>MVP scope<input value="72 hours" disabled /></label><label>Monetization<input value="Excluded from MVP" disabled /></label></div><button className="primary" disabled={busy || !candidate} onClick={() => candidate && onAudit(candidate)}>{busy ? 'Auditing…' : 'Run Venture Scout audit'}</button>{readiness && !readiness.ready && <small className="gate-hint">Some gates are not met. The audit will still run, and remains a proposal rather than a verdict.</small>}</> : <EmptyState title="No candidate available">Run Meta Hunter and approve required matches first.</EmptyState>}</article>
      <article className="data-panel"><div className="panel-heading"><div><span>Measured against the ledger</span><h2>Audit gates</h2></div>{readiness && <Badge tone={readiness.ready ? 'verified' : 'insufficient'}>{readiness.ready ? 'All gates pass' : 'Gates outstanding'}</Badge>}</div>
        {readinessError && <div className="warning-box"><strong>Readiness unavailable</strong><span>{readinessError}</span></div>}
        {!candidateId ? <EmptyState title="No candidate selected">Gate state is computed per candidate; nothing is assumed.</EmptyState>
          : readiness ? <><div className="audit-gates">{readiness.gates.map(gate => <div key={gate.label}><i />{gate.label}<small>{gate.detail}</small><Badge tone={gate.passed ? 'verified' : 'insufficient'}>{gate.passed ? 'Pass' : 'Not met'}</Badge></div>)}</div>
            <div className="invariant-note"><span>Always-on pipeline invariants</span>{readiness.invariants.map(item => <div key={item.label}><b>{item.label}</b><small>{item.detail}</small></div>)}</div></>
          : <div className="drawer-loading">Checking gates…</div>}</article></div>
    {audit?.proposal ? <div className="audit-output"><article className="data-panel"><div className="section-number">01</div><h2>{audit.proposal.concept_title}</h2><div className="classified proposal"><Badge tone="proposal">Model proposal</Badge><p>{audit.proposal.core_loop}</p></div><div className="classified inference"><Badge tone="inference">Differentiator</Badge><p>{audit.proposal.differentiator}</p></div></article><article className="data-panel"><div className="section-number">02</div><h2>72-hour milestone plan</h2><div className="milestone-grid">{audit.proposal.build_steps.map((step, index) => <div key={step}><span>Milestone {index + 1}</span><strong>{step}</strong><small>Human scope confirmation required</small></div>)}</div></article><article className="data-panel"><div className="section-number">03</div><h2>Risk and mitigation ledger</h2><div className="risk-list">{audit.proposal.risks.map(risk => <div key={risk}><Badge tone="proposal">Proposed risk</Badge><p>{risk}</p><span>Not a measured probability</span></div>)}</div></article></div> : <article className="data-panel"><EmptyState title="No Venture Scout audit loaded">Select a source-backed candidate and run the audit. Invalid model output will fail closed.</EmptyState></article>}
  </section>
}

function CalibrationPage({ calibration, summary }: { calibration: Calibration | null; summary: DashboardSummary | null }) {
  const progress = calibration ? Math.min(100, calibration.complete_clusters / calibration.required_clusters * 100) : 0
  const gates = [
    ['Complete 30-day windows', Boolean(calibration && calibration.complete_clusters >= calibration.required_clusters), `${calibration?.complete_clusters || 0} / ${calibration?.required_clusters || 200}`],
    ['Held-out precision ≥ 95%', Boolean(calibration?.heldout_precision && calibration.heldout_precision >= .95), calibration?.heldout_precision == null ? 'Pending' : `${(calibration.heldout_precision * 100).toFixed(2)}%`],
    ['At least ten held-out recommendations', Boolean(calibration?.heldout_recommendations && calibration.heldout_recommendations >= 10), calibration?.heldout_recommendations == null ? 'Pending' : String(calibration.heldout_recommendations)],
    ['Frozen artifact activated', Boolean(calibration?.scoring_active), calibration?.model_version || 'Not active'],
  ] as const
  return <section className="workspace"><div className={`calibration-warning ${calibration?.scoring_active ? 'active' : ''}`}><Badge tone={calibration?.scoring_active ? 'verified' : 'conflict'}>{calibration?.scoring_active ? 'Scoring active' : 'Scoring locked'}</Badge><div><strong>{calibration?.scoring_active ? 'Market-growth scoring is active' : 'Market-growth scoring remains disabled'}</strong><p>{calibration?.reason}</p></div></div><div className="metrics-grid compact"><Metric label="Candidate clusters" value={summary?.counts.candidate_clusters || 0} note="Tracked" /><Metric label="Complete windows" value={calibration?.complete_clusters || 0} note={`of ${calibration?.required_clusters || 200}`} /><Metric label="Dataset readiness" value={`${progress.toFixed(1)}%`} note="Window gate" /><Metric label="Held-out precision" value={calibration?.heldout_precision == null ? '—' : `${(calibration.heldout_precision * 100).toFixed(2)}%`} note="Untouched test set" /></div><div className="calibration-grid"><article className="data-panel"><div className="panel-heading"><div><span>30-day snapshot continuity</span><h2>Collection matrix</h2></div></div><div className="large-progress"><i style={{ width: `${progress}%` }} /><span>{progress.toFixed(1)}%</span></div><EmptyState title="Candidate-level matrix awaits history">A row will appear for every tracked cluster. Missing daily snapshots remain explicit gaps.</EmptyState></article><article className="data-panel"><div className="panel-heading"><div><span>Activation policy</span><h2>Decision gates</h2></div></div><div className="gate-stack">{gates.map(([label, pass, result]) => <div key={label}><i className={pass ? 'pass' : ''}>{pass ? '✓' : '×'}</i><span><strong>{label}</strong><small>{result}</small></span><Badge tone={pass ? 'verified' : 'insufficient'}>{pass ? 'Pass' : 'Locked'}</Badge></div>)}</div></article></div></section>
}

function HealthPage({ health, summary, matching }: { health: Health | null; summary: DashboardSummary | null; matching: MatchingStatus | null }) {
  const modules = [
    ['FastAPI core service', health?.status === 'ok', `Uptime ${summary ? formatDuration(summary.uptime_seconds) : '—'}`],
    ['SQLite WAL evidence ledger', health?.database === 'connected', `${summary?.counts.source_artifacts || 0} artifacts`],
    ['Local Ollama daemon', health?.ollama.available, health?.ollama.base_url || 'Unknown endpoint'],
    ['Qwen3 14B primary', health?.ollama.primary_present, health?.ollama.primary_present ? 'Installed' : 'Missing'],
    ['Qwen3 8B fallback', health?.ollama.fallback_present, health?.ollama.fallback_present ? 'Installed' : 'Missing'],
    ['Matching engine', Boolean(matching), matching?.matcher_version || 'Unavailable'],
    ['Tavily search API', health?.connectors.tavily_configured, health?.connectors.tavily_configured ? 'Configured' : 'Key missing'],
    ['YouTube Data API', health?.connectors.youtube_configured, health?.connectors.youtube_configured ? 'Configured' : 'Key missing'],
    ['Daily snapshot scheduler', Boolean(health?.scheduler.daily_at), health ? `${health.scheduler.daily_at} ${health.scheduler.timezone}` : 'Checking'],
    ['Local embedding model', health?.embeddings.package_installed, health?.embeddings.package_installed ? `${health.embeddings.model_name}${health.embeddings.last_association_used_embeddings === false ? ' · last run used lexical fallback' : ''}` : 'fastembed not installed · lexical retrieval only'],
  ] as const
  return <section className="workspace"><div className="keyring-banner"><strong>▣ Local secret boundary engaged</strong><p>API keys stay in the ignored local environment file and are never returned to the dashboard.</p><Badge tone="verified">No secrets exposed</Badge></div><div className="module-grid">{modules.map(([name, ok, detail]) => <article className="data-panel" key={name}><div><span className={`module-dot ${ok ? 'online' : ''}`} /><Badge tone={ok ? 'verified' : 'conflict'}>{ok ? 'Healthy' : 'Attention'}</Badge></div><h2>{name}</h2><p>{detail}</p></article>)}</div><div className="health-bottom"><article className="data-panel"><div className="panel-heading"><div><span>External connectors</span><h2>API readiness</h2></div></div><div className="quota-list"><div><span>Tavily</span><b>{health?.connectors.tavily_configured ? 'Authenticated' : 'Not configured'}</b></div><div><span>YouTube Data API v3</span><b>{health?.connectors.youtube_configured ? 'Authenticated' : 'Not configured'}</b></div><div><span>Roblox public interfaces</span><b>Read-only</b></div></div></article><article className="data-panel"><div className="panel-heading"><div><span>Deterministic capture</span><h2>Snapshot ledger</h2></div></div><Metric label="Last snapshot" value={formatDate(health?.scheduler.last_snapshot?.completed_at)} note={health?.scheduler.last_snapshot ? JSON.stringify(health.scheduler.last_snapshot.counts || {}) : 'No snapshot recorded'} /></article></div></section>
}

function EvidenceDrawer({ detail, loading, onClose, onFact }: { detail: SourceDetail | null; loading: boolean; onClose: () => void; onFact: (id: string) => void }) {
  return <div className={`drawer-scrim ${loading || detail ? 'open' : ''}`} onMouseDown={event => { if (event.currentTarget === event.target) onClose() }}><aside className="evidence-drawer" aria-label="Evidence inspector"><header><div><span>Evidence inspector</span><strong>{detail?.artifact.id || 'Loading source'}</strong></div><button aria-label="Close evidence inspector" onClick={onClose}>×</button></header>{loading ? <div className="drawer-loading">Loading provenance…</div> : detail ? <div className="drawer-body"><dl><dt>Canonical URL</dt><dd><a href={detail.artifact.url} target="_blank" rel="noreferrer">{detail.artifact.url}</a></dd><dt>Publisher</dt><dd>{detail.artifact.publisher_owner}</dd><dt>Retrieval</dt><dd>{detail.artifact.retrieval_method.replaceAll('_', ' ')}</dd><dt>Captured</dt><dd>{formatDate(detail.artifact.captured_at)}</dd><dt>SHA256</dt><dd><code>{detail.artifact.sha256}</code></dd><dt>Raw payload</dt><dd>{detail.artifact.content_type} · {formatBytes(detail.artifact.raw_size)}</dd></dl><section><div className="drawer-section-title"><span>Extracted observations</span><Badge tone="verified">{detail.observations.length}</Badge></div>{detail.observations.length ? detail.observations.map(item => <article key={item.id}><span>{item.metric}</span><strong>{String(item.value)} {item.unit || ''}</strong><code>{item.pointer}</code><small>{item.extraction_method.replaceAll('_', ' ')}</small></article>) : <EmptyState title="No trusted observations">Discovery-only artifacts cannot create observations.</EmptyState>}</section><section><div className="drawer-section-title"><span>Compiled facts</span><Badge tone="verified">{detail.facts.length}</Badge></div>{detail.facts.map(fact => <button className="drawer-fact" key={fact.id} onClick={() => onFact(fact.id)}><Badge tone="verified">Verified fact</Badge><span>{fact.text}</span></button>)}</section></div> : null}</aside></div>
}

export default function App() {
  const [page, setPage] = useState<PageId>('home')
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [timeline, setTimeline] = useState<TimelinePoint[]>([])
  const [sources, setSources] = useState<Source[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [calibration, setCalibration] = useState<Calibration | null>(null)
  const [matchingStatus, setMatchingStatus] = useState<MatchingStatus | null>(null)
  const [matchingReviews, setMatchingReviews] = useState<MatchingReview[]>([])
  const [selectedMatch, setSelectedMatch] = useState('')
  const [selectedIdea, setSelectedIdea] = useState('')
  const [sourceDetail, setSourceDetail] = useState<SourceDetail | null>(null)
  const [sourceLoading, setSourceLoading] = useState(false)
  const [audit, setAudit] = useState<{ proposal?: Proposal; risks?: string[]; note?: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    const saved = localStorage.getItem('rva_theme')
    if (saved === 'dark' || saved === 'light') return saved
    return 'dark'
  })
  const [zoom, setZoom] = useState<number>(() => {
    const saved = localStorage.getItem('rva_zoom')
    if (saved) {
      const num = parseFloat(saved)
      if (!isNaN(num) && num >= 0.8 && num <= 1.6) return num
    }
    return 1.15
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('rva_theme', theme)
  }, [theme])

  useEffect(() => {
    document.documentElement.style.setProperty('--app-zoom', String(zoom))
    localStorage.setItem('rva_zoom', String(zoom))
  }, [zoom])

  const candidates = useMemo(() => runs.flatMap(run => run.candidates), [runs])
  const metaRunning = runs.some(run => ['queued', 'running'].includes(run.status))
  const scoutReady = candidates.length > 0

  const loadMatching = useCallback(async () => {
    const [status, reviews] = await Promise.all([
      api<MatchingStatus>('/api/matching/status'), api<MatchingReview[]>('/api/matching/reviews?limit=50&include_resolved=false'),
    ])
    setMatchingStatus(status); setMatchingReviews(reviews)
    setSelectedMatch(current => reviews.some(item => item.association_id === current) ? current : reviews[0]?.association_id || '')
  }, [])

  const loadAll = useCallback(async () => {
    setRefreshing(true); setError('')
    try {
      const [nextSummary, nextTimeline, nextSources, nextRuns, nextCalibration, nextHealth] = await Promise.all([
        api<DashboardSummary>('/api/dashboard/summary'), api<{ points: TimelinePoint[] }>('/api/dashboard/timeline'),
        api<Source[]>('/api/sources?limit=100'), api<Run[]>('/api/research-runs?limit=25'),
        api<Calibration>('/api/calibration/status'), api<Health>('/api/health'),
      ])
      setSummary(nextSummary); setTimeline(nextTimeline.points); setSources(nextSources); setRuns(nextRuns)
      setCalibration(nextCalibration); setHealth(nextHealth)
      await loadMatching()
    } catch (caught) { setError((caught as Error).message) } finally { setRefreshing(false) }
  }, [loadMatching])

  useEffect(() => { void loadAll() }, [loadAll])

  // Depend on the active run's ID, not on `runs`. The callback calls setRuns,
  // so depending on the array tore down and rebuilt the timer every tick.
  const activeRunId = runs.find(run => ['queued', 'running'].includes(run.status))?.id ?? ''
  useEffect(() => {
    if (!activeRunId) return
    const timer = window.setInterval(async () => {
      try {
        const updated = await api<Run>(`/api/research-runs/${activeRunId}`)
        setRuns(current => current.map(run => run.id === updated.id ? updated : run))
        if (!['queued', 'running'].includes(updated.status)) void loadAll()
      } catch (caught) { setError((caught as Error).message) }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [activeRunId, loadAll])

  async function startResearch(niche: string) {
    setBusy(true); setError(''); setNotice('')
    try {
      const run = await api<Run>('/api/research-runs', { method: 'POST', body: JSON.stringify({ niche }) })
      setRuns(current => [run, ...current.filter(item => item.id !== run.id)])
      setNotice(`Meta Hunter queued research for “${run.niche}”.`)
    } catch (caught) { setError((caught as Error).message) } finally { setBusy(false) }
  }

  async function openSourceById(artifactId: string) {
    setSourceLoading(true); setSourceDetail(null); setError('')
    try { setSourceDetail(await api<SourceDetail>(`/api/sources/${artifactId}`)) }
    catch (caught) { setError((caught as Error).message) }
    finally { setSourceLoading(false) }
  }

  async function openSource(source: Source) {
    await openSourceById(source.id)
  }

  async function inspectFact(id: string) {
    setError('')
    try {
      const evidence = await api<{ artifacts: Array<{ id: string }> }>(`/api/evidence/${id}`)
      const artifactId = evidence.artifacts[0]?.id
      if (!artifactId) throw new Error('This fact has no linked source artifact.')
      // Fetch the artifact by ID. Searching the loaded source list meant any
      // fact beyond the first 100 sources could not be inspected at all.
      await openSourceById(artifactId)
    } catch (caught) { setError((caught as Error).message) }
  }

  async function runAudit(candidate: Candidate) {
    setBusy(true); setError(''); setAudit(null)
    try { setAudit(await api(`/api/candidates/${candidate.id}/audit`, { method: 'POST' })) }
    catch (caught) { setError((caught as Error).message) }
    finally { setBusy(false) }
  }

  async function reviewMatch(review: MatchingReview, verdict: string, reason: string, candidateId: string | null) {
    setBusy(true); setError(''); setNotice('')
    try {
      await api(`/api/matching/reviews/${review.association_id}`, {
        method: 'POST', body: JSON.stringify({ verdict, reason, selected_candidate_id: candidateId }),
      })
      setNotice(`Review recorded as ${verdict}. The original engine verdict remains immutable.`)
      await loadMatching()
    } catch (caught) { setError((caught as Error).message) }
    finally { setBusy(false) }
  }

  function navigate(nextPage: PageId) {
    setPage(nextPage); setError(''); setNotice('')
    if (nextPage !== 'ideas') setSelectedIdea('')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return <div className="app-shell">
    <AppSidebar page={page} onNavigate={navigate} health={health} metaRunning={metaRunning} scoutReady={scoutReady} />
    <div className="app-content"><TopBar page={page} health={health} calibration={calibration} refreshing={refreshing} onRefresh={loadAll} metaRunning={metaRunning} scoutReady={scoutReady} theme={theme} onToggleTheme={() => setTheme(t => t === 'dark' ? 'light' : 'dark')} zoom={zoom} onZoomChange={setZoom} />
      <main id="primary-workspace">
        {error && <div role="alert" className="global-alert"><strong>Request failed safely</strong><span>{error}</span><button onClick={() => setError('')}>×</button></div>}
        {notice && <div className="global-notice"><span>{notice}</span><button onClick={() => setNotice('')}>×</button></div>}
        {page === 'home' && <CommandCenter summary={summary} timeline={timeline} sources={sources} runs={runs} health={health} calibration={calibration} onSource={openSource} onNavigate={navigate} />}
        {page === 'ideas' && <IdeasPanel runs={runs} selectedId={selectedIdea} onSelect={setSelectedIdea} onInspectFact={inspectFact} onAudit={runAudit} audit={audit} busy={busy} />}
        {page === 'sources' && <SourcesPage sources={sources} onSource={openSource} />}
        {page === 'matching' && <MatchingEnginePage status={matchingStatus} reviews={matchingReviews} selectedId={selectedMatch} onSelect={setSelectedMatch} onReload={loadMatching} onReview={reviewMatch} busy={busy} />}
        {page === 'meta' && <MetaHunterPage runs={runs} health={health} onStart={startResearch} busy={busy} />}
        {page === 'scout' && <VentureScoutPage candidates={candidates} onAudit={runAudit} audit={audit} busy={busy} />}
        {page === 'calibration' && <CalibrationPage calibration={calibration} summary={summary} />}
        {page === 'health' && <HealthPage health={health} summary={summary} matching={matchingStatus} />}
      </main>
    </div>
    <EvidenceDrawer detail={sourceDetail} loading={sourceLoading} onClose={() => { setSourceDetail(null); setSourceLoading(false) }} onFact={inspectFact} />
  </div>
}
