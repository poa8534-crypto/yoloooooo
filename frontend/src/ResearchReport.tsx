import { useEffect, useState } from 'react'

type Question = { id: string; question: string; state: string; limitation: string; fact_ids: string[] }
type History = { status: string; trend: null | { label: string; value: number }; points: Array<{ day: string; measurements: null | Array<{ metric: string; entity: string; value: number; observation_id: string }> }> }
type Design = { concept_title: string; core_loop: string; differentiator: string; build_steps: string[]; risks: string[]; questions: string[]; supporting_fact_ids?: string[]; essential_features?: string[]; excluded_features?: string[]; dependencies?: string[]; validation_tasks?: string[]; counterevidence?: string[]; design_assumptions?: Array<{ kind: string; value: number; unit: string; basis: string }> }
type Report = {
  report_id: string; selection_rule: string; questions: Question[]; quota_note: string
  progress: { stop_reason: string; elapsed_seconds: number; errors?: Array<{ stage: string; error: string }>;
    budget_stops?: Array<{ stage: string; error: string }> }; api_usage: Record<string, number>
  abstentions?: Array<{ stage: string; reason: string }>
  comparison: Array<{ candidate_id: string; universe_id: string; niche_relevance: string; omitted_facts?: number; facts: Array<{ id: string; text: string; freshness: string }>; history: History }>
  concepts: Array<{ id: string; candidate_id: string; payload: Design }>
  audits: Array<{ audit_id: string; candidate_id: string; proposal_id: string; evidence_state: string; proposal: Design | null; risks: string[] }>
}

export type CitedFact = { id: string; text: string }

/** A citation reads as the claim it points at, not as its row id.
 *
 * These were rendered as "Inspect cited fact <uuid>" linking to raw JSON, so a
 * reader saw seven identical-looking hex strings and learned nothing from any
 * of them. `facts` supplies the text; `onInspect` opens the evidence drawer in
 * place instead of throwing the reader into a new tab.
 */
export function DesignDetails({ design, facts, onInspect }: { design: Design; facts?: CitedFact[]; onInspect?: (id: string) => void }) {
  const textFor = (id: string) => facts?.find(fact => fact.id === id)?.text || ''

  return <div className="classified proposal"><span className="evidence-badge proposal">Speculative design · human validation required</span>
    {!!design.supporting_fact_ids?.length && <details className="brief-fold"><summary>Cited evidence<span>{design.supporting_fact_ids.length}</span></summary><p>Citations identify inputs; they do not verify the model's interpretation.</p><ul className="cited-list">{design.supporting_fact_ids.map(id => <li key={id}>{onInspect
      ? <button type="button" className="text-button" onClick={() => onInspect(id)}>{textFor(id) || `Fact ${id.slice(0, 8)}`}</button>
      : <a href={`/api/evidence/${id}`} target="_blank" rel="noreferrer">{textFor(id) || `Fact ${id.slice(0, 8)}`}</a>}</li>)}</ul></details>}
    {([['Essential features', design.essential_features], ['Excluded features', design.excluded_features], ['Dependencies', design.dependencies], ['Validation tasks', design.validation_tasks], ['Counterevidence / challenges to investigate', design.counterevidence], ['Unanswered questions', design.questions]] as const).map(([label, values]) => values?.length ? <details className="brief-fold" key={label} open={values.length <= 4}><summary>{label}<span>{values.length}</span></summary><ul>{values.map((value, i) => <li key={i}>{value}</li>)}</ul></details> : null)}
    {!!design.design_assumptions?.length && <details className="brief-fold"><summary>Unverified design quantities<span>{design.design_assumptions.length}</span></summary><ul>{design.design_assumptions.map((a, i) => <li key={i}>{a.kind.replaceAll('_', ' ')}: {a.value} {a.unit} — {a.basis.replaceAll('_', ' ')}</li>)}</ul></details>}
  </div>
}

function HistoryTable({ data }: { data: History }) {
  return <div><p>{data.status.replaceAll('_', ' ')}. Daily captures are not interpolated.</p>
    {data.trend && <p>Preliminary seven-day CCU difference: {data.trend.value}. Not a growth forecast.</p>}
    {data.points.length > 0 && <div className="table-scroll"><table><thead><tr><th>UTC day</th><th>Captured measurement</th><th>Value</th><th>Observation ID</th></tr></thead><tbody>{data.points.map(point => point.measurements ? point.measurements.map(m => <tr key={`${point.day}-${m.metric}-${m.entity}`}><td>{point.day}</td><td>{m.metric} · {m.entity}</td><td>{m.value.toLocaleString()}</td><td><code>{m.observation_id}</code></td></tr>) : <tr key={point.day}><td>{point.day}</td><td colSpan={3}>Missing — no interpolation</td></tr>)}</tbody></table></div>}
  </div>
}

export function CandidateHistory({ candidateId }: { candidateId: string }) {
  const [data, setData] = useState<History | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setData(null); setError('')
    fetch(`/api/candidates/${candidateId}/history`, { signal: controller.signal }).then(async r => {
      if (!r.ok) throw new Error('History unavailable')
      return r.json()
    }).then(setData).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [candidateId])
  return data ? <HistoryTable data={data} /> : <p>{error || 'Loading captured history…'}</p>
}

export function ResearchReport({ runId, status }: { runId: string; status: string }) {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setReport(null); setError('')
    if (['queued', 'running'].includes(status)) return () => controller.abort()
    fetch(`/api/research-runs/${runId}/report`, { signal: controller.signal }).then(async r => {
      if (!r.ok) throw new Error(r.status === 404 ? 'No finalized deep report for this run. Legacy quick runs contain candidate evidence only.' : 'Report could not be loaded')
      return r.json()
    }).then(setReport).catch(e => { if (!controller.signal.aborted) setError(e.message) })
    return () => controller.abort()
  }, [runId, status])
  if (!report) return <article className="data-panel"><h2>Research report</h2><p>{error || 'Report will appear when the bounded investigation finishes.'}</p></article>
  return <article className="data-panel"><div className="panel-heading"><div><span>Persisted investigation</span><h2>Evidence-backed research report</h2></div><span className="evidence-badge insufficient">Research only</span></div>
    <p>{report.selection_rule}</p><p>Elapsed: {Math.round(report.progress.elapsed_seconds)} seconds · Stop: {report.progress.stop_reason}</p>
    <details><summary>API and model attempt usage</summary><ul>{Object.entries(report.api_usage).map(([key, value]) => <li key={key}>{key}: {value}</li>)}</ul><p>{report.quota_note}</p></details>
    {!!report.progress.errors?.length && <div className="warning-box"><strong>Incomplete steps</strong>{report.progress.errors.map((e, i) => <p key={i}>{e.stage}: {e.error}</p>)}</div>}
    {/* A configured cap stopping further work is the budget holding, not a failure. */}
    {!!report.progress.budget_stops?.length && <div className="warning-box"><strong>Budget caps reached</strong>{report.progress.budget_stops.map((e, i) => <p key={i}>{e.stage}: {e.error}</p>)}<small>Work stopped at a configured limit; nothing failed.</small></div>}
    {!!report.abstentions?.length && <details><summary>Deliberate abstentions</summary>{report.abstentions.map((a, i) => <p key={i}>{a.stage}: {a.reason}</p>)}</details>}
    <h3>Research questions and limitations</h3><div className="fact-stack">{report.questions.map(q => <div key={q.id}><strong>{q.question}</strong><p>{q.state} — {q.limitation}</p><small>{q.fact_ids.length} supporting fact IDs</small></div>)}</div>
    <h3>Candidate comparison</h3>{report.comparison.map(d => <details key={d.candidate_id}><summary>Universe {d.universe_id} · {d.facts.length} admissible facts · relevance requires review</summary>
      {!!d.omitted_facts && <p>Some saved facts are no longer admissible and were omitted.</p>}
      <ul>{d.facts.map(f => <li key={f.id}>{f.text} <small>({f.freshness})</small> <a href={`/api/evidence/${f.id}`} target="_blank" rel="noreferrer">Inspect provenance</a></li>)}</ul><HistoryTable data={d.history} /></details>)}
    <h3>Research concepts and bound audits</h3>{report.concepts.length ? report.concepts.map(c => {
      const audit = report.audits.find(a => a.proposal_id === c.id && a.candidate_id === c.candidate_id)
      return <details key={c.id}><summary>{c.payload.concept_title} · speculative research concept</summary><p>{c.payload.core_loop}</p><p>{c.payload.differentiator}</p><DesignDetails design={c.payload} />
        {audit ? <div><h3>Venture Scout — {audit.evidence_state.replaceAll('_', ' ')}</h3><a href={`/api/audits/${audit.audit_id}`} target="_blank" rel="noreferrer">Persistent audit record</a>{audit.proposal && <><p>{audit.proposal.core_loop}</p><ol>{audit.proposal.build_steps.map((s, i) => <li key={i}>{s}</li>)}</ol><DesignDetails design={audit.proposal} /></>}<ul>{audit.risks.map((risk, i) => <li key={i}>{risk}</li>)}</ul></div> : <p>Audit not produced within evidence/model budget.</p>}
      </details>
    }) : <p>No valid concepts produced. This is an allowed abstention; inspect limitations and errors.</p>}
  </article>
}
