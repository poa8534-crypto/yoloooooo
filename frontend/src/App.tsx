import { FormEvent, useEffect, useMemo, useState } from 'react'

type Fact = { id: string; text: string; source_ids: string[]; freshness: string; verification_state: string }
type Proposal = {
  concept_title: string; core_loop: string; differentiator: string; build_steps: string[];
  risks: string[]; questions: string[]; supporting_fact_ids: string[]
}
type Candidate = {
  id: string; external_id: string; display_name: string; facts: Fact[]; proposal: Proposal | null;
  decision: string; decision_id: string | null; score: number | null; confidence: number | null
}
type Run = {
  id: string; niche: string; status: string; message: string; created_at: string; completed_at: string | null;
  candidates: Candidate[]; passing_results: Candidate[]
}
type Calibration = {
  phase: string; complete_clusters: number; required_clusters: number; scoring_active: boolean;
  model_version: string | null; heldout_precision: number | null; heldout_recommendations: number | null; reason: string
}
type Health = {
  status: string; database: string;
  ollama: { available: boolean; primary_present: boolean; fallback_present: boolean };
  connectors: { tavily_configured: boolean; youtube_configured: boolean };
  scheduler: { timezone: string; daily_at: string; last_snapshot: unknown };
  calibration: Calibration
}

const tabs = [
  ['research', '⌁', 'Research'], ['audit', '◇', 'Candidate Audit'], ['evidence', '◎', 'Evidence Explorer'],
  ['calibration', '↗', 'Collection / Calibration'], ['overrides', '✎', 'Overrides'], ['health', '●', 'System Health'],
] as const

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options })
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Request failed: ${response.status}`)
  return response.json()
}

function Badge({ children, tone = 'plain' }: { children: React.ReactNode; tone?: 'plain' | 'good' | 'warn' | 'bad' }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`status-dot ${ok ? 'on' : 'off'}`} aria-label={ok ? 'available' : 'unavailable'} />
}

function FactCard({ fact, inspect }: { fact: Fact; inspect: (id: string) => void }) {
  return <button className="fact-card" onClick={() => inspect(fact.id)}>
    <span className="eyebrow">Trusted fact · {fact.verification_state}</span>
    <strong>{fact.text}</strong>
    <span className="fact-meta">{fact.source_ids.length} source artifact{fact.source_ids.length === 1 ? '' : 's'} · {fact.freshness}</span>
  </button>
}

function ProposalCard({ proposal, title = 'Model proposal' }: { proposal: Proposal; title?: string }) {
  return <article className="proposal-card">
    <span className="eyebrow purple">{title} · Not a factual claim</span>
    <h3>{proposal.concept_title}</h3>
    <p>{proposal.core_loop}</p>
    <p><b>Differentiator:</b> {proposal.differentiator}</p>
    <div className="columns">
      <div><h4>Build steps</h4><ul>{proposal.build_steps.map(item => <li key={item}>{item}</li>)}</ul></div>
      <div><h4>Risks</h4><ul>{proposal.risks.map(item => <li key={item}>{item}</li>)}</ul></div>
    </div>
  </article>
}

export default function App() {
  const [tab, setTab] = useState('research')
  const [niche, setNiche] = useState('')
  const [run, setRun] = useState<Run | null>(null)
  const [selectedCandidate, setSelectedCandidate] = useState<string>('')
  const [selectedFact, setSelectedFact] = useState<string>('')
  const [evidence, setEvidence] = useState<any>(null)
  const [audit, setAudit] = useState<any>(null)
  const [calibration, setCalibration] = useState<Calibration | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [overrideKind, setOverrideKind] = useState('research_more')
  const [overrideReason, setOverrideReason] = useState('')
  const [overrideResult, setOverrideResult] = useState<any>(null)

  const candidate = useMemo(() => run?.candidates.find(item => item.id === selectedCandidate) || run?.candidates[0], [run, selectedCandidate])

  useEffect(() => {
    const saved = localStorage.getItem('venture-last-run')
    if (saved) api<Run>(`/api/research-runs/${saved}`).then(setRun).catch(() => localStorage.removeItem('venture-last-run'))
    refreshStatus()
  }, [])

  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return
    const timer = window.setInterval(() => api<Run>(`/api/research-runs/${run.id}`).then(setRun).catch(e => setError(e.message)), 1200)
    return () => window.clearInterval(timer)
  }, [run?.id, run?.status])

  async function refreshStatus() {
    const [c, h] = await Promise.all([api<Calibration>('/api/calibration/status'), api<Health>('/api/health')])
    setCalibration(c); setHealth(h)
  }

  async function startResearch(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setAudit(null); setEvidence(null)
    try {
      const next = await api<Run>('/api/research-runs', { method: 'POST', body: JSON.stringify({ niche }) })
      setRun(next); localStorage.setItem('venture-last-run', next.id)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function inspectFact(id: string) {
    setSelectedFact(id); setTab('evidence'); setError('')
    try { setEvidence(await api(`/api/evidence/${id}`)) } catch (e) { setError((e as Error).message) }
  }

  async function runAudit() {
    if (!candidate) return
    setBusy(true); setError('')
    try { setAudit(await api(`/api/candidates/${candidate.id}/audit`, { method: 'POST' })) }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function submitOverride(event: FormEvent) {
    event.preventDefault(); if (!candidate?.decision_id) return
    setBusy(true); setError('')
    try {
      setOverrideResult(await api(`/api/decisions/${candidate.decision_id}/override`, {
        method: 'POST', body: JSON.stringify({ requested_kind: overrideKind, reason: overrideReason }),
      }))
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return <div className="shell">
    <aside>
      <div className="brand"><div className="brand-mark">V</div><div><strong>Venture Agents</strong><span>Evidence-first Roblox research</span></div></div>
      <nav>{tabs.map(([id, icon, label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}><span>{icon}</span>{label}</button>)}</nav>
      <div className="trust-card"><span className="shield">◆</span><div><b>Evidence firewall active</b><small>Facts and decisions are compiled from stored evidence.</small></div></div>
      <div className="local-only"><StatusDot ok /> Bound to this PC only</div>
    </aside>
    <main>
      <header><div><span className="eyebrow">Local research system</span><h1>{tabs.find(item => item[0] === tab)?.[2]}</h1></div><div className="header-badges"><Badge tone={calibration?.scoring_active ? 'good' : 'warn'}>{calibration?.scoring_active ? 'Scoring active' : 'Collection phase'}</Badge><Badge>Zero unsupported facts</Badge></div></header>
      {error && <div className="alert bad">{error}</div>}

      {tab === 'research' && <section>
        <div className="hero-grid">
          <div className="panel hero-panel"><span className="eyebrow green">Agent one · Meta Hunter</span><h2>Find signals worth investigating.</h2><p>Discovery creates leads. Only captured primary or corroborated evidence can create trusted facts. Until calibration passes, results remain research-only.</p>
            <form className="search-form" onSubmit={startResearch}><input value={niche} onChange={e => setNiche(e.target.value)} minLength={3} placeholder="e.g. cooperative cozy farming" required /><button disabled={busy}>{busy ? 'Starting…' : 'Run research'}</button></form>
          </div>
          <div className="panel metric-panel"><span className="eyebrow">Calibration readiness</span><div className="big-number">{calibration?.complete_clusters ?? 0}<small> / {calibration?.required_clusters ?? 200}</small></div><div className="progress"><i style={{ width: `${Math.min(100, 100 * (calibration?.complete_clusters ?? 0) / (calibration?.required_clusters ?? 200))}%` }} /></div><p>{calibration?.reason}</p></div>
        </div>
        {run && <div className="panel run-panel"><div className="panel-title"><div><span className="eyebrow">Latest run · {run.status}</span><h2>{run.niche}</h2></div><Badge tone={run.status === 'failed' ? 'bad' : run.status === 'complete' ? 'good' : 'warn'}>{run.status}</Badge></div><p>{run.message}</p>
          <div className="result-summary"><b>{run.passing_results.length} passing opportunities</b><span>{run.candidates.length} researched candidates</span></div>
          {run.passing_results.length === 0 && <div className="empty-state"><span>∅</span><div><b>No candidate is being recommended.</b><p>This is expected while scoring is locked or evidence is incomplete. The system will not fill empty slots with guesses.</p></div></div>}
          <div className="candidate-grid">{run.candidates.map(item => <article className="candidate-card" key={item.id}><div className="candidate-head"><Badge tone={item.decision === 'recommend' ? 'good' : 'warn'}>{item.decision.replaceAll('_', ' ')}</Badge><span>ID {item.external_id}</span></div><h3>{item.display_name}</h3><p>{item.facts.length} source-backed facts</p>{item.score === null ? <div className="locked-score">Score locked</div> : <div className="score">{item.score.toFixed(1)}</div>}<button className="secondary" onClick={() => { setSelectedCandidate(item.id); setTab('audit') }}>Open audit</button></article>)}</div>
        </div>}
      </section>}

      {tab === 'audit' && <section><div className="panel"><span className="eyebrow purple">Agent two · Venture Scout</span><h2>Audit a source-backed candidate</h2><p>The audit may propose an MVP and risks. It cannot change the deterministic engine verdict.</p>
        {run?.candidates.length ? <><label>Candidate<select value={candidate?.id || ''} onChange={e => setSelectedCandidate(e.target.value)}>{run.candidates.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label><button onClick={runAudit} disabled={busy}>{busy ? 'Auditing…' : 'Run Venture Scout audit'}</button></> : <div className="empty-state"><span>◇</span><div><b>No candidate loaded</b><p>Run Meta Hunter research first.</p></div></div>}
      </div>{candidate && <div className="panel"><div className="panel-title"><h2>{candidate.display_name}</h2><Badge tone={candidate.decision === 'recommend' ? 'good' : 'warn'}>{candidate.decision}</Badge></div><div className="facts-list">{candidate.facts.map(fact => <FactCard key={fact.id} fact={fact} inspect={inspectFact} />)}</div></div>}{audit?.proposal && <ProposalCard proposal={audit.proposal} title="Venture Scout proposal" />}</section>}

      {tab === 'evidence' && <section><div className="panel"><span className="eyebrow green">Immutable provenance</span><h2>Trace a fact to captured bytes</h2><form className="inline-form" onSubmit={e => { e.preventDefault(); inspectFact(selectedFact) }}><input value={selectedFact} onChange={e => setSelectedFact(e.target.value)} placeholder="Fact ID" required /><button>Inspect</button></form></div>
        {evidence && <div className="evidence-grid"><article className="panel"><span className="eyebrow">Rendered fact</span><h3>{evidence.fact.text}</h3><p>Template: <code>{evidence.fact.template_id}</code></p><Badge tone="good">{evidence.fact.verification_state}</Badge></article><article className="panel"><span className="eyebrow">Observation</span>{evidence.observations.map((o: any) => <dl key={o.id}><dt>Metric</dt><dd>{o.metric}</dd><dt>Stored value</dt><dd>{String(o.value)}</dd><dt>Extraction</dt><dd>{o.extraction_method}</dd><dt>Pointer</dt><dd><code>{o.pointer}</code></dd></dl>)}</article><article className="panel span-two"><span className="eyebrow">Source artifacts</span>{evidence.artifacts.map((a: any) => <div className="source-row" key={a.id}><div><b>{a.publisher_owner}</b><a href={a.url} target="_blank" rel="noreferrer">{a.url}</a></div><div><Badge>{a.source_tier}</Badge><code>{a.sha256}</code></div></div>)}</article></div>}
      </section>}

      {tab === 'calibration' && <section><div className="hero-grid"><div className="panel"><span className="eyebrow">Current phase</span><h2>{calibration?.phase.replaceAll('_', ' ')}</h2><p>{calibration?.reason}</p><Badge tone={calibration?.scoring_active ? 'good' : 'warn'}>{calibration?.scoring_active ? 'Automatic recommendations enabled' : 'No scores or recommendations'}</Badge></div><div className="panel metric-panel"><span className="eyebrow">Complete market windows</span><div className="big-number">{calibration?.complete_clusters ?? 0}<small> / {calibration?.required_clusters ?? 200}</small></div><div className="progress"><i style={{ width: `${Math.min(100, 100 * (calibration?.complete_clusters ?? 0) / (calibration?.required_clusters ?? 200))}%` }} /></div></div></div><div className="panel"><h3>Activation gates</h3><div className="gate-list"><div><StatusDot ok={Boolean(calibration && calibration.complete_clusters >= calibration.required_clusters)} /> Complete tracked clusters</div><div><StatusDot ok={Boolean(calibration?.heldout_precision && calibration.heldout_precision >= .95)} /> Held-out precision floor</div><div><StatusDot ok={Boolean(calibration?.heldout_recommendations && calibration.heldout_recommendations >= 10)} /> Minimum held-out recommendations</div><div><StatusDot ok={Boolean(calibration?.scoring_active)} /> Frozen artifact activated</div></div></div></section>}

      {tab === 'overrides' && <section><div className="panel"><span className="eyebrow">Human authority with audit trail</span><h2>Annotate an engine decision</h2><p>An override creates a new immutable record. It never erases or edits the original verdict.</p>{candidate?.decision_id ? <form className="override-form" onSubmit={submitOverride}><label>Decision<select value={candidate.id} onChange={e => setSelectedCandidate(e.target.value)}>{run?.candidates.filter(c => c.decision_id).map(item => <option value={item.id} key={item.id}>{item.display_name} — {item.decision}</option>)}</select></label><label>Requested action<select value={overrideKind} onChange={e => setOverrideKind(e.target.value)}><option value="research_more">Research more</option><option value="recommend">Recommend manually</option><option value="blocked_conflict">Block for conflict</option></select></label><label>Reason<textarea value={overrideReason} onChange={e => setOverrideReason(e.target.value)} minLength={10} required placeholder="Explain why you are overriding the engine record…" /></label><button disabled={busy}>Record override</button></form> : <div className="empty-state"><span>✎</span><div><b>No decision available</b><p>Load a completed research run first.</p></div></div>}{overrideResult && <div className="alert good">Override recorded. Original engine verdict: {overrideResult.engine_verdict}.</div>}</div></section>}

      {tab === 'health' && <section><div className="panel"><div className="panel-title"><div><span className="eyebrow">Local runtime</span><h2>System checks</h2></div><button className="secondary" onClick={refreshStatus}>Refresh</button></div><div className="health-grid"><div><StatusDot ok={health?.database === 'connected'} /><b>SQLite ledger</b><span>{health?.database}</span></div><div><StatusDot ok={Boolean(health?.ollama.available)} /><b>Ollama</b><span>{health?.ollama.available ? 'reachable' : 'offline'}</span></div><div><StatusDot ok={Boolean(health?.ollama.primary_present)} /><b>Qwen primary</b><span>{health?.ollama.primary_present ? 'installed' : 'qwen3:14b not installed'}</span></div><div><StatusDot ok={Boolean(health?.ollama.fallback_present)} /><b>Qwen fallback</b><span>{health?.ollama.fallback_present ? 'installed' : 'missing'}</span></div><div><StatusDot ok={Boolean(health?.connectors.tavily_configured)} /><b>Tavily</b><span>{health?.connectors.tavily_configured ? 'configured' : 'API key required'}</span></div><div><StatusDot ok={Boolean(health?.connectors.youtube_configured)} /><b>YouTube</b><span>{health?.connectors.youtube_configured ? 'configured' : 'API key required'}</span></div></div><div className="scheduler-note"><b>Daily snapshot</b><span>{health?.scheduler.daily_at} {health?.scheduler.timezone}</span></div></div></section>}
    </main>
  </div>
}

