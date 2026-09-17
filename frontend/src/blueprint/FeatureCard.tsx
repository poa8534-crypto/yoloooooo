import type { FeatureSuggestion } from './api'

// A feature the architect proposed, as something to decide about rather than a
// checkbox to skim. Selected and unselected must be unmistakable at a glance:
// the whole stage turns on the person actually choosing, and an ambiguous card
// gets clicked by accident.

const COMPLEXITY_LABEL: Record<string, string> = { low: 'Low', medium: 'Medium', high: 'High' }
const PRIORITY_LABEL: Record<string, string> = {
  essential: 'Essential', high: 'Very high', medium: 'Medium', low: 'Low',
}

export function FeatureCard({ feature, onSelect, onEdit, busy }: {
  feature: FeatureSuggestion
  onSelect: (selected: boolean | null) => void
  onEdit?: () => void
  busy?: boolean
}) {
  const selected = feature.selected === true
  const rejected = feature.selected === false
  const state = selected ? 'selected' : rejected ? 'rejected' : 'undecided'
  return (
    <article className={`feature-card feature-${state}`} data-testid={`feature-${feature.id}`}>
      <header>
        <h3>{feature.title}</h3>
        {feature.origin === 'user' && <span className="feature-origin">Yours</span>}
      </header>
      <p className="feature-description">{feature.description}</p>

      <div className="feature-reason">
        <span>Why it helps</span>
        <p>{feature.reason}</p>
      </div>

      <dl className="feature-metrics">
        <div><dt>Build complexity</dt>
          <dd data-level={feature.implementation_complexity}>
            {COMPLEXITY_LABEL[feature.implementation_complexity]}</dd></div>
        <div><dt>MVP value</dt>
          <dd data-level={feature.mvp_priority}>{PRIORITY_LABEL[feature.mvp_priority]}</dd></div>
      </dl>

      {feature.required_systems.length > 0 && (
        <p className="feature-systems">Needs: {feature.required_systems.join(', ')}</p>
      )}

      <footer>
        {/* Three states, three buttons. A single toggle cannot express
            "not decided yet", and undecided is the state that blocks the build
            -- so it has to be visible and reachable. */}
        <button type="button" className={selected ? 'primary' : 'secondary'} disabled={busy}
          onClick={() => onSelect(selected ? null : true)}
          aria-pressed={selected}>
          {selected ? 'Selected ✓' : 'Add +'}
        </button>
        <button type="button" className={rejected ? 'primary' : 'text-button'} disabled={busy}
          onClick={() => onSelect(rejected ? null : false)}
          aria-pressed={rejected}>
          {rejected ? 'Excluded' : 'Not this'}
        </button>
        {onEdit && <button type="button" className="text-button" onClick={onEdit} disabled={busy}>
          Edit</button>}
      </footer>
    </article>
  )
}
