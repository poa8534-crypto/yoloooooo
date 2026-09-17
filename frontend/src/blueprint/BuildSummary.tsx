import type { BlueprintView } from './api'

// The persistent "YOUR BUILD" panel. Everything on it is counted by the
// backend, never estimated here: a number the UI works out for itself can
// disagree with the one the build uses, and the disagreement is invisible.

const SCOPE_LABEL: Record<string, string> = {
  quick_prototype: 'Quick prototype',
  vertical_slice: 'Playable vertical slice',
  expanded_prototype: 'Expanded prototype',
}
const PERSISTENCE_LABEL: Record<string, string> = {
  session_only: 'Session only', saved_progression: 'Saved progression',
}
const WORLD_LABEL: Record<string, string> = {
  single_arena: 'Single arena', small_world: 'Small map',
  multiple_zones: 'Multiple zones', procedural: 'Procedural',
}
const VERDICT_LABEL: Record<string, string> = {
  lean: 'Lean', balanced: 'Balanced', too_large: 'Heavy',
}

export function BuildSummary({ view, onUseSuggestedMvp }: {
  view: BlueprintView
  onUseSuggestedMvp?: (keep: string[]) => void
}) {
  const { blueprint, readiness, scope, counts } = view
  const config = blueprint.config
  return (
    <aside className="build-summary" aria-label="Your build">
      <h2>Your build</h2>
      <strong className="build-summary-title">{blueprint.title}</strong>

      <dl>
        <div><dt>Prototype</dt><dd>{SCOPE_LABEL[config.scope]}</dd></div>
        <div><dt>Players</dt><dd>{config.min_players}–{config.max_players}</dd></div>
        <div><dt>Platforms</dt><dd>{config.platforms.join(' + ') || 'none chosen'}</dd></div>
        <div><dt>Progression</dt><dd>{PERSISTENCE_LABEL[config.persistence]}</dd></div>
        <div><dt>World</dt><dd>{WORLD_LABEL[config.world]}</dd></div>
        <div><dt>Selected features</dt><dd>{counts.selected_features}</dd></div>
        <div><dt>Systems</dt><dd>{counts.systems}</dd></div>
        <div><dt>Scope</dt><dd data-verdict={scope.verdict}>{VERDICT_LABEL[scope.verdict]}</dd></div>
      </dl>

      <section className="readiness">
        <h3>Build readiness</h3>
        <div className="readiness-bar" role="progressbar" aria-valuenow={readiness.percent}
          aria-valuemin={0} aria-valuemax={100} aria-label="Build readiness">
          <span style={{ width: `${readiness.percent}%` }} />
        </div>
        <p className="readiness-percent">{readiness.percent}%</p>
        {readiness.ready
          ? <p className="readiness-ready">✓ Ready to build</p>
          : <>
            <p className="readiness-missing-label">Still required</p>
            <ul className="readiness-missing">
              {readiness.missing.map(item => <li key={item.key}>{item.prompt}</li>)}
            </ul>
          </>}
      </section>

      {scope.verdict === 'too_large' && scope.suggested_cut.length > 0 && (
        <section className="scope-warning">
          <h3>Scope getting large</h3>
          {/* Counted from systems, dependencies and complexity by the backend --
              not a model's opinion about how big this feels. */}
          <p>{scope.systems} systems against a budget of {scope.budget} for this prototype size.</p>
          <p className="scope-cut">Suggested to drop: {scope.suggested_cut.join(', ')}</p>
          {onUseSuggestedMvp && (
            <button type="button" className="secondary"
              onClick={() => onUseSuggestedMvp(scope.suggested_cut)}>
              Use suggested MVP
            </button>
          )}
        </section>
      )}
    </aside>
  )
}
