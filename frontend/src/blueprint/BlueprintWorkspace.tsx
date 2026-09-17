import { useCallback, useEffect, useState } from 'react'
import './blueprint.css'
import {
  ApiError, blueprintApi,
  type BlueprintConfig, type BlueprintView, type BuildResult, type BuildStarted,
  type SpecificationView,
} from './api'
import { BuildSummary } from './BuildSummary'
import { FeatureCard } from './FeatureCard'
import { StudioConnection, useStudio } from './StudioConnection'

// The stage between an idea and a build. One workspace rather than a wizard:
// the person is shaping one thing, and five pages would make it five things.
//
// Every number shown here comes from the backend. Readiness, scope and system
// counts are computed there and recomputed on every read, so nothing on screen
// can drift from what the build will actually use.

type Step = 'refine' | 'features' | 'setup' | 'systems' | 'review'

const STEPS: Array<[Step, string]> = [
  ['refine', 'Refine'], ['features', 'Features'], ['setup', 'Setup'],
  ['systems', 'Systems'], ['review', 'Review'],
]

const SCOPES: Array<[BlueprintConfig['scope'], string]> = [
  ['quick_prototype', 'Quick prototype'],
  ['vertical_slice', 'Playable vertical slice'],
  ['expanded_prototype', 'Expanded prototype'],
]
const WORLDS: Array<[BlueprintConfig['world'], string]> = [
  ['single_arena', 'Arena'], ['small_world', 'Small map'],
  ['multiple_zones', 'Multiple zones'], ['procedural', 'Procedural'],
]
const PLAYER_PRESETS: Array<[string, number, number]> = [
  ['Solo', 1, 1], ['1-4', 1, 4], ['4-8', 4, 8],
]

export function BlueprintWorkspace({ auditId, title, originalIdea, onClose }: {
  auditId: string
  title: string
  originalIdea?: { core_loop?: string; differentiator?: string; risks?: string[] }
  onClose: () => void
}) {
  const [view, setView] = useState<BlueprintView | null>(null)
  const [step, setStep] = useState<Step>('refine')
  const [intent, setIntent] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [spec, setSpec] = useState<SpecificationView | null>(null)
  const [build, setBuild] = useState<BuildStarted | null>(null)
  const [result, setResult] = useState<BuildResult | null>(null)
  const studio = useStudio()

  const run = useCallback(async function <T>(label: string, work: () => Promise<T>) {
    setBusy(label)
    setError('')
    try {
      return await work()
    } catch (caught) {
      // The backend's sentence, verbatim. Those sentences say what to do about
      // the problem; a generic message throws the instruction away.
      setError(caught instanceof ApiError ? caught.message : (caught as Error).message)
      return null
    } finally {
      setBusy('')
    }
  }, [])

  useEffect(() => {
    void run('Opening', async () => {
      const opened = await blueprintApi.proceed(auditId, title)
      setView(opened)
      setIntent(opened.blueprint.user_intent)
      if (opened.blueprint.suggestions.length) setStep('features')
      return opened
    })
  }, [auditId, title, run])

  const blueprint = view?.blueprint
  const id = blueprint?.id

  async function enhance() {
    if (!id) return
    const saved = await run('Saving what you wrote', () => blueprintApi.setIntent(id, intent))
    if (!saved) return
    setView(saved)
    const withSuggestions = await run('The architect is reading your idea', () =>
      blueprintApi.suggest(id))
    if (withSuggestions) { setView(withSuggestions); setStep('features') }
  }

  async function decide(featureId: string, selected: boolean | null) {
    if (!id) return
    const updated = await run('Saving', () => blueprintApi.select(id, { [featureId]: selected }))
    if (updated) setView(updated)
  }

  async function suggestMore() {
    if (!id) return
    const updated = await run('Asking for alternatives', () => blueprintApi.suggest(id))
    if (updated) setView(updated)
  }

  async function saveConfig(changes: Partial<BlueprintConfig>) {
    if (!id || !blueprint) return
    const updated = await run('Saving setup', () =>
      blueprintApi.setConfig(id, { ...blueprint.config, ...changes }))
    if (updated) setView(updated)
  }

  async function planSystems() {
    if (!id) return
    const updated = await run('Working out which systems this needs', () =>
      blueprintApi.systems(id))
    if (updated) { setView(updated); setStep('systems') }
  }

  async function review() {
    if (!id) return
    const compiled = await run('Compiling the specification', () => blueprintApi.specification(id))
    if (compiled) { setSpec(compiled); setStep('review') }
  }

  async function startBuild() {
    if (!id) return
    const started = await run('Sending the build to Studio', () =>
      blueprintApi.build(id, studio.token, true))
    if (!started) return
    setBuild(started)
    setResult(null)
    // Poll the bridge for what the plugin reported. Real operation results, not
    // a progress animation: every line is something Studio actually did.
    const poll = setInterval(async () => {
      try {
        const answer = await blueprintApi.buildResult(started.batch_id, studio.token)
        setResult(answer)
        if (answer.status !== 'pending') clearInterval(poll)
      } catch {
        // The plugin may not have reported yet; keep asking.
      }
    }, 1500)
    setTimeout(() => clearInterval(poll), 120000)
  }

  if (!view || !blueprint) {
    return <div className="blueprint-workspace">
      <p className="drawer-loading">{busy || 'Opening the blueprint...'}</p>
      {error && <div className="warning-box"><p>{error}</p></div>}
    </div>
  }

  const readiness = view.readiness
  const undecided = view.counts.undecided_features
  const connected = Boolean(studio.state?.plugin_connected)

  return (
    <div className="blueprint-workspace" data-testid="blueprint-workspace">
      <header className="blueprint-header">
        <div>
          <span className="blueprint-eyebrow">Blueprint</span>
          <strong>{blueprint.title}</strong>
        </div>
        <nav className="blueprint-steps" aria-label="Blueprint progress">
          {STEPS.map(([key, label]) => (
            <button key={key} type="button" className="blueprint-step"
              data-active={step === key} onClick={() => setStep(key)}>{label}</button>
          ))}
        </nav>
        <button type="button" className="text-button" onClick={onClose}>Close</button>
      </header>

      {error && <div className="warning-box" role="alert"><p>{error}</p></div>}
      {busy && <p className="blueprint-busy" role="status">{busy}</p>}

      <div className="blueprint-columns">
        <main className="blueprint-main">
          {step === 'refine' && (
            <section className="data-panel">
              <h2>Make this idea yours</h2>
              <p className="body-copy">
                Describe how you imagine the game. You can change mechanics, progression,
                multiplayer, the world, difficulty, or anything else.
              </p>
              <textarea className="intent-input" rows={8} value={intent}
                onChange={event => setIntent(event.target.value)}
                aria-label="How you imagine this game"
                placeholder="I want players trapped inside an underground research facility. They collect samples from zombies and use them to research new technology. The research they perform should affect which zombies mutate on later nights." />
              <button type="button" className="primary" disabled={!intent.trim() || Boolean(busy)}
                onClick={enhance}>Enhance blueprint</button>

              {originalIdea && (
                <details className="original-idea">
                  <summary>Original Scout idea</summary>
                  {originalIdea.core_loop && <><h3>Core loop</h3><p>{originalIdea.core_loop}</p></>}
                  {originalIdea.differentiator &&
                    <><h3>Differentiator</h3><p>{originalIdea.differentiator}</p></>}
                  {originalIdea.risks && originalIdea.risks.length > 0 && <><h3>Risks</h3>
                    <ul>{originalIdea.risks.map((risk, index) =>
                      <li key={index}>{risk}</li>)}</ul></>}
                </details>
              )}
            </section>
          )}

          {step === 'features' && (
            <section className="data-panel">
              <h2>Suggested features</h2>
              {blueprint.summary && <p className="body-copy">{blueprint.summary}</p>}
              {blueprint.suggestions.length === 0
                ? <p className="body-copy">No suggestions yet. Describe the game first.</p>
                : <>
                  <div className="feature-grid">
                    {blueprint.suggestions.map(feature => (
                      <FeatureCard key={feature.id} feature={feature} busy={Boolean(busy)}
                        onSelect={selected => decide(feature.id, selected)} />
                    ))}
                  </div>
                  {undecided > 0 && (
                    <p className="gate-hint">
                      {undecided} suggestion{undecided === 1 ? '' : 's'} still undecided.
                      Nothing is added unless you choose it.
                    </p>
                  )}
                  <div className="feature-actions">
                    <button type="button" className="secondary" disabled={Boolean(busy)}
                      onClick={suggestMore}>Suggest more</button>
                    <button type="button" className="primary"
                      disabled={undecided > 0 || Boolean(busy)} onClick={planSystems}>
                      Work out the systems
                    </button>
                  </div>
                </>}
            </section>
          )}

          {step === 'setup' && (
            <section className="data-panel">
              <h2>Game setup</h2>
              <fieldset><legend>Prototype</legend>
                {SCOPES.map(([value, label]) => (
                  <button key={value} type="button"
                    className={blueprint.config.scope === value ? 'primary' : 'secondary'}
                    onClick={() => saveConfig({ scope: value })}>{label}</button>
                ))}
              </fieldset>
              <fieldset><legend>Players</legend>
                {PLAYER_PRESETS.map(([label, low, high]) => (
                  <button key={label} type="button"
                    className={blueprint.config.min_players === low
                      && blueprint.config.max_players === high ? 'primary' : 'secondary'}
                    onClick={() => saveConfig({ min_players: low, max_players: high })}>
                    {label}</button>
                ))}
              </fieldset>
              <fieldset><legend>Progression</legend>
                <button type="button"
                  className={blueprint.config.persistence === 'session_only' ? 'primary' : 'secondary'}
                  onClick={() => saveConfig({ persistence: 'session_only' })}>Session only</button>
                <button type="button"
                  className={blueprint.config.persistence === 'saved_progression'
                    ? 'primary' : 'secondary'}
                  onClick={() => saveConfig({ persistence: 'saved_progression' })}>
                  Saved progression</button>
              </fieldset>
              <fieldset><legend>World</legend>
                {WORLDS.map(([value, label]) => (
                  <button key={value} type="button"
                    className={blueprint.config.world === value ? 'primary' : 'secondary'}
                    onClick={() => saveConfig({ world: value })}>{label}</button>
                ))}
              </fieldset>
            </section>
          )}

          {step === 'systems' && (
            <section className="data-panel">
              <h2>Systems to build</h2>
              {blueprint.systems.length === 0
                ? <p className="body-copy">No systems yet. Decide on the features first.</p>
                : <ul className="system-list">
                  {blueprint.systems.map(system => (
                    <li key={system.name} data-layer={system.layer}>
                      <strong>{system.name}</strong>
                      <span className="system-layer">{system.layer}</span>
                      <p>{system.purpose}</p>
                      {system.depends_on.length > 0 &&
                        <p className="system-depends">Needs: {system.depends_on.join(', ')}</p>}
                      <p className="system-criteria">
                        {system.acceptance_criteria.length} acceptance criteria</p>
                    </li>
                  ))}
                </ul>}
              <button type="button" className="primary"
                disabled={!readiness.ready || Boolean(busy)} onClick={review}>
                Review the build
              </button>
            </section>
          )}

          {step === 'review' && (
            <section className="data-panel">
              <h2>What the Engineer will build</h2>
              {!spec
                ? <p className="body-copy">Compile the specification from the Systems step.</p>
                : <>
                  <dl className="spec-preview">
                    <div><dt>Players</dt><dd>{spec.preview.players}</dd></div>
                    <div><dt>World</dt><dd>{spec.preview.world}</dd></div>
                    <div><dt>Systems</dt><dd>{spec.preview.systems_total}</dd></div>
                    <div><dt>Server</dt><dd>{spec.preview.server_systems.length}</dd></div>
                    <div><dt>Client</dt><dd>{spec.preview.client_systems.length}</dd></div>
                    <div><dt>Shared</dt><dd>{spec.preview.shared_systems.length}</dd></div>
                    <div><dt>Acceptance criteria</dt>
                      <dd>{spec.preview.acceptance_criteria_count}</dd></div>
                    <div><dt>Complexity</dt><dd>{spec.preview.estimated_complexity}</dd></div>
                  </dl>
                  <h3>Build order</h3>
                  <ol className="build-order">
                    {spec.preview.build_order.map(name => <li key={name}>{name}</li>)}
                  </ol>
                  {spec.preview.excluded_features.length > 0 && <>
                    <h3>Excluded</h3>
                    <ul>{spec.preview.excluded_features.map(name => <li key={name}>{name}</li>)}</ul>
                  </>}
                  {spec.preview.asset_requirements.length > 0 && <>
                    <h3>Missing assets</h3>
                    <ul>{spec.preview.asset_requirements.map(name =>
                      <li key={name}>{name}</li>)}</ul>
                    <p className="gate-hint">Placeholders are used where practical.</p>
                  </>}
                  <h3>Constraints the Engineer must obey</h3>
                  <ul>{spec.preview.technical_constraints.map((line, index) =>
                    <li key={index}>{line}</li>)}</ul>
                </>}

              <div className="build-launch">
                <StudioConnection state={studio.state} token={studio.token}
                  onToken={studio.setToken} error={studio.error} />
                <button type="button" className="build-button"
                  disabled={!readiness.ready || !connected || Boolean(busy)}
                  onClick={startBuild}>
                  Build in Roblox Studio
                </button>
                {!connected && <p className="gate-hint">
                  Studio has to be connected before a build can be sent.</p>}
              </div>

              {build && (
                <section className="build-report" data-testid="build-report">
                  <h3>Build {build.build_id}</h3>
                  <p>{build.files} file(s) sent as {build.operations} operation(s).</p>
                  {build.missing_systems.length > 0 && (
                    <div className="warning-box">
                      <p>The specification asks for systems nobody has written yet, so they were
                        not sent: {build.missing_systems.join(', ')}.</p>
                    </div>
                  )}
                  {result && result.status === 'pending' &&
                    <p role="status">Waiting for Studio to report</p>}
                  {result && result.status !== 'pending' && <>
                    <p><strong>{result.applied}</strong> applied, {result.skipped} unchanged,{' '}
                      <strong>{result.failed_count}</strong> failed
                      {result.place_name ? ` in ${result.place_name}` : ''}.</p>
                    <ul className="operation-results">
                      {(result.results ?? []).filter(entry => entry.status === 'failed')
                        .map(entry => <li key={entry.operation_id}>
                          <code>{entry.operation_id}</code> {entry.detail}</li>)}
                    </ul>
                  </>}
                </section>
              )}
            </section>
          )}
        </main>

        <BuildSummary view={view} />
      </div>
    </div>
  )
}
