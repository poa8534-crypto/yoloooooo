import { ResearchReport, CandidateHistory, DesignDetails } from './ResearchReport'
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { readRoute, routeHash, matchesDecision, RESEARCH_PHASES, researchPhase, type PageId } from './routing'

type Fact = { id: string; text: string; source_ids: string[]; freshness: string; verification_state: string }
type Proposal = {
  concept_title: string; core_loop: string; differentiator: string; build_steps: string[]
  risks: string[]; questions: string[]; supporting_fact_ids: string[]
  // Authored only by a Venture Scout audit; a Hunter concept leaves them empty.
  executive_summary?: string; opportunity_gap?: string; competitive_notes?: string[]
}
type Candidate = {
  id: string; external_id: string; display_name: string; facts: Fact[]; proposal: Proposal | null; proposal_id?: string | null
  cited_fact_ids?: string[]; withdrawn_fact_ids?: string[]; has_audit?: boolean
  decision: string; decision_id: string | null; score: number | null; confidence: number | null
}
interface AuditResult {
  candidate_id: string; proposal_id?: string | null; proposal?: Proposal | null
  risks?: string[]; note?: string; audit_id?: string | null; evidence_state?: string
  gates?: AuditGate[]; cited_fact_ids?: string[]; withdrawn_fact_ids?: string[]
  unresolved_concerns?: string[]; revision_applied?: boolean
}

// One source, one round. Recorded per search so that "answered but returned
// nothing we could use" stays distinguishable from "never asked".
type DiscoveryDiagnostic = { source: string; query: string; usable_leads: number; selected_leads?: number; engine_errors?: number }

function discoveryTotals(rows: DiscoveryDiagnostic[] = []) {
  const totals: Record<string, { found: number; used: number; engineErrors: number; rounds: number }> = {}
  for (const row of rows) {
    const entry = totals[row.source] || { found: 0, used: 0, engineErrors: 0, rounds: 0 }
    entry.found += row.usable_leads ?? 0
    entry.used += row.selected_leads ?? 0
    entry.engineErrors += row.engine_errors ?? 0
    entry.rounds += 1
    totals[row.source] = entry
  }
  return totals
}

type Run = {
  id: string; niche: string; status: string; message: string; created_at: string; completed_at: string | null
  candidates: Candidate[]; passing_results: Candidate[]
  progress?: { stage: string; elapsed_seconds: number; remaining_seconds: number; usage: Record<string, number>; stop_reason?: string; round?: number; search_queries?: string[]; discovery_sources?: DiscoveryDiagnostic[]; questions: Array<{ id: string; question: string; state: string }> }
}
type Calibration = {
  phase: string; complete_clusters: number; required_clusters: number; scoring_active: boolean
  model_version: string | null; heldout_precision: number | null; heldout_recommendations: number | null; reason: string
}
type DependencyReport = { name: string; configured: boolean; state: string; detail: string; checked_at: string | null }
type Health = {
  status: string; database: string
  ollama: { available: boolean; primary_present: boolean; fallback_present: boolean; base_url: string }
  embeddings: { package_installed: boolean; model_name: string; last_association_used_embeddings: boolean | null }
  connectors: { tavily_configured: boolean; youtube_configured: boolean }
  scheduler: { timezone: string; daily_at: string; last_snapshot: null | { completed_at?: string; counts?: Record<string, number> } }
  calibration: Calibration
  dependencies?: DependencyReport[]
  build?: { commit: string; short_commit: string; dirty: boolean | null; branch: string;
    committed_at: string | null; started_at: string; source: string }
}
type DashboardSummary = {
  service_started_at: string; uptime_seconds: number; collection_started_at: string | null
  collection_age_seconds: number; latest_capture_at: string | null
  counts: {
    unique_publishers: number; source_artifacts: number; artifact_bytes: number; observations: number
    verified_facts: number; unique_games: number; candidate_clusters: number; proposals: number; associations: number
    conflicts: number; research_runs: number; unmeasured_artifacts: number
  }
  source_tiers: Record<string, number>
  activity: Array<{ kind: string; actor: string; message: string; status: string; at: string; target_id: string }>
}
type TimelinePoint = { day: string; artifacts: number; observations: number; facts: number; associations: number }
type AuditGate = { label: string; passed: boolean; detail: string; state: string }
type AuditReadiness = { candidate_id: string; ready: boolean; gates: AuditGate[]; invariants: AuditGate[] }
type Source = {
  id: string; url: string; publisher_owner: string; retrieval_method: string; captured_at: string
  sha256: string; content_type: string; source_tier: string; is_discovery_only: boolean
  raw_size: number; observation_count: number
}
type SourceDetail = {
  focusedFact?: string
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

type Tone = 'verified' | 'proposal' | 'inference' | 'override' | 'conflict' | 'insufficient'
// Two distinct operations, matching the job API. Analyze works from
// evidence alone; Audit critiques one exact Hunter proposal version.
type ScoutOperation = 'analyze_game' | 'audit_idea'
type JobEvent = { sequence: number; stage: string; detail: string; at: string; untrusted?: boolean; model?: string }
type AuditJob = {
  id: string; candidate_id: string; operation: ScoutOperation; proposal_id: string | null
  status: string; created_at: string; completed_at: string | null; model_attempts: number
  audit_id: string | null; error: string | null; remaining_seconds: number; events: JobEvent[]
}
const JOB_ACTIVE = ['queued', 'running']

const navigation: Array<{ group: string; items: Array<[PageId, string, string]> }> = [
  { group: 'Intelligence', items: [['home', '⌂', 'Command Center'], ['ideas', '◉', 'Idea Panel'], ['sources', '▦', 'Sources'], ['market', '◬', 'Market Pulse'], ['history', '≡', 'Agent History']] },
  { group: 'Engine', items: [['matching', '⌘', 'Matching Engine'], ['meta', '◎', 'Meta Hunter'], ['scout', '◈', 'Venture Scout']] },
  { group: 'System', items: [['calibration', '☷', 'Collection & Calibration'], ['health', '⌁', 'System Health']] },
]

const pageNames: Record<PageId, string> = {
  home: 'Home', ideas: 'Ideas and game dossiers', sources: 'Sources', matching: 'Matching Engine',
  meta: 'Meta Hunter', scout: 'Venture Scout', history: 'Agent History', market: 'Market Pulse', calibration: 'Collection & Calibration', health: 'System Health',
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
  }).format(new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : value + "Z"))
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
    <nav aria-label="Main navigation">{navigation.map(section => <div className="nav-group" key={section.group}><span>{section.group}</span>{section.items.map(([id]) => <button key={id} aria-current={page === id ? 'page' : undefined} className={page === id ? 'active' : ''} onClick={() => onNavigate(id)}>{pageNames[id]}{id === 'meta' && metaRunning && <em>Running</em>}</button>)}</div>)}</nav>
    <div className="sidebar-footer"><span>Local to this PC</span><small>{health?.database === 'connected' ? 'Evidence database connected' : 'Checking database'}</small></div>
  </aside>
}

function TopBar({ page, calibration, refreshing, onRefresh }: { page: PageId; calibration: Calibration | null; refreshing: boolean; onRefresh: () => void }) {
  return <><header className="topbar"><span>Roblox research · local workspace</span><button className="secondary" onClick={onRefresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh data'}</button></header>
  <div className="page-title"><h1>{pageNames[page]}</h1><Badge>{calibration?.scoring_active ? 'Scoring active' : 'Research only'}</Badge></div></>
}

type TimelineRange = '7' | '30' | 'all'

function CommandCenter({ summary, timeline, sources, runs, health, calibration, onSource, onNavigate, onCandidate }: {
  summary: DashboardSummary | null; timeline: TimelinePoint[]; sources: Source[]; runs: Run[]; health: Health | null
  calibration: Calibration | null; onSource: (source: Source) => void; onNavigate: (page: PageId) => void; onCandidate: (id: string) => void
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
      <Metric label="Unique games" value={formatCount(counts?.unique_games)} note="Canonical Roblox universes; not niche clusters" />
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
      {candidates.length ? <div className="table-scroll"><table><thead><tr><th>Candidate / niche</th><th>State</th><th>Facts</th><th>Confidence</th><th>Score</th><th>Action</th></tr></thead><tbody>{candidates.slice(0, 12).map(item => <tr key={item.id}><td><strong>{item.display_name}</strong><small>{item.niche} · ID {item.external_id}</small></td><td><Badge tone={toneFor(item.decision)}>{item.decision.replaceAll('_', ' ')}</Badge></td><td className="numeric">{item.facts.length}</td><td className="numeric">{item.confidence == null ? '—' : `${item.confidence.toFixed(1)}%`}</td><td className="numeric">{item.score == null ? 'Locked' : item.score.toFixed(1)}</td><td><button className="text-button" onClick={() => onCandidate(item.id)}>Inspect →</button></td></tr>)}</tbody></table></div> : <EmptyState title="No tracked market data yet">The first successful Meta Hunter run will populate candidates here. No preview records are shown as real evidence.</EmptyState>}
    </article>
    <article className="data-panel"><div className="panel-heading"><div><span>Recent captures</span><h2>Evidence ingestion stream</h2></div><button className="secondary" onClick={() => onNavigate('sources')}>View all sources</button></div>
      {sources.length ? <div className="table-scroll"><table><thead><tr><th>Publisher / endpoint</th><th>Tier</th><th>Captured</th><th>Yield</th><th>SHA256</th><th /></tr></thead><tbody>{sources.slice(0, 8).map(source => <tr key={source.id}><td><strong>{source.publisher_owner}</strong><small>{source.retrieval_method.replaceAll('_', ' ')}</small></td><td><Badge tone={source.is_discovery_only ? 'insufficient' : 'verified'}>{source.source_tier}</Badge></td><td>{formatDate(source.captured_at)}</td><td className="numeric">{source.observation_count} obs</td><td><code>{source.sha256.slice(0, 10)}…{source.sha256.slice(-6)}</code></td><td><button className="text-button" onClick={() => onSource(source)}>Inspect</button></td></tr>)}</tbody></table></div> : <EmptyState title="Evidence library is ready">Sources will appear after research captures the first API response.</EmptyState>}
    </article>
    {!counts?.source_artifacts && <div className="setup-state"><div><span>01</span><strong>{health?.connectors.tavily_configured && health?.connectors.youtube_configured ? 'Connectors configured' : 'Connector setup incomplete'}</strong><small>{health?.connectors.tavily_configured && health?.connectors.youtube_configured ? 'Tavily and YouTube keys are loaded locally.' : 'Check System Health before starting a run.'}</small></div><div><span>02</span><strong>Matching in shadow mode</strong><small>Unvalidated fuzzy matches cannot reach scoring.</small></div><div><span>03</span><strong>Start first research run</strong><small>Choose a niche in the Meta Hunter workspace.</small><button className="primary" onClick={() => onNavigate('meta')}>Open Meta Hunter</button></div><div><span>04</span><strong>Calibration remains locked</strong><small>{calibration?.reason || 'Awaiting complete evidence windows.'}</small></div></div>}
  </section>
}

type ActivityEvent = { sequence: number; stage: string; detail: string; at: string; model?: string; attempt?: number }

const STAGE_LABEL: Record<string, string> = {
  started: 'Audit requested', gates: 'Gates evaluated', gate_blocked: 'Gate blocked',
  evidence: 'Evidence packed', queued: 'Waiting for the model', draft_started: 'Pass 1 — drafting',
  attempt: 'Asking the model', attempt_refused: 'Answer refused', draft_ready: 'Draft accepted',
  critique_started: 'Pass 2 — self-critique', critique_ready: 'Critique accepted',
  critique_skipped: 'Critique skipped', revision_started: 'Pass 3 — revising',
  revision_ready: 'Revision accepted', revision_skipped: 'Revision skipped',
  proposal_accepted: 'Design accepted', citations: 'Citations re-verified',
  stored: 'Written to the ledger', blocked: 'Blocked',
  interrupted: 'Interrupted by restart',
}

// An audit takes minutes and used to show nothing but a spinner, so a working
// run and a stuck one looked identical. The feed is backed by a durable
// SQLite ledger and in-memory broker, so progress survives service restarts.
function useAuditJob(candidateId: string) {
  const [job, setJob] = useState<AuditJob | null>(null)
  const [jobError, setJobError] = useState('')
  const [workOpen, setWorkOpen] = useState(false)
  const [stored, setStored] = useState<AuditResult | null>(null)
  const running = Boolean(job && JOB_ACTIVE.includes(job.status))

  // A finished audit lives in the ledger, so selecting a candidate reads back
  // whatever it already has rather than showing an empty panel.
  const reload = useCallback(() => {
    if (!candidateId) { setStored(null); return }
    api<AuditResult>(`/api/candidates/${candidateId}/audit`)
      .then(setStored).catch(() => setStored(null))
  }, [candidateId])
  useEffect(() => { setStored(null); setJob(null); setJobError(''); reload() }, [candidateId, reload])

  // Follow the job by polling rather than holding a request open. Polling also
  // recovers a job that was already running when the page loaded, which a
  // stream opened on submit would miss.
  useEffect(() => {
    if (!job || !JOB_ACTIVE.includes(job.status)) return
    const timer = window.setInterval(async () => {
      try {
        const next = await api<AuditJob>(`/api/audit-jobs/${job.id}`)
        setJob(next)
        if (!JOB_ACTIVE.includes(next.status)) reload()
      } catch (caught) { setJobError((caught as Error).message) }
    }, 1200)
    return () => window.clearInterval(timer)
  }, [job, reload])

  async function start(operation: ScoutOperation, proposalId?: string | null) {
    if (!candidateId) return
    setJobError(''); setWorkOpen(true)
    const query = new URLSearchParams({ operation })
    if (operation === 'audit_idea' && proposalId) query.set('proposal_id', proposalId)
    try {
      setJob(await api<AuditJob>(`/api/candidates/${candidateId}/audit-jobs?${query}`, { method: 'POST' }))
    } catch (caught) { setJobError((caught as Error).message) }
  }

  async function cancel() {
    if (!job) return
    try { setJob(await api<AuditJob>(`/api/audit-jobs/${job.id}/cancel`, { method: 'POST' })) }
    catch (caught) { setJobError((caught as Error).message) }
  }

  // A job interrupted by a restart keeps its original deadline and attempt
  // count. Resuming stays a decision rather than something the service does on
  // startup, so it needs a control.
  async function resume() {
    if (!job) return
    try { setJob(await api<AuditJob>(`/api/audit-jobs/${job.id}/resume`, { method: 'POST' })) }
    catch (caught) { setJobError((caught as Error).message) }
  }

  return { job, jobError, running, workOpen, setWorkOpen, stored, start, cancel, resume }
}

function BackgroundWorkDrawer({ job, events, open, onClose, onCancel, onResume }: {
  job: AuditJob | null; events: JobEvent[]; open: boolean
  onClose: () => void; onCancel: () => void; onResume?: () => void
}) {
  const panel = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    panel.current?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close.current() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); previous?.focus?.() }
  }, [open])

  const running = job ? JOB_ACTIVE.includes(job.status) : false
  return <div className={`drawer-scrim ${open ? 'open' : ''}`} onMouseDown={event => { if (event.currentTarget === event.target) onClose() }}>
    <aside className="evidence-drawer" ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Background work">
      <header>
        <div><span>Background work</span><strong>{job ? `${job.status}${running ? ` · ${Math.round(job.remaining_seconds)}s left` : ''}` : 'Nothing running'}</strong></div>
        <div className="drawer-header-actions">
          {running && <button className="text-button" onClick={onCancel}>Cancel run</button>}
          {job?.status === 'interrupted' && onResume && <button className="text-button" onClick={onResume}>Resume</button>}
          <button aria-label="Close background work" onClick={onClose}>×</button>
        </div>
      </header>
      <div className="drawer-body">
        {job && <p className="body-copy">Job <code>{job.id.slice(0, 8)}</code> · {job.operation.replaceAll('_', ' ')} · {job.model_attempts} model attempt(s){job.error ? ` · ${job.error}` : ''}</p>}
        {events.length ? <section className="work-log">{events.map(event => event.untrusted
          ? <details key={event.sequence} className="brief-fold reasoning">
              <summary>Model reasoning{event.model ? ` · ${event.model}` : ''}<span>{event.detail.length} chars</span></summary>
              <p className="reasoning-text">{event.detail}</p>
              <small>Written by the model, not by the pipeline. Shown so you can see what it worked through; it is not evidence and nothing downstream reads it.</small>
            </details>
          : <article key={event.sequence}>
              <span>{STAGE_LABEL[event.stage] || event.stage.replaceAll('_', ' ')}</span>
              <p>{event.detail}</p>
              {event.model && <small>{event.model}</small>}
            </article>)}
        </section> : <EmptyState title="Nothing running">Start an analysis or an audit and every step appears here, including the model's own reasoning.</EmptyState>}
      </div>
    </aside>
  </div>
}

function IdeasPanel({ runs, selectedId, onSelect, onInspectFact }: {
  runs: Run[]; selectedId: string; onSelect: (id: string) => void; onInspectFact: (id: string) => void
}) {
  const [filter, setFilter] = useState<'all' | 'research_more' | 'recommend' | 'blocked_conflict'>('all')
  const allIdeas = runs.flatMap(run => run.candidates.map(candidate => ({ candidate, run })))
  const [kind, setKind] = useState<'ideas' | 'games'>('ideas')
  const ideas = allIdeas.filter(item => (kind === 'games' || Boolean(item.candidate.proposal)) && matchesDecision(item.candidate.decision, filter))
  const active = allIdeas.find(item => item.candidate.id === selectedId)
  return <><div className="filter-row" aria-label="Record type"><button aria-pressed={kind === 'ideas'} onClick={() => setKind('ideas')}>Generated ideas</button><button aria-pressed={kind === 'games'} onClick={() => setKind('games')}>Game dossiers</button></div><IdeaPanelBody active={active} ideas={ideas} filter={filter} setFilter={setFilter}
    onSelect={onSelect} onInspectFact={onInspectFact} /></>
}

function IdeaPanelBody({ active, ideas, filter, setFilter, onSelect, onInspectFact }: {
  active: { candidate: Candidate; run: Run } | undefined
  ideas: { candidate: Candidate; run: Run }[]
  filter: string; setFilter: (value: 'all' | 'research_more' | 'recommend' | 'blocked_conflict') => void
  onSelect: (id: string) => void; onInspectFact: (id: string) => void
}) {
  // The audit button used to be unconditional, so a candidate whose gates
  // could never pass returned a blocked audit in two seconds and the brief
  // said only "Proposal not generated" -- which reads as the model failing
  // rather than the request never reaching it.
  const candidateId = active?.candidate.id || ''
  const [readiness, setReadiness] = useState<AuditReadiness | null>(null)
  useEffect(() => {
    let cancelled = false
    setReadiness(null)
    if (!candidateId) return
    api<AuditReadiness>(`/api/candidates/${candidateId}/audit-readiness`)
      .then(result => { if (!cancelled) setReadiness(result) })
      .catch(() => { if (!cancelled) setReadiness(null) })
    return () => { cancelled = true }
  }, [candidateId])
  // An audit costs minutes and is already in the ledger. Without this the
  // brief came back empty after a reload, which reads as the audit never
  // having run.
  const { job, jobError, running, workOpen, setWorkOpen, stored, start, cancel, resume } = useAuditJob(candidateId)
  // The audit is bound to the Hunter proposal when the game has one, and works
  // from the evidence alone when it does not. The button used to do only the
  // first and refuse everything else.
  const operation: ScoutOperation = active?.candidate.proposal_id ? 'audit_idea' : 'analyze_game' 
  const shown = stored
  const blocking = (readiness?.gates || []).filter(gate => gate.state === 'fail' || gate.state === 'missing')
  const blockedAudit = shown && !shown.proposal ? shown : null
  if (active) {
    const { candidate, run } = active
    const proposal = shown?.proposal || candidate.proposal
    const citedIds = shown?.proposal ? shown.cited_fact_ids : candidate.cited_fact_ids
    const withdrawnIds = shown?.proposal ? shown.withdrawn_fact_ids : candidate.withdrawn_fact_ids
    return <section className="workspace analyst-brief"><div className="brief-toolbar"><button className="text-button" onClick={() => onSelect('')}>← Back to ideas</button><div><Badge tone={toneFor(candidate.decision)}>{candidate.decision.replaceAll('_', ' ')}</Badge><Badge tone="verified">{candidate.facts.length} verified facts</Badge></div><button className="primary" disabled={running || blocking.length > 0} onClick={() => start(operation, candidate.proposal_id)} title={blocking.length ? blocking.map(gate => `${gate.label}: ${gate.detail}`).join(' · ') : 'Scout reads the evidence, drafts, critiques its own draft and revises. This takes several minutes.'}>{running ? 'Scout is deliberating…' : blocking.length ? 'Audit blocked by gates' : operation === 'audit_idea' ? 'Audit this idea' : 'Analyze this game'}</button><button className="text-button" onClick={() => setWorkOpen(true)}>See what it is doing{running ? ' ●' : ''}</button>{running && <button className="text-button" onClick={cancel}>Cancel</button>}</div>
      {jobError && <div className="warning-box"><strong>The run could not be started</strong><span>{jobError}</span></div>}
      <BackgroundWorkDrawer job={job} events={job?.events || []} open={workOpen}
        onClose={() => setWorkOpen(false)} onCancel={cancel} onResume={resume} />
      {!running && shown?.proposal && <div className={`audit-result-banner${shown.unresolved_concerns?.length ? ' incomplete' : ''}`}>
        <div><strong>{shown.unresolved_concerns?.length ? 'Venture Scout audit incomplete' : 'Venture Scout audit complete'}</strong><span>{shown.proposal.concept_title} · {(shown.cited_fact_ids || []).length} cited fact(s){shown.withdrawn_fact_ids?.length ? `, ${shown.withdrawn_fact_ids.length} withdrawn` : ''}</span></div>
        <div><a href="#brief-1" onClick={event => { event.preventDefault(); document.getElementById("brief-1")?.scrollIntoView() }}>Read the brief</a>{shown.audit_id && <a href={`/api/audits/${shown.audit_id}`} target="_blank" rel="noreferrer">Audit record</a>}<button type="button" className="text-button" onClick={() => setWorkOpen(true)}>How it ran</button></div>
      </div>}
      {running && <div className="warning-box"><strong>Venture Scout is running</strong><span>Reading the evidence, drafting, critiquing its own draft and revising it. Several minutes on a local model. <button className="text-button" onClick={() => setWorkOpen(true)}>Watch it work</button></span></div>}
      {blocking.length > 0 && <div className="warning-box"><strong>Audit cannot run yet</strong>{blocking.map(gate => <p key={gate.label}>{gate.label}: {gate.detail}</p>)}</div>}
      {!running && !!shown?.unresolved_concerns?.length && <div className="warning-box">
        <strong>The audit raised concerns it never answered</strong>
        <span>It criticised its own draft and the revision that should have addressed the critique did not arrive, so this design is the unrevised draft. Treat it as work in progress.</span>
        {shown.unresolved_concerns.map(concern => <p key={concern}>{concern}</p>)}</div>}
      {blockedAudit && blocking.length === 0 && <div className="warning-box"><strong>Last audit was recorded without a proposal</strong>{(blockedAudit.risks || []).map(risk => <p key={risk}>{risk}</p>)}</div>}
      <article className="brief-hero"><span>Research dossier / {run.niche}</span><h2>{proposal?.concept_title || candidate.display_name}</h2><p>{proposal?.core_loop || 'A detailed proposal will appear after the constrained local model completes a schema-valid run.'}</p><div className="metrics-grid compact"><Metric label="Engine decision" value={candidate.decision.replaceAll('_', ' ')} note="Deterministic" /><Metric label="Evidence facts" value={candidate.facts.length} note="Clickable provenance" /><Metric label="Confidence" value={candidate.confidence == null ? '—' : `${candidate.confidence.toFixed(1)}%`} note={candidate.confidence == null ? 'Not computed' : 'Evidence quality'} /><Metric label="Market score" value={candidate.score == null ? 'Locked' : candidate.score.toFixed(1)} note={candidate.score == null ? 'Calibration inactive' : 'Frozen artifact'} /></div></article>
      <div className="brief-layout"><nav className="brief-index"><span>Research brief</span>{['Executive summary', 'Why this idea', 'Demand history', 'Competitors', 'Opportunity gap', 'Twist lab', '72-hour MVP', 'Risk register', 'Provenance'].map((item, index) => <a key={item} href={`#brief-${index + 1}`} onClick={event => { event.preventDefault(); document.getElementById(`brief-${index + 1}`)?.scrollIntoView() }}>{String(index + 1).padStart(2, '0')}. {item}</a>)}</nav>
        <div className="brief-sections"><article id="brief-1" className="data-panel"><div className="section-number">01</div><h2>Executive summary</h2>{proposal ? <><div className="classified verified"><Badge tone="verified">Verified context</Badge><p>{candidate.facts.length} facts are available from captured evidence. Use the provenance section to inspect each exact source chain.</p></div><div className="classified proposal"><Badge tone="proposal">Model proposal</Badge><p>{proposal.executive_summary || proposal.core_loop}</p></div><div className="classified inference"><Badge tone="inference">Analyst interpretation</Badge><p>The proposal is a design hypothesis. It is not evidence that the market or game will succeed.</p></div></> : <EmptyState title="Proposal not generated">Run Venture Scout after evidence and matching gates allow an audit.</EmptyState>}</article>
          <article id="brief-2" className="data-panel"><div className="section-number">02</div><h2>Why this idea was chosen</h2><p className="body-copy">The engine state is <strong>{candidate.decision.replaceAll('_', ' ')}</strong>. Selection reasons must come from deterministic decision records; the local model cannot author a verdict.</p><div className="fact-stack">{candidate.facts.map(fact => <button key={fact.id} onClick={() => onInspectFact(fact.id)}><Badge tone="verified">Verified fact</Badge><span>{fact.text}</span><code>{fact.id}</code></button>)}</div></article>
          <article id="brief-3" className="data-panel"><div className="section-number">03</div><h2>Demand and trend history</h2><CandidateHistory candidateId={candidate.id} /></article>
          <article id="brief-4" className="data-panel"><div className="section-number">04</div><h2>Competitor landscape</h2><p>Discovered peers, not validated niche competitors. Source-reported at capture time.</p>{(() => {
            // This listed every peer in the run with two full sentences each,
            // so one run pushed a wall of near-identical prose between the
            // reader and the rest of the brief.
            const peers = run.candidates.filter(peer => peer.id !== candidate.id).map(peer => {
              const read = (pattern: RegExp) => {
                const found = peer.facts.find(fact => pattern.test(fact.text))
                const digits = found?.text.match(/([\d,]+)/)
                const parsed = digits ? Number(digits[1].replaceAll(',', '')) : NaN
                return { value: Number.isFinite(parsed) ? parsed.toLocaleString() : '—', id: found?.id }
              }
              return { id: peer.id, name: peer.display_name, ccu: read(/concurrent players/), visits: read(/lifetime visits/) }
            })
            return peers.length ? <details className="brief-fold"><summary>Discovered peers<span>{peers.length}</span></summary>
              <div className="table-scroll"><table><thead><tr><th>Experience</th><th>Players at capture</th><th>Lifetime visits</th></tr></thead>
                <tbody>{peers.map(peer => <tr key={peer.id}>
                  <td>{peer.name}</td>
                  <td>{peer.ccu.id ? <button type="button" className="text-button" onClick={() => onInspectFact(peer.ccu.id!)}>{peer.ccu.value}</button> : peer.ccu.value}</td>
                  <td>{peer.visits.id ? <button type="button" className="text-button" onClick={() => onInspectFact(peer.visits.id!)}>{peer.visits.value}</button> : peer.visits.value}</td>
                </tr>)}</tbody></table></div></details>
              : <EmptyState title="No peers captured">This run tracked no other experience.</EmptyState>
          })()}</article>
          <article id="brief-5" className="data-panel"><div className="section-number">05</div><h2>Opportunity gap</h2><div className="classified inference"><Badge tone="inference">Unvalidated hypothesis</Badge><p>{proposal?.opportunity_gap || proposal?.differentiator || "No admissible proposal. More evidence is needed."}</p><p>Niche relevance and absence of competition have not been established.</p></div></article>
          <article id="brief-6" className="data-panel"><div className="section-number">06</div><h2>Twist lab</h2>{proposal ? <div className="twist-grid"><div><Badge tone="proposal">Core loop</Badge><h3>{proposal.concept_title}</h3><p>{proposal.core_loop}</p></div><div><Badge tone="proposal">Differentiator</Badge><h3>Distinctive layer</h3><p>{proposal.differentiator}</p></div></div> : <EmptyState title="No model proposal">A schema-valid proposal has not been stored.</EmptyState>}</article>
          <article id="brief-7" className="data-panel"><div className="section-number">07</div><h2>72-hour MVP plan</h2>{proposal && <DesignDetails design={proposal} facts={candidate.facts} onInspect={onInspectFact} />}{proposal ? <div className="milestone-grid">{proposal.build_steps.map((step, index) => <div key={step}><span>Phase {index + 1}</span><strong>{step}</strong><small>Scope and timing require human confirmation.</small></div>)}</div> : <EmptyState title="MVP plan unavailable">Run the Venture Scout audit to generate a constrained proposal.</EmptyState>}</article>
          <article id="brief-8" className="data-panel"><div className="section-number">08</div><h2>Risk register</h2>{proposal?.risks.length ? <div className="risk-list">{proposal.risks.map(risk => <div key={risk}><Badge tone="proposal">Model risk</Badge><p>{risk}</p><span>Requires human assessment</span></div>)}</div> : <EmptyState title="No risk record">The audit has not produced a valid risk checklist.</EmptyState>}</article>
          <article id="brief-9" className="data-panel"><div className="section-number">09</div><h2>Provenance and audit trail</h2>{!!withdrawnIds?.length && <div className="warning-box"><strong>Citations withdrawn since the audit ran</strong><span>These were cited when the proposal was written and no longer resolve to admissible evidence, so they are not shown as verified.</span>{withdrawnIds.map(id => <p key={id}><code>{id}</code></p>)}</div>}
            <div className="provenance-grid"><div><span>Cited and re-verified</span>{(citedIds || []).map(id => <button className="text-button" key={id} onClick={() => onInspectFact(id)}>{candidate.facts.find(fact => fact.id === id)?.text || `Fact ${id.slice(0, 8)}`}</button>)}{!citedIds?.length && <small>The proposal cites no evidence that still resolves.</small>}</div><div><span>Supporting facts</span><details className="brief-fold"><summary>All captured facts<span>{candidate.facts.length}</span></summary>{candidate.facts.map(fact => <button className="text-button" key={fact.id} onClick={() => onInspectFact(fact.id)}>{fact.text}</button>)}</details></div><div><span>Source artifacts</span><strong>{new Set(candidate.facts.flatMap(fact => fact.source_ids)).size}</strong></div><div><span>Research run</span><code>{run.id}</code><small>{formatDate(run.created_at)}</small></div></div></article>
        </div></div>
    </section>
  }
  return <section className="workspace"><div className="section-intro"><div><span>Senior analyst dossiers</span><h2>Idea Panel</h2><p>Every idea is separated into verified evidence, deterministic decisions and model proposals.</p></div><div className="filter-row">{([['all', 'All'], ['research_more', 'Research only'], ['recommend', 'Recommended'], ['blocked_conflict', 'Blocked']] as const).map(([value, label]) => <button key={value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div></div>
    {ideas.length ? <div className="ideas-list">{ideas.map(({ candidate, run }) => <button key={candidate.id} onClick={() => onSelect(candidate.id)}><div><span>{run.niche}</span><h3>{candidate.proposal?.concept_title || candidate.display_name}</h3><p>{candidate.proposal?.core_loop || 'Evidence collected; proposal not yet generated.'}</p></div><div><Badge tone={toneFor(candidate.decision)}>{candidate.decision.replaceAll('_', ' ')}</Badge>{candidate.has_audit && <Badge tone="verified">Scout audit ready</Badge>}<span>{candidate.facts.length} facts</span><span>{formatDate(run.created_at)}</span><b>Open full brief →</b></div></button>)}</div> : <EmptyState title={filter === 'all' ? "No ideas yet" : "No candidates in this state"}>{filter === 'all' ? 'Meta Hunter has not produced a source-backed candidate. Start a research run from its workspace.' : 'No tracked candidate currently holds that engine decision.'}</EmptyState>}
  </section>
}

type SourcePage = { items: Source[]; total: number; offset: number; limit: number; next_offset: number | null }

const SOURCE_PAGE = 50

// The filter used to run over whichever rows the dashboard already held, so a
// search reported nothing for every record past the first hundred. Both the
// filter and the paging happen in the database now, and the page says how many
// records the filter actually matched.
type PillarReading = { key: string; label: string; unit: string; value: number | null
  state: string; detail: string; observations: number; basis: string[] }
type GamePillars = { version: string; universe_id: string; genre: string; observations: number
  shelves: string[]; pillars: PillarReading[]; measured: number; note: string }
type MarketPulse = {
  captured_at: string | null; samples: number; rows: number; universes?: number
  shelves: Array<{ sort_id: string; games: number }>
  genres: Array<{ genre: string; games: number; share: number }>
  rising: Array<{ universe_id: string; name: string; rank: number; player_count: number
    genre: string; up_votes: number; down_votes: number }>
  pillars_version?: string; note: string
}

const SHELF_NAMES: Record<string, string> = {
  'up-and-coming': 'Up-and-Coming', 'top-trending': 'Top Trending',
  'top-playing-now': 'Top Playing Now', 'fun-with-friends': 'Fun with Friends',
  'top-revisited': 'Top Revisited', 'top-earning': 'Top Earning',
}

// A rate is only as good as the interval it was measured over, so the reading's
// age is shown with the same weight as the reading itself.
function ageOf(iso: string | null, now: number = Date.now()) {
  if (!iso) return null
  const minutes = Math.round((now - new Date(iso).getTime()) / 60000)
  if (minutes < 60) return { text: minutes + ' min ago', stale: false }
  const hours = minutes / 60
  return { text: hours.toFixed(1) + ' h ago', stale: hours > 2 }
}

function formatPillar(reading: PillarReading) {
  if (reading.value === null) return '—'
  if (reading.unit === 'approval' || reading.unit === 'share of ranked games') {
    return (reading.value * 100).toFixed(1) + '%'
  }
  const rounded = Math.abs(reading.value) >= 100 ? reading.value.toFixed(0) : reading.value.toFixed(1)
  return reading.unit.includes('/') && reading.value > 0 ? '+' + rounded : rounded
}

function PillarGrid({ reading }: { reading: GamePillars }) {
  return <div className="pillar-grid">
    {reading.pillars.map(entry => <div key={entry.key}
      className={entry.state === 'measured' ? 'pillar' : 'pillar unmeasured'}>
      <span>{entry.label}</span>
      <strong>{formatPillar(entry)}</strong>
      <small>{entry.state === 'measured' ? entry.detail + ' · ' + entry.unit : entry.detail}</small>
    </div>)}
  </div>
}

function RisingGame({ game }: { game: MarketPulse['rising'][number] }) {
  const [reading, setReading] = useState<GamePillars | null>(null)
  const [error, setError] = useState('')
  const votes = game.up_votes + game.down_votes
  const approval = votes ? (game.up_votes / votes * 100).toFixed(1) + '%' : 'no votes'
  return <details className="brief-fold" onToggle={event => {
    if (!(event.currentTarget as HTMLDetailsElement).open || reading) return
    api<GamePillars>('/api/market/game/' + encodeURIComponent(game.universe_id))
      .then(setReading).catch(caught => setError((caught as Error).message))
  }}>
    <summary>{game.name || 'Universe ' + game.universe_id}
      <span>{game.player_count.toLocaleString()} playing · {approval} approve</span></summary>
    {error && <div className="warning-box"><p>{error}</p></div>}
    {reading ? <><PillarGrid reading={reading} />
      <small>{reading.note} Rules version <code>{reading.version}</code>, from {reading.observations} observation(s).</small></>
      : !error && <p>Reading the measurements…</p>}
  </details>
}

function MarketPulsePage() {
  const [pulse, setPulse] = useState<MarketPulse | null>(null)
  const [error, setError] = useState('')
  // A freshness badge computed once at mount freezes at whatever it said when
  // the page opened, so a census that goes stale while the tab is open still
  // reads as new. Re-read the clock, and re-fetch often enough to notice a
  // census landing.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    let cancelled = false
    const load = () => api<MarketPulse>('/api/market/pulse')
      .then(result => { if (!cancelled) { setPulse(result); setError('') } })
      .catch(caught => { if (!cancelled) setError((caught as Error).message) })
    load()
    const tick = window.setInterval(() => setNow(Date.now()), 30_000)
    const refresh = window.setInterval(load, 120_000)
    return () => { cancelled = true; window.clearInterval(tick); window.clearInterval(refresh) }
  }, [])
  const age = ageOf(pulse?.captured_at || null, now)
  // `workspace` is the shell every other page renders inside; a bare grid sits
  // outside the page's own width constraints and scrolls sideways.
  return <section className="workspace">
    <article className="data-panel"><div className="panel-heading"><div>
      <span>Roblox's own front page, sampled on a schedule</span><h2>Market census</h2></div>
      {age && <Badge tone={age.stale ? 'insufficient' : 'verified'}>{age.text}</Badge>}</div>
      {error && <div className="warning-box"><strong>Could not read the census</strong><p>{error}</p></div>}
      {pulse && !pulse.captured_at && <EmptyState title="No census sampled yet">{pulse.note}</EmptyState>}
      {pulse?.captured_at && <>
        <p className="body-copy">{pulse.note}</p>
        <div className="metrics-grid compact">
          <Metric label="games ranked" value={pulse.universes ?? 0} note="Distinct games in the latest census" />
          <Metric label="placements" value={pulse.rows} note="A game on three shelves counts three times" />
          <Metric label="censuses taken" value={pulse.samples} note="Two are needed before any rate can be measured" />
        </div>
        {pulse.samples < 2 && <div className="warning-box"><strong>Rates are not measurable yet</strong>
          <p>Momentum and acceleration are derivatives: they need the same game observed
            at two different times. {pulse.samples} census has been taken so far. Until
            the next one lands they report insufficient evidence rather than zero.</p></div>}
      </>}
    </article>

    {!!pulse?.shelves.length && <article className="data-panel">
      <div className="panel-heading"><div><span>Where Roblox is placing games</span><h2>Shelves</h2></div></div>
      <div className="table-scroll"><table><thead><tr><th>Shelf</th><th>Games</th></tr></thead><tbody>
        {pulse.shelves.map(shelf => <tr key={shelf.sort_id}>
          <td>{SHELF_NAMES[shelf.sort_id] || shelf.sort_id}</td>
          <td className="numeric">{shelf.games}</td></tr>)}
      </tbody></table></div></article>}

    {!!pulse?.genres.length && <article className="data-panel">
      <div className="panel-heading"><div><span>How crowded the shelf space is</span><h2>Genre saturation</h2></div></div>
      <p className="body-copy">Share of ranked games in each genre. A crowded genre is not
        automatically a bad one — it is proven demand and a wall of incumbents at the same
        time. This measures the crowding, not the verdict.</p>
      <div className="table-scroll"><table><thead><tr><th>Genre</th><th>Games</th><th>Share</th></tr></thead><tbody>
        {pulse.genres.slice(0, 10).map(entry => <tr key={entry.genre}>
          <td>{entry.genre}</td><td className="numeric">{entry.games}</td>
          <td className="numeric">{(entry.share * 100).toFixed(1)}%</td></tr>)}
      </tbody></table></div></article>}

    {!!pulse?.rising.length && <article className="data-panel">
      <div className="panel-heading"><div><span>Roblox's own answer to what grew today</span>
        <h2>Up-and-Coming</h2></div><Badge>{pulse.rising.length}</Badge></div>
      <p className="body-copy">Placement here is Roblox's judgement, not this system's. Open a
        game to see its measured pillars. Pillars are never combined into a single score;
        that requires calibration, which is locked.</p>
      {pulse.rising.map(game => <RisingGame key={game.universe_id} game={game} />)}
    </article>}
  </section>
}

function SourcesPage({ onSource }: { sources: Source[]; onSource: (source: Source) => void }) {
  const [query, setQuery] = useState('')
  const [term, setTerm] = useState('')
  const [page, setPage] = useState<SourcePage | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => { setTerm(query.trim()); setOffset(0) }, 250)
    return () => window.clearTimeout(timer)
  }, [query])
  useEffect(() => {
    let cancelled = false
    setError('')
    api<SourcePage>(`/api/sources?paged=true&limit=${SOURCE_PAGE}&offset=${offset}&q=${encodeURIComponent(term)}`)
      .then(result => { if (!cancelled) setPage(result) })
      .catch(caught => { if (!cancelled) setError((caught as Error).message) })
    return () => { cancelled = true }
  }, [term, offset])
  const items = page?.items || []
  const showing = items.length ? `${page!.offset + 1}–${page!.offset + items.length} of ${page!.total}` : `0 of ${page?.total ?? 0}`
  return <section className="workspace"><div className="section-intro"><div><span>Immutable provenance</span><h2>Sources and evidence library</h2><p>Inspect exactly where every trusted observation came from.</p></div><input className="search-input" value={query} onChange={event => setQuery(event.target.value)} aria-label="Filter sources" placeholder="Search every captured artifact" /></div>
    <div className="history-toolbar"><span>{showing}{term ? ` matching “${term}”` : ''}</span>
      <button className="text-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - SOURCE_PAGE))}>← Previous</button>
      <button className="text-button" disabled={!page?.next_offset} onClick={() => setOffset(page!.next_offset!)}>Next →</button></div>
    {error && <div className="warning-box"><strong>Sources unavailable</strong><span>{error}</span></div>}
    <article className="data-panel">{!page ? <div className="drawer-loading">Reading the evidence library…</div> : items.length ? <div className="table-scroll"><table><thead><tr><th>Publisher / resource</th><th>Tier</th><th>Captured</th><th>Type</th><th>Yield</th><th>Size</th><th>SHA256</th><th /></tr></thead><tbody>{items.map(source => <tr key={source.id}><td><strong>{source.publisher_owner}</strong><small>{source.retrieval_method.replaceAll('_', ' ')}</small></td><td><Badge tone={source.is_discovery_only ? 'insufficient' : 'verified'}>{source.source_tier}</Badge></td><td>{formatDate(source.captured_at)}</td><td>{source.content_type}</td><td className="numeric">{source.observation_count}</td><td className="numeric">{formatBytes(source.raw_size)}</td><td><code>{source.sha256.slice(0, 12)}…</code></td><td><button className="text-button" onClick={() => onSource(source)}>Inspect →</button></td></tr>)}</tbody></table></div> : <EmptyState title="No matching sources">{term ? 'Nothing in the whole library matches that search.' : 'Run research to capture evidence.'}</EmptyState>}</article>
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
    <details className="policy-details"><summary>Policy and unavailable tools</summary><p>Threshold tuning and a match sandbox are not implemented. This workspace reviews recorded associations; it cannot loosen evidence gates.</p><p>Threshold version: {status?.threshold_version || "Unknown"}. High threshold: {status?.high_threshold ?? "Unknown"}. Required margin: {status?.margin_threshold ?? "Unknown"}.</p></details>
    <div className="review-layout"><article className="data-panel review-queue"><div className="panel-heading"><div><span>Human decisions</span><h2>Association outcomes</h2></div><Badge tone="inference">{reviews.length} items</Badge></div>{reviews.length ? reviews.map(item => <button className={review?.association_id === item.association_id ? 'active' : ''} key={item.association_id} onClick={() => onSelect(item.association_id)}><strong>{item.subject_title || 'Untitled captured source'}</strong><span>{item.candidate?.display_name || 'No candidate'} · margin {item.margin.toFixed(3)}</span><Badge tone={toneFor(item.outcome)}>{item.outcome.replaceAll('_', ' ')}</Badge></button>) : <EmptyState title="Review queue is empty">Ambiguous or conflicting associations will appear here instead of being guessed.</EmptyState>}</article>
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
  // This was eight documentation steps with none of them marked, because the
  // backend used to report only one message per run. It reports a real stage
  // now, so the phases below are the ones it actually moves through and the
  // live one is marked. An unrecognised stage marks nothing rather than
  // guessing.
  const [reportId, setReportId] = useState("")
  const latest = runs.find(r => r.id === reportId) || runs[0]
  return <section className="workspace"><div className="agent-boundary"><Badge tone="proposal">Agent authority: proposal only</Badge><p>Meta Hunter can suggest concepts and search heuristics. It cannot create URLs, platform metrics, scores, confidence values or verdicts.</p><strong>Invariant enforced</strong></div>
    <div className="agent-columns"><article className="data-panel"><div className="panel-heading"><div><span>Research configuration</span><h2>Meta Hunter parameters</h2></div><Badge tone={active ? 'inference' : 'insufficient'}>{active ? active.status : 'Idle'}</Badge></div><form onSubmit={submit}><label>Research niche or question<input value={niche} minLength={3} required onChange={event => setNiche(event.target.value)} placeholder="e.g. cooperative cozy farming" /></label><div className="form-grid"><label>Region<input value="Global" disabled /></label><label>Corpus language<input value="English" disabled /></label><label>Candidate cap<input value="30 unique games · up to 3 concepts" disabled /></label><label>Search policy<input value="Discovery → primary evidence" disabled /></label></div><button className="primary" disabled={busy || Boolean(active)}>{busy ? 'Starting…' : active ? 'Research already running' : 'Start research run'}</button></form></article>
      <article className="data-panel"><div className="panel-heading"><div><span>Immutable safety rules</span><h2>Protected invariants</h2></div></div><div className="invariant-list">{[['Evidence firewall', true], ['URLs from model', false], ['Platform metrics from model', false], ['Model-authored verdicts', false], ['Strict JSON schema', true], ['Fail-closed mode', true]].map(([label, enabled]) => <div key={String(label)}><span>{label}</span><Badge tone={enabled ? 'verified' : 'conflict'}>{enabled ? 'Enabled · locked' : 'Disabled · locked'}</Badge></div>)}</div></article></div>
    <article className="data-panel"><div className="panel-heading"><div><span>Reported run state</span><h2>Pipeline</h2></div>{latest && <Badge tone={toneFor(latest.status)}>{latest.status}</Badge>}</div>
      {latest ? <div className="run-state"><strong>{latest.niche}</strong><p className="mono">{latest.message}</p><small>{active ? 'This run is still in progress.' : `Finished ${formatDate(latest.completed_at || latest.created_at)}`}</small></div> : <EmptyState title="No run reported yet">Start a research run to see its reported state.</EmptyState>}
      {(() => {
        const stage = latest?.progress?.stage || ''
        const current = latest ? researchPhase(stage, latest.status) : 0
        const round = latest?.progress?.round
        return <>
          <ol className="pipeline-list">{RESEARCH_PHASES.map((phase, index) => {
            const state = current < 0 ? 'unknown' : index < current ? 'done' : index === current ? 'current' : 'pending'
            return <li key={phase.id} className={state} aria-current={state === 'current' ? 'step' : undefined}>
              <b>{state === 'done' ? '✓' : index + 1}</b>
              <span><strong>{phase.label}{state === 'current' && phase.id === 'investigating' && round ? ` · round ${round}` : ''}</strong><small>{phase.detail}</small></span>
              {state === 'current' && <Badge tone="inference">Now</Badge>}
            </li>
          })}</ol>
          {current < 0 && <small className="gate-hint">The run reports a stage this page does not recognise: <code>{stage}</code>. Nothing is marked rather than guessing which phase it belongs to.</small>}
          {current >= 0 && <small className="gate-hint">Phases come from the stage the run persists to its checkpoint, not from a fixed script.</small>}
        </>
      })()}</article>
    <article className="data-panel"><div className="panel-heading"><div><span>Append-only run records</span><h2>Run history</h2></div></div>{runs.length ? <div className="table-scroll"><table><thead><tr><th>Niche</th><th>Status</th><th>Started</th><th>Candidates</th><th>Result</th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td><strong>{run.niche}</strong><small><code>{run.id}</code></small></td><td><Badge tone={toneFor(run.status)}>{run.status}</Badge></td><td>{formatDate(run.created_at)}</td><td className="numeric">{run.candidates.length}</td><td>{run.message}</td></tr>)}</tbody></table></div> : <EmptyState title="No Meta Hunter runs">Submit the first niche above when you are ready to collect evidence.</EmptyState>}</article>
    {latest?.progress && <article className="data-panel"><div className="panel-heading"><div><span>Where the games came from</span><h2>Discovery sources</h2></div></div>
      <p className="body-copy">Every source is asked and the run keeps whatever answers. One being unavailable is recorded as an abstention, not a failure.</p>
      {(() => { const totals = discoveryTotals(latest.progress?.discovery_sources)
        return <div className="table-scroll"><table><thead><tr><th>Source</th><th>Search attempts</th><th>Ranked leads per query</th><th>Selected leads</th><th>Needs a key</th></tr></thead><tbody>
        {([['Roblox search', 'roblox_search', false], ['Local search (SearxNG)', 'searxng_search', false], ['Tavily', 'tavily_search', true]] as const).map(([label, key, keyed]) => {
          const searches = latest.progress?.usage[key] ?? 0, seen = totals[key]
          return <tr key={key}><td>{label}{!!seen?.engineErrors && <small>{seen.engineErrors} upstream engine failures across searches</small>}</td>
            <td className="numeric">{searches}</td>
            {/* Answered but nothing survived ranking is a real outcome and must not read as "never asked". */}
            <td className="numeric">{seen ? seen.found : (searches ? '—' : 0)}</td>
            <td className="numeric">{seen ? seen.used : (searches ? '—' : 0)}</td>
            <td>{keyed ? 'yes' : 'no'}</td></tr>
        })}
      </tbody></table></div> })()}
      {!!latest.progress?.search_queries?.length && <details className="brief-fold"><summary>Planned searches<span>{latest.progress.search_queries.length}</span></summary>
        <p>Written from your niche by the query planner, then reused every round.</p>
        <ul>{latest.progress!.search_queries!.map(q => <li key={q}><code>{q}</code></li>)}</ul></details>}
    </article>}
    {latest && <><label>Inspect research run<select value={latest.id} onChange={e => setReportId(e.target.value)}>{runs.map(r => <option key={r.id} value={r.id}>{r.niche} — {r.status}</option>)}</select></label>{latest.progress && <article className="data-panel"><h3>{latest.progress.stage}</h3><p>{Math.round(latest.progress.elapsed_seconds)}s elapsed · {Math.round(latest.progress.remaining_seconds)}s {["queued", "running"].includes(latest.status) ? "remaining" : "unused budget"}</p><div className="metrics-grid compact">{Object.entries(latest.progress.usage).map(([key, value]) => <Metric key={key} label={key.replaceAll("_", " ")} value={value} note="Reserved attempts / captured entities" />)}</div>{latest.progress.questions.map(q => <p key={q.id}>{q.question} — {q.state}</p>)}</article>}<ResearchReport runId={latest.id} status={latest.status} /></>}
    <div className="api-readiness"><span>Tavily <b>{health?.connectors.tavily_configured ? 'Configured — not a live authentication check' : 'Missing key'}</b></span><span>YouTube <b>{health?.connectors.youtube_configured ? 'Configured — not a live authentication check' : 'Missing key'}</b></span><span>Roblox <b>Public interface</b></span><span>Qwen <b>{health?.ollama.primary_present ? '14B ready' : 'Unavailable'}</b></span></div>
  </section>
}

function VentureScoutPage({ candidates }: { candidates: Candidate[] }) {
  const [candidateId, setCandidateId] = useState(candidates[0]?.id || '')
  const [operation, setOperation] = useState<ScoutOperation>('analyze_game')
  const { job, jobError, running, workOpen, setWorkOpen, stored, start, cancel, resume } = useAuditJob(candidateId)
  const [readiness, setReadiness] = useState<AuditReadiness | null>(null)
  const [readinessError, setReadinessError] = useState('')
  // Only a candidate carrying a selected Meta Hunter proposal can be audited;
  // the rest fail the binding gate before the model is ever called. Default to
  // one that can actually run rather than to whichever loaded first.
  useEffect(() => { if (!candidates.some(item => item.id === candidateId)) setCandidateId((candidates.find(item => item.proposal_id) || candidates[0])?.id || '') }, [candidates, candidateId])
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
  const shown = stored
  const startJob = () => start(operation, candidate?.proposal_id)
  const cancelJob = cancel
  return <section className="workspace">
    <BackgroundWorkDrawer job={job} events={job?.events || []} open={workOpen}
      onClose={() => setWorkOpen(false)} onCancel={cancelJob} onResume={resume} />
    <div className="scout-guard"><div><strong>Venture Scout audit guardrail</strong><span>Fail-closed enforced</span></div><p>Scout does two different things. <strong>Analyze game</strong> works from the captured evidence alone and needs no Meta Hunter proposal. <strong>Audit idea</strong> critiques one exact Hunter proposal. Both are scoped to a solo beginner and a three-day MVP, and neither can invent facts or override a deterministic decision.</p></div>
    <div className="agent-columns"><article className="data-panel"><div className="panel-heading"><div><span>Solo MVP configuration</span><h2>Scout operation</h2></div></div>{candidates.length ? <><label>Operation<select value={operation} onChange={event => setOperation(event.target.value as ScoutOperation)}>
      <option value="analyze_game">Analyze game — from captured evidence alone</option>
      <option value="audit_idea">Audit idea — critique this game's Hunter proposal</option>
    </select></label><label>Candidate<select value={candidateId} onChange={event => setCandidateId(event.target.value)}>{candidates.map(item => <option key={item.id} value={item.id}>{item.display_name}{item.proposal_id ? '' : ' — no Hunter proposal'}</option>)}</select></label>
    {operation === 'audit_idea' && !candidate?.proposal_id && <small className="gate-hint">This game has no Meta Hunter proposal, so there is nothing to audit. Analyze game works from the evidence instead.</small>}<div className="form-grid"><label>Builder experience<input value="Beginner" disabled /></label><label>Team capacity<input value="Solo" disabled /></label><label>MVP scope<input value="72 hours" disabled /></label><label>Monetization<input value="Excluded from MVP" disabled /></label></div><div className="scout-actions"><button className="primary" disabled={running || !candidate || (operation === 'audit_idea' && !candidate?.proposal_id)} onClick={startJob}>{running ? 'Scout is deliberating…' : operation === 'audit_idea' ? 'Audit this idea' : 'Analyze this game'}</button><button type="button" className="text-button" onClick={() => setWorkOpen(true)}>See what it is doing{running ? ' ●' : ''}</button>{running && <button type="button" className="text-button" onClick={cancelJob}>Cancel</button>}</div>{jobError && <div className="warning-box"><strong>The run could not be started</strong><span>{jobError}</span></div>}{job?.status === 'interrupted' && <div className="warning-box"><strong>This run was interrupted</strong><span>The service stopped while it was working. Its original deadline and attempt count were kept; nothing was replayed automatically. <button type="button" className="text-button" onClick={resume}>Resume it</button></span></div>}{job && !running && job.status !== 'complete' && job.status !== 'interrupted' && <div className="warning-box"><strong>Run ended as {job.status}</strong><span>{job.error || 'No completed result was produced. Nothing was written as a finding.'}</span></div>}{readiness && !readiness.ready && <small className="gate-hint">Some gates are not met. The backend will record a blocked audit without invoking the model.</small>}</> : <EmptyState title="No game captured yet">Start a Meta Hunter research run; Scout analyses what it captures.</EmptyState>}</article>
      <article className="data-panel"><div className="panel-heading"><div><span>Measured against the ledger</span><h2>Audit gates</h2></div>{readiness && <Badge tone={readiness.ready ? 'verified' : 'insufficient'}>{readiness.ready ? 'All gates pass' : 'Gates outstanding'}</Badge>}</div>
        {readinessError && <div className="warning-box"><strong>Readiness unavailable</strong><span>{readinessError}</span></div>}
        {!candidateId ? <EmptyState title="No candidate selected">Gate state is computed per candidate; nothing is assumed.</EmptyState>
          : readiness ? <><div className="audit-gates">{readiness.gates.map(gate => <div key={gate.label}><i />{gate.label}<small>{gate.detail}</small><Badge tone={gate.passed ? 'verified' : 'insufficient'}>{gate.state.replaceAll('_', ' ')}</Badge></div>)}</div>
            <div className="invariant-note"><span>Always-on pipeline invariants</span>{readiness.invariants.map(item => <div key={item.label}><b>{item.label}</b><small>{item.detail}</small></div>)}</div></>
          : <div className="drawer-loading">Checking gates…</div>}</article></div>
    {shown?.proposal ? <div className="audit-output"><article className="data-panel"><div className="section-number">01</div><h2>{shown.proposal.concept_title}</h2><div className="classified proposal"><Badge tone="proposal">Model proposal</Badge><p>{shown.proposal.core_loop}</p></div><div className="classified inference"><Badge tone="inference">Differentiator</Badge><p>{shown.proposal.differentiator}</p></div></article><article className="data-panel"><div className="section-number">02</div><h2>72-hour milestone plan</h2><div className="milestone-grid">{shown.proposal.build_steps.map((step, index) => <div key={step}><span>Milestone {index + 1}</span><strong>{step}</strong><small>Human scope confirmation required</small></div>)}</div></article><article className="data-panel"><div className="section-number">03</div><h2>Risk and mitigation ledger</h2><div className="risk-list">{shown.proposal.risks.map(risk => <div key={risk}><Badge tone="proposal">Proposed risk</Badge><p>{risk}</p><span>Not a measured probability</span></div>)}</div></article></div> : shown ? <article className="data-panel"><div className="panel-heading"><div><span>Audit recorded without a model proposal</span><h2>Audit blocked</h2></div><Badge tone="insufficient">fail closed</Badge></div><p>The audit ran and was written to the ledger; the model was not asked for a design because the reasons below were not cleared.</p><div className="risk-list">{(shown.risks || []).map(risk => <div key={risk}><Badge tone="insufficient">Blocking reason</Badge><p>{risk}</p></div>)}</div></article> : <article className="data-panel"><EmptyState title="No Venture Scout result loaded">Pick an operation and a game, then run it. Analyze game needs only captured evidence; Audit idea needs that game's Hunter proposal. Invalid model output fails closed either way.</EmptyState></article>}
  </section>
}

type AgentRun = {
  id: string; kind: 'meta_hunter' | 'venture_scout'; created_at: string
  run_id: string | null; niche: string; candidate_id: string; candidate_name: string
  title: string; summary: string; outcome: string; model_name: string; operation?: string | null
  cited_fact_ids: string[]; cited_facts: Fact[]; withdrawn_fact_ids: string[]; blocking_reasons: string[]
  payload: Proposal | null
}

const HISTORY_PAGE = 25
const AGENT_LABEL: Record<string, string> = { meta_hunter: 'Meta Hunter', venture_scout: 'Venture Scout' }
const OUTCOME_TONE: Record<string, Tone> = { design: 'verified', concept: 'proposal', blocked: 'insufficient', unreadable: 'conflict' }

// Both agents wrote to the ledger and neither was listed anywhere, so a
// finished concept or audit could only be found by remembering which candidate
// it belonged to and opening that brief.
function AgentHistoryPage({ onInspectFact, onOpenCandidate }: { onInspectFact: (id: string) => void; onOpenCandidate: (id: string) => void }) {
  const [runs, setRuns] = useState<AgentRun[] | null>(null)
  const [total, setTotal] = useState(0)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [kind, setKind] = useState<'all' | 'venture_scout' | 'meta_hunter'>('all')
  const [query, setQuery] = useState('')
  const [openId, setOpenId] = useState('')
  // The page asked for two hundred runs and reported "N of N", which counted
  // only what it had been handed. It pages now, and the total comes from the
  // ledger.
  const reload = useCallback(() => {
    setRuns(null); setError('')
    api<{ items: AgentRun[]; total: number; next_offset: number | null }>(
      `/api/agent-runs?paged=true&limit=${HISTORY_PAGE}&offset=${offset}&kind=${kind}`)
      .then(page => { setRuns(page.items); setTotal(page.total); setNextOffset(page.next_offset) })
      .catch(caught => setError((caught as Error).message))
  }, [kind, offset])
  useEffect(() => { reload() }, [reload])
  useEffect(() => { setOffset(0) }, [kind])
  const term = query.trim().toLowerCase()
  const shown = (runs || []).filter(run => !term
    || run.title.toLowerCase().includes(term)
    || run.candidate_name.toLowerCase().includes(term)
    || run.niche.toLowerCase().includes(term))

  return <section className="workspace">
    <div className="section-intro">
      <div><span>Everything both agents have produced</span><h2>Agent History</h2>
        <p>Every Meta Hunter concept and Venture Scout audit written to the ledger, newest first. Citations are re-checked now, not replayed from the stored record.</p></div>
      <div className="filter-row">{([['all', 'All'], ['venture_scout', 'Venture Scout'], ['meta_hunter', 'Meta Hunter']] as const).map(([value, label]) =>
        <button key={value} className={kind === value ? 'active' : ''} onClick={() => setKind(value)}>{label}</button>)}</div>
    </div>
    <div className="history-toolbar">
      <input aria-label="Filter agent history" placeholder="Filter by concept, experience or niche" value={query} onChange={event => setQuery(event.target.value)} />
      <button className="text-button" onClick={reload}>Refresh</button>
      {runs && <span>{runs.length ? `${offset + 1}–${offset + runs.length}` : '0'} of {total} run(s){term ? ` · ${shown.length} shown` : ''}</span>}
      <button className="text-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - HISTORY_PAGE))}>← Previous</button>
      <button className="text-button" disabled={nextOffset === null} onClick={() => setOffset(nextOffset!)}>Next →</button>
    </div>
    {error && <div className="warning-box"><strong>History unavailable</strong><span>{error}</span></div>}
    {!runs ? <div className="drawer-loading">Loading agent history…</div>
      : shown.length ? <div className="history-list">{shown.map(run => <article key={`${run.kind}-${run.id}`} className="history-row">
        <header role="button" tabIndex={0} aria-expanded={openId === run.id} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setOpenId(current => current === run.id ? "" : run.id) } }} onClick={() => setOpenId(current => current === run.id ? '' : run.id)}>
          <div>
            <Badge tone={run.kind === 'venture_scout' ? 'inference' : 'proposal'}>{AGENT_LABEL[run.kind]}</Badge>
            <Badge tone={OUTCOME_TONE[run.outcome] || 'proposal'}>{run.outcome}</Badge>
            {run.operation && <Badge tone="inference">{run.operation.replaceAll('_', ' ')}</Badge>}
            <strong>{run.title || run.candidate_name}</strong>
          </div>
          <div><span>{run.candidate_name}</span><span>{run.niche || 'no niche recorded'}</span><span>{formatDate(run.created_at)}</span><b>{openId === run.id ? 'Hide ▴' : 'Open ▾'}</b></div>
        </header>
        {openId === run.id && <div className="history-body">
          {run.summary && <p>{run.summary}</p>}
          {!!run.blocking_reasons.length && <div className="warning-box"><strong>No design was produced</strong>{run.blocking_reasons.map(reason => <p key={reason}>{reason}</p>)}</div>}
          {!run.payload && !run.blocking_reasons.length && <p className="body-copy">The stored record does not satisfy the current evidence firewall, so it is listed but not rendered as a proposal.</p>}
          {run.payload && <DesignDetails design={run.payload} facts={run.cited_facts} onInspect={onInspectFact} />}
          {!!run.withdrawn_fact_ids.length && <div className="warning-box"><strong>Citations withdrawn since this ran</strong><span>Cited at the time; they no longer resolve to admissible evidence.</span>{run.withdrawn_fact_ids.map(id => <p key={id}><code>{id}</code></p>)}</div>}
          <div className="history-actions">
            <button className="text-button" onClick={() => onOpenCandidate(run.candidate_id)}>Open the full brief</button>
            {run.kind === 'venture_scout' && <a href={`/api/audits/${run.id}`} target="_blank" rel="noreferrer">Stored audit record</a>}
            {run.model_name && <span>{run.model_name}</span>}
          </div>
        </div>}
      </article>)}</div>
      : <EmptyState title="No agent runs yet">Neither agent has written a concept or an audit to the ledger.</EmptyState>}
  </section>
}

type Continuity = {
  days: Array<{ day: string; entities: number; observations: number }>
  captured_days: number; longest_consecutive_days: number; entities_tracked: number
  first_capture: string | null; last_capture: string | null; niche_cluster_calibration: string
}

function CalibrationPage({ calibration, summary }: { calibration: Calibration | null; summary: DashboardSummary | null }) {
  // The readiness bar was complete_clusters / required_clusters. Niche-cluster
  // calibration is not implemented, so it was always zero out of two hundred:
  // a progress indicator for a pipeline that does not exist. What is shown now
  // is what has actually been captured.
  const [continuity, setContinuity] = useState<Continuity | null>(null)
  const [continuityError, setContinuityError] = useState('')
  useEffect(() => {
    let cancelled = false
    api<Continuity>('/api/collection/continuity')
      .then(result => { if (!cancelled) setContinuity(result) })
      .catch(caught => { if (!cancelled) setContinuityError((caught as Error).message) })
    return () => { cancelled = true }
  }, [])
  const gates = [
    ['Complete 30-day windows', Boolean(calibration && calibration.complete_clusters >= calibration.required_clusters), `${calibration?.complete_clusters || 0} / ${calibration?.required_clusters || 200}`],
    ['Held-out precision ≥ 95%', Boolean(calibration?.heldout_precision && calibration.heldout_precision >= .95), calibration?.heldout_precision == null ? 'Pending' : `${(calibration.heldout_precision * 100).toFixed(2)}%`],
    ['At least ten held-out recommendations', Boolean(calibration?.heldout_recommendations && calibration.heldout_recommendations >= 10), calibration?.heldout_recommendations == null ? 'Pending' : String(calibration.heldout_recommendations)],
    ['Frozen artifact activated', Boolean(calibration?.scoring_active), calibration?.model_version || 'Not active'],
  ] as const
  return <section className="workspace"><div className={`calibration-warning ${calibration?.scoring_active ? 'active' : ''}`}><Badge tone={calibration?.scoring_active ? 'verified' : 'conflict'}>{calibration?.scoring_active ? 'Scoring active' : 'Scoring locked'}</Badge><div><strong>{calibration?.scoring_active ? 'Market-growth scoring is active' : 'Market-growth scoring remains disabled'}</strong><p>{calibration?.reason}</p></div></div><div className="metrics-grid compact"><Metric label="Entities tracked" value={continuity?.entities_tracked ?? '—'} note="Distinct Roblox universes" /><Metric label="Days with captures" value={continuity?.captured_days ?? '—'} note="Actual snapshot days" /><Metric label="Longest unbroken run" value={continuity ? `${continuity.longest_consecutive_days} day${continuity.longest_consecutive_days === 1 ? '' : 's'}` : '—'} note="Gaps are never interpolated" /><Metric label="Held-out precision" value={calibration?.heldout_precision == null ? '—' : `${(calibration.heldout_precision * 100).toFixed(2)}%`} note="Untouched test set" /></div><div className="calibration-grid"><article className="data-panel"><div className="panel-heading"><div><span>What has actually been captured</span><h2>Snapshot continuity</h2></div></div>
      {continuityError && <div className="warning-box"><strong>Continuity unavailable</strong><span>{continuityError}</span></div>}
      {continuity ? (continuity.days.length ? <><p className="body-copy">Captures run from {continuity.first_capture} to {continuity.last_capture}. A day with no capture breaks the run; nothing is interpolated across it.</p>
        <div className="table-scroll"><table><thead><tr><th>UTC day</th><th>Entities measured</th><th>Observations</th></tr></thead>
          <tbody>{continuity.days.map(entry => <tr key={entry.day}><td>{entry.day}</td><td>{entry.entities.toLocaleString()}</td><td>{entry.observations.toLocaleString()}</td></tr>)}</tbody></table></div></>
        : <EmptyState title="Nothing captured yet">The first daily snapshot will start this table.</EmptyState>)
        : <div className="drawer-loading">Reading captured history…</div>}
      <div className="invariant-note"><span>Not included above</span><div><b>Niche-cluster calibration</b><small>Not implemented. Snapshot days are entity measurements; they are not validated niche-cluster windows, and no amount of them activates scoring on their own.</small></div></div></article><article className="data-panel"><div className="panel-heading"><div><span>Activation policy</span><h2>Decision gates</h2></div></div><div className="gate-stack">{gates.map(([label, pass, result]) => <div key={label}><i className={pass ? 'pass' : ''}>{pass ? '✓' : '×'}</i><span><strong>{label}</strong><small>{result}</small></span><Badge tone={pass ? 'verified' : 'insufficient'}>{pass ? 'Pass' : 'Locked'}</Badge></div>)}</div></article></div></section>
}

const DEPENDENCY_TONE: Record<string, Tone> = {
  reachable: 'verified', last_request_succeeded: 'verified',
  unreachable: 'conflict', last_request_failed: 'conflict',
  not_configured: 'insufficient', unknown: 'insufficient',
}

const DEPENDENCY_LABEL: Record<string, string> = {
  reachable: 'Reachable', last_request_succeeded: 'Last request succeeded',
  unreachable: 'Unreachable', last_request_failed: 'Last request failed',
  not_configured: 'Not configured', unknown: 'Never observed',
}

function HealthPage({ health, summary, matching }: { health: Health | null; summary: DashboardSummary | null; matching: MatchingStatus | null }) {
  // Configuration is not health. A present key, an expired key and a provider
  // that has been down all morning used to render identically as
  // "Authenticated", so observation is shown first and separately.
  const dependencies = health?.dependencies || []
  const failing = dependencies.filter(item => item.state === 'unreachable' || item.state === 'last_request_failed')
  const modules = [
    ['FastAPI core service', health?.status === 'ok', `Uptime ${summary ? formatDuration(summary.uptime_seconds) : '—'}`],
    ['SQLite WAL evidence ledger', health?.database === 'connected', `${summary?.counts.source_artifacts || 0} artifacts`],
    ['Local Ollama daemon', health?.ollama.available, health?.ollama.base_url || 'Unknown endpoint'],
    ['Qwen3 14B primary', health?.ollama.primary_present, health?.ollama.primary_present ? 'Installed' : 'Missing'],
    ['Qwen3 8B fallback', health?.ollama.fallback_present, health?.ollama.fallback_present ? 'Installed' : 'Missing'],
    ['Matching engine', Boolean(matching), matching?.matcher_version || 'Unavailable'],
    ['Daily snapshot scheduler', Boolean(health?.scheduler.daily_at), health ? `${health.scheduler.daily_at} ${health.scheduler.timezone}` : 'Checking'],
    ['Local embedding model', health?.embeddings.package_installed, health?.embeddings.package_installed ? `${health.embeddings.model_name}${health.embeddings.last_association_used_embeddings === false ? ' · last run used lexical fallback' : ''}` : 'fastembed not installed · lexical retrieval only'],
  ] as const
  const build = health?.build
  return <section className="workspace">
    {build && <div className="build-identity">
      <div><span>Running code</span><strong>{build.source === 'git' ? `${build.branch} @ ${build.short_commit}` : 'Unknown — not a git checkout'}</strong></div>
      <div><span>Committed</span><strong>{build.committed_at ? formatDate(build.committed_at) : '—'}</strong></div>
      <div><span>Service started</span><strong>{formatDate(build.started_at)}</strong></div>
      {build.dirty && <Badge tone="conflict">Uncommitted changes</Badge>}
      {build.dirty === false && <Badge tone="verified">Clean checkout</Badge>}
    </div>}
    {!!failing.length && <div className="warning-box"><strong>{failing.length} dependency check{failing.length > 1 ? 's are' : ' is'} failing</strong>{failing.map(item => <p key={item.name}>{item.name}: {item.detail}{item.checked_at ? ` (observed ${formatDate(item.checked_at)})` : ''}</p>)}</div>}
    <article className="data-panel"><div className="panel-heading"><div><span>Observed, not configured</span><h2>Dependency status</h2></div></div>
      <p className="body-copy">A configured credential is not evidence that a dependency works. Each row below is the last thing actually observed, with when it was observed. Metered APIs are not probed, because a probe would spend quota.</p>
      <div className="table-scroll"><table><thead><tr><th>Dependency</th><th>Configured</th><th>Observed</th><th>When</th><th>Detail</th></tr></thead>
        <tbody>{dependencies.length ? dependencies.map(item => <tr key={item.name}>
          <td>{item.name}</td>
          <td>{item.configured ? 'Yes' : 'No'}</td>
          <td><Badge tone={DEPENDENCY_TONE[item.state] || 'insufficient'}>{DEPENDENCY_LABEL[item.state] || item.state}</Badge></td>
          <td>{item.checked_at ? formatDate(item.checked_at) : '—'}</td>
          <td>{item.detail}</td>
        </tr>) : <tr><td colSpan={5}>Health has not been read yet.</td></tr>}</tbody></table></div></article>
    <div className="keyring-banner"><strong>▣ Local secret boundary engaged</strong><p>API keys stay in the ignored local environment file and are never returned to the dashboard.</p><Badge tone="verified">Credentials redacted by policy</Badge></div><div className="module-grid">{modules.map(([name, ok, detail]) => <article className="data-panel" key={name}><div><span className={`module-dot ${ok ? 'online' : ''}`} /><Badge tone={ok ? 'verified' : 'conflict'}>{ok ? (name.includes('API') ? 'Configured' : name.includes('Qwen') || name.includes('embedding') ? 'Installed' : name.includes('scheduler') ? 'Scheduled' : 'Reachable') : 'Unavailable or unchecked'}</Badge></div><h2>{name}</h2><p>{detail}</p></article>)}</div><div className="health-bottom"><article className="data-panel"><div className="panel-heading"><div><span>External connectors</span><h2>API readiness</h2></div></div><div className="quota-list"><div><span>Tavily</span><b>{health?.connectors.tavily_configured ? 'Credential present — see observed status above' : 'Not configured'}</b></div><div><span>YouTube Data API v3</span><b>{health?.connectors.youtube_configured ? 'Credential present — see observed status above' : 'Not configured'}</b></div><div><span>Roblox public interfaces</span><b>Read-only</b></div></div></article><article className="data-panel"><div className="panel-heading"><div><span>Deterministic capture</span><h2>Snapshot ledger</h2></div></div><Metric label="Last snapshot" value={formatDate(health?.scheduler.last_snapshot?.completed_at)} note={health?.scheduler.last_snapshot ? JSON.stringify(health.scheduler.last_snapshot.counts || {}) : 'No snapshot recorded'} /></article></div></section>
}

export function EvidenceDrawer({ detail, loading, onClose, onFact }: { detail: SourceDetail | null; loading: boolean; onClose: () => void; onFact: (id: string) => void }) {
  const panel = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  const open = loading || Boolean(detail)
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    panel.current?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close.current(); return }
      if (event.key !== 'Tab') return
      const items = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input, select, textarea, summary, [tabindex="0"]') || []).filter(item => !item.closest('details:not([open])') || item.tagName === 'SUMMARY')
      const first = items[0], last = items.at(-1)
      if (!first) { event.preventDefault(); return }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); previous?.focus() }
  }, [open])
  if (!open) return null
  return <div className={`drawer-scrim ${loading || detail ? 'open' : ''}`} onMouseDown={event => { if (event.currentTarget === event.target) onClose() }}><aside ref={panel} tabIndex={-1} role="dialog" aria-modal="true" className="evidence-drawer" aria-label="Evidence inspector"><header><div><span>Evidence inspector</span><strong>{detail?.artifact.id || 'Loading source'}</strong></div><button aria-label="Close evidence inspector" onClick={onClose}>×</button></header>{loading ? <div className="drawer-loading">Loading provenance…</div> : detail ? <div className="drawer-body">{detail.focusedFact && <section><h2>Selected claim</h2><p>{detail.focusedFact}</p><small>Source-reported evidence, not a guarantee of source accuracy.</small></section>}<dl><dt>Canonical URL</dt><dd><a href={detail.artifact.url} target="_blank" rel="noreferrer">{detail.artifact.url}</a></dd><dt>Publisher</dt><dd>{detail.artifact.publisher_owner}</dd><dt>Retrieval</dt><dd>{detail.artifact.retrieval_method.replaceAll('_', ' ')}</dd><dt>Captured</dt><dd>{formatDate(detail.artifact.captured_at)}</dd><dt>SHA256</dt><dd><code>{detail.artifact.sha256}</code></dd><dt>Raw payload</dt><dd>{detail.artifact.content_type} · {formatBytes(detail.artifact.raw_size)}</dd></dl><section><div className="drawer-section-title"><span>Extracted observations</span><Badge tone="verified">{detail.observations.length}</Badge></div>{detail.observations.length ? detail.observations.map(item => <article key={item.id}><span>{item.metric}</span><strong>{String(item.value)} {item.unit || ''}</strong><code>{item.pointer}</code><small>{item.extraction_method.replaceAll('_', ' ')}</small></article>) : <EmptyState title="No trusted observations">Discovery-only artifacts cannot create observations.</EmptyState>}</section><section><div className="drawer-section-title"><span>Compiled facts</span><Badge tone="verified">{detail.facts.length}</Badge></div>{detail.facts.map(fact => <button className="drawer-fact" key={fact.id} onClick={() => onFact(fact.id)}><Badge tone="verified">Verified fact</Badge><span>{fact.text}</span></button>)}</section></div> : null}</aside></div>
}

export default function App() {
  const [route, setRoute] = useState(readRoute)
  const page = route.page
  const selectedIdea = route.candidate
  const setSelectedIdea = (id: string) => { window.location.hash = routeHash('ideas', { candidate: id }) }
  useEffect(() => { const update = () => setRoute(readRoute()); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update) }, [])
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [timeline, setTimeline] = useState<TimelinePoint[]>([])
  const [sources, setSources] = useState<Source[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [calibration, setCalibration] = useState<Calibration | null>(null)
  const [matchingStatus, setMatchingStatus] = useState<MatchingStatus | null>(null)
  const [matchingReviews, setMatchingReviews] = useState<MatchingReview[]>([])
  const [selectedMatch, setSelectedMatch] = useState('')
  const [sourceDetail, setSourceDetail] = useState<SourceDetail | null>(null)
  const [sourceLoading, setSourceLoading] = useState(false)
  const sourceGeneration = useRef(0)
  const [busy, setBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => { document.documentElement.setAttribute('data-theme', 'light'); document.documentElement.style.removeProperty('--app-zoom') }, [])
  const refreshGeneration = useRef(0)
  const [updatedAt, setUpdatedAt] = useState('')
  const [refreshErrors, setRefreshErrors] = useState<string[]>([])
  const candidates = useMemo(() => runs.flatMap(run => run.candidates), [runs])
  const metaRunning = runs.some(run => ['queued', 'running'].includes(run.status))
  const scoutReady = candidates.length > 0

  const loadMatching = useCallback(async () => {
    const [status, reviews] = await Promise.all([
      api<MatchingStatus>('/api/matching/status'), api<MatchingReview[]>('/api/matching/reviews?limit=50&include_resolved=true'),
    ])
    setMatchingStatus(status); setMatchingReviews(reviews)
    setSelectedMatch(current => reviews.some(item => item.association_id === current) ? current : reviews[0]?.association_id || '')
  }, [])

  // Each panel renders as its own response lands. Awaiting all six together
  // meant the slowest call held every counter at an em dash, which reads as
  // lost data rather than a slow request -- and the runs list is by far the
  // heaviest, since it carries every candidate and its facts.
  const loadAll = useCallback(async () => {
    const generation = ++refreshGeneration.current
    setRefreshing(true)
    const fill = async <T,>(url: string, apply: (value: T) => void) => {
      try { const value = await api<T>(url); if (generation === refreshGeneration.current) apply(value) }
      catch (e) { throw new Error(url.split('?')[0] + ': ' + (e as Error).message) }
    }
    const results = await Promise.allSettled([
      fill<DashboardSummary>('/api/dashboard/summary', setSummary),
      fill<{ points: TimelinePoint[] }>('/api/dashboard/timeline', value => setTimeline(value.points)),
      fill<Source[]>('/api/sources?limit=100', setSources),
      fill<Run[]>('/api/research-runs?limit=25', setRuns),
      fill<Calibration>('/api/calibration/status', setCalibration),
      fill<Health>('/api/health', setHealth),
      loadMatching(),
    ])
    if (generation !== refreshGeneration.current) return
    const failures = results.flatMap(r => r.status === 'rejected' ? [(r.reason as Error).message] : [])
    setRefreshErrors(failures)
    if (!failures.length) setUpdatedAt(new Date().toISOString())
    setRefreshing(false)
  }, [loadMatching])

  useEffect(() => { void loadAll() }, [loadAll])

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => { await loadAll(); if (!stopped) timer = setTimeout(tick, 5000) }
    timer = setTimeout(tick, 5000)
    return () => { stopped = true; clearTimeout(timer) }
  }, [loadAll])

  async function startResearch(niche: string) {
    setBusy(true); setError(''); setNotice('')
    try {
      const run = await api<Run>('/api/research-runs', { method: 'POST', body: JSON.stringify({ niche, mode: "deep" }) })
      setRuns(current => [run, ...current.filter(item => item.id !== run.id)])
      setNotice(`Meta Hunter queued research for “${run.niche}”.`)
    } catch (caught) { setError((caught as Error).message) } finally { setBusy(false) }
  }

  async function openSourceById(artifactId: string) {
    const generation = ++sourceGeneration.current
    setSourceLoading(true); setSourceDetail(null); setError('')
    try { const detail = await api<SourceDetail>(`/api/sources/${artifactId}`); if (generation === sourceGeneration.current) setSourceDetail(detail) }
    catch (caught) { if (generation === sourceGeneration.current) setError((caught as Error).message) }
    finally { if (generation === sourceGeneration.current) setSourceLoading(false) }
  }
  async function openSource(source: Source) { await openSourceById(source.id) }
  async function inspectFact(id: string) {
    const generation = ++sourceGeneration.current
    setSourceLoading(true); setSourceDetail(null); setError('')
    try {
      const evidence = await api<{ fact: SourceDetail['facts'][number]; observations: Array<{ id: string }>; artifacts: Array<{ id: string }> }>(`/api/evidence/${id}`)
      if (!evidence.artifacts.length) throw new Error('This fact has no linked source artifact.')
      const records = await Promise.all(evidence.artifacts.map(a => api<SourceDetail>(`/api/sources/${a.id}`)))
      const selected = new Set(evidence.observations.map(o => o.id))
      if (generation === sourceGeneration.current) setSourceDetail({ ...records[0], focusedFact: evidence.fact.text,
        observations: records.flatMap(r => r.observations).filter(o => selected.has(o.id)), facts: [evidence.fact] })
    } catch (caught) { if (generation === sourceGeneration.current) setError((caught as Error).message) }
    finally { if (generation === sourceGeneration.current) setSourceLoading(false) }
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
    window.location.hash = routeHash(nextPage)
    setError(''); setNotice('')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return <div className="app-shell">
    <AppSidebar page={page} onNavigate={navigate} health={health} metaRunning={metaRunning} scoutReady={scoutReady} />
    <div className="app-content"><TopBar page={page} calibration={calibration} refreshing={refreshing} onRefresh={loadAll} />
      <main id="primary-workspace"><p className="data-freshness" role="status">{refreshing ? 'Refreshing data…' : updatedAt ? 'Last complete refresh: ' + formatDate(updatedAt) : 'Waiting for data'}{refreshErrors.length > 0 ? ' · Some panels are stale or unavailable.' : ''}</p>{refreshErrors.length > 0 && <div className="warning-box">{refreshErrors.map(message => <p key={message}>{message}</p>)}</div>}
        {error && <div role="alert" className="global-alert"><strong>Request failed safely</strong><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}>×</button></div>}
        {notice && <div className="global-notice"><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice('')}>×</button></div>}
        {page === 'home' && <CommandCenter summary={summary} timeline={timeline} sources={sources} runs={runs} health={health} calibration={calibration} onSource={openSource} onNavigate={navigate} onCandidate={setSelectedIdea} />}
        {page === 'ideas' && <IdeasPanel runs={runs} selectedId={selectedIdea} onSelect={setSelectedIdea} onInspectFact={inspectFact} />}
        {page === 'sources' && <SourcesPage sources={sources} onSource={openSource} />}
        {page === 'history' && <AgentHistoryPage onInspectFact={inspectFact} onOpenCandidate={id => setSelectedIdea(id)} />}
        {page === 'matching' && <MatchingEnginePage status={matchingStatus} reviews={matchingReviews} selectedId={selectedMatch} onSelect={setSelectedMatch} onReload={loadMatching} onReview={reviewMatch} busy={busy} />}
        {page === 'market' && <MarketPulsePage />}
        {page === 'meta' && <MetaHunterPage runs={runs} health={health} onStart={startResearch} busy={busy} />}
        {page === 'scout' && <VentureScoutPage candidates={candidates} />}
        {page === 'calibration' && <CalibrationPage calibration={calibration} summary={summary} />}
        {page === 'health' && <HealthPage health={health} summary={summary} matching={matchingStatus} />}
      </main>
    </div>
    <EvidenceDrawer detail={sourceDetail} loading={sourceLoading} onClose={() => { sourceGeneration.current++; setSourceDetail(null); setSourceLoading(false) }} onFact={inspectFact} />
  </div>
}
