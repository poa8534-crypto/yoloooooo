import { useEffect, useState } from 'react'

type Question = { id: string; question: string; state: string; limitation: string; fact_ids: string[] }
type History = { status: string; trend: null | { label: string; value: number }; points: Array<{ day: string; measurements: null | Array<{ metric: string; entity: string; value: number; observation_id: string }> }> }
type Design = { concept_title: string; core_loop: string; differentiator: string; build_steps: string[]; risks: string[]; questions: string[]; supporting_fact_ids?: string[]; essential_features?: string[]; excluded_features?: string[]; dependencies?: string[]; validation_tasks?: string[]; counterevidence?: string[]; design_assumptions?: Array<{ kind: string; value: number; unit: string; basis: string }> }
type Report = {
  report_id: string; selection_rule: string; questions: Question[]; quota_note: string
  progress: { stop_reason: string; elapsed_seconds: number; errors?: Array<{ stage: string; error: string }>;
    budget_stops?: Array<{ stage: string; error: string }>; search_queries?: string[] }
  api_usage: Record<string, number>
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
      : <span>{textFor(id) || `Fact ${id.slice(0, 8)}`}</span>}</li>)}</ul></details>}
    {([['Essential features', design.essential_features], ['Excluded features', design.excluded_features], ['Dependencies', design.dependencies], ['Validation tasks', design.validation_tasks], ['Counterevidence / challenges to investigate', design.counterevidence], ['Unanswered questions', design.questions]] as const).map(([label, values]) => values?.length ? <details className="brief-fold" key={label} open={values.length <= 4}><summary>{label}<span>{values.length}</span></summary><ul>{values.map((value, i) => <li key={i}>{value}</li>)}</ul></details> : null)}
    {!!design.design_assumptions?.length && <details className="brief-fold"><summary>Unverified design quantities<span>{design.design_assumptions.length}</span></summary><ul>{design.design_assumptions.map((a, i) => <li key={i}>{a.kind.replaceAll('_', ' ')}: {a.value} {a.unit} — {a.basis.replaceAll('_', ' ')}</li>)}</ul></details>}
  </div>
}

// Why the run stopped, in words. The identifier alone tells a reader nothing
// about whether to trust the result: "round_limit" and "all_questions_answered"
// look equally final and mean opposite things. The identifier stays visible
// next to the sentence so a report can still be matched against the code.
const STOP_REASONS: Record<string, string> = {
  all_questions_answered: 'every research question was answered.',
  candidate_inspection_limit: 'the cap on how many games one run may inspect was reached, so discovery stopped looking for more.',
  no_new_admissible_evidence_two_rounds: 'two rounds running added no admissible evidence, so the run stopped rather than repeat itself.',
  round_limit: 'the configured number of rounds ran out with questions still open.',
  finalization_reserve: 'time ran short and the rest of the budget was held back to write this report.',
  service_shutdown: 'the service stopped mid-run. This run was interrupted, not finished.',
  operator_cancelled: 'you stopped this run. It was not finished, and everything it had already collected is kept.',
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

export function ResearchReport({ runId, status, onInspectFact }: {
  runId: string; status: string; onInspectFact?: (id: string) => void
}) {
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
    <p>{report.selection_rule}</p>
    <p>Elapsed: {Math.round(report.progress.elapsed_seconds)} seconds · Stop: {STOP_REASONS[report.progress.stop_reason] ?? report.progress.stop_reason}
      {!!STOP_REASONS[report.progress.stop_reason] && <small><code>{report.progress.stop_reason}</code></small>}</p>
    <details><summary>API and model attempt usage</summary><ul>{Object.entries(report.api_usage).map(([key, value]) => <li key={key}>{key}: {value}</li>)}</ul><p>{report.quota_note}</p></details>
    {!!report.progress.errors?.length && <div className="warning-box"><strong>Incomplete steps</strong>{report.progress.errors.map((e, i) => <p key={i}>{e.stage}: {e.error}</p>)}</div>}
    {/* A configured cap stopping further work is the budget holding, not a failure. */}
    {!!report.progress.budget_stops?.length && <div className="warning-box"><strong>Budget caps reached</strong>{report.progress.budget_stops.map((e, i) => <p key={i}>{e.stage}: {e.error}</p>)}<small>Work stopped at a configured limit; nothing failed.</small></div>}
    {!!report.progress.search_queries?.length && <details className="brief-fold"><summary>What this run searched for<span>{report.progress.search_queries.length}</span></summary>
      <p>Planned once at the start from the niche, then reused every round. A search is not evidence; it only nominates games to capture.</p>
      <ul>{report.progress.search_queries.map(query => <li key={query}><code>{query}</code></li>)}</ul></details>}
    {!!report.abstentions?.length && <details className="brief-fold"><summary>Deliberate abstentions<span>{report.abstentions.length}</span></summary>{report.abstentions.map((a, i) => <p key={i}>{a.stage}: {a.reason}</p>)}</details>}
    <h3>Research questions and limitations</h3><div className="fact-stack">{report.questions.map(q => <div key={q.id}><strong>{q.question}</strong><p>{q.state} — {q.limitation}</p><small>{q.fact_ids.length} supporting fact IDs</small></div>)}</div>
    <h3>Candidate comparison</h3>{report.comparison.map(d => <details key={d.candidate_id}><summary>Universe {d.universe_id} · {d.facts.length} admissible facts · relevance requires review</summary>
      {!!d.omitted_facts && <p>Some saved facts are no longer admissible and were omitted.</p>}
      <ul>{d.facts.map(f => <li key={f.id}>{f.text} <small>({f.freshness})</small> {onInspectFact
        ? <button type="button" className="text-button" onClick={() => onInspectFact(f.id)}>Inspect provenance</button>
        : null}</li>)}</ul><HistoryTable data={d.history} /></details>)}
    <h3>Research concepts and bound audits</h3>{report.concepts.length ? report.concepts.map(c => {
      const audit = report.audits.find(a => a.proposal_id === c.id && a.candidate_id === c.candidate_id)
      return <details key={c.id}><summary>{c.payload.concept_title} · speculative research concept</summary><p>{c.payload.core_loop}</p><p>{c.payload.differentiator}</p><DesignDetails design={c.payload} />
        {audit ? <div><h3>Venture Scout — {audit.evidence_state.replaceAll('_', ' ')}</h3><small>Audit record <code>{audit.audit_id}</code></small>{audit.proposal && <><p>{audit.proposal.core_loop}</p><ol>{audit.proposal.build_steps.map((s, i) => <li key={i}>{s}</li>)}</ol><DesignDetails design={audit.proposal} onInspect={onInspectFact} /></>}<ul>{audit.risks.map((risk, i) => <li key={i}>{risk}</li>)}</ul></div> : <p>Not audited yet. A run drafts concepts and stops; the Venture Scout runs them from its audit queue when you choose to.</p>}
      </details>
    }) : (() => {
      // Drafting nothing is now a decision with a stated reason, not just an
      // absence. Making the reader open a fold to find out why is how the
      // generic version of this message wasted their time.
      const declined = report.abstentions?.find(a => a.stage === 'hunter')
      return <div className="warning-box"><strong>No concepts drafted</strong>
        <p>{declined ? declined.reason : 'No valid concepts were produced.'}</p>
        <small>This is an allowed abstention, not a failure. Inspect the limitations and errors above.</small></div>
    })()}
  </article>
}
