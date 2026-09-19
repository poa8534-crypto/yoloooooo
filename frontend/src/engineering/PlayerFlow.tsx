import type { PlayabilityGate, Plan } from './api'

// The player's path through the game, and how far a build has got along it.
//
// A different question from the architecture map beside it. That one shows the
// software; this shows what somebody actually does, in order, and which of
// those steps a player could reach today. Eleven systems built is not a number
// a player would recognise; "they can cast but the catch goes nowhere" is.
//
// Every state here comes from the backend's plan. The gates in particular are
// derived from which systems are built, so this cannot be more optimistic than
// the build is.

const GATE_LABEL: Record<string, string> = {
  spawnable: 'Can join', interactable: 'Can act', core_action: 'Mechanic works',
  rewardable: 'Gets the reward', loopable: 'Loop closes',
  understandable: 'Can tell what happened', persistent: 'Progress kept',
  mvp_playable: 'Playable slice',
}

export function PlayerFlow({ plan, onSelect }: {
  plan: Plan
  onSelect: (system: string) => void
}) {
  const nodes = plan.path?.nodes ?? []
  const built = new Set(plan.states.filter(s => s.state === 'done').map(s => s.name))

  if (!nodes.length) {
    return (
      <p className="quiet-note">
        This build has no player path. It was planned before the Engineer started
        working from a player journey, so there is nothing to show here rather
        than a flow invented from the system names.
      </p>
    )
  }

  return (
    <div className="player-flow">
      <ol className="flow-steps">
        {nodes.map((node, index) => {
          // Reached means every system the step needs is built. Partly is worth
          // its own state: it is the difference between "you can fish" and
          // "you can cast and the fish vanishes".
          const supporting = node.systems.length
          const ready = node.systems.filter(name => built.has(name)).length
          const state = supporting === 0 ? 'unsupported'
            : ready === supporting ? 'reached' : ready ? 'partial' : 'waiting'
          return (
            <li key={node.id} data-state={state}>
              <span className="flow-index">{index + 1}</span>
              <div className="flow-body">
                <strong>{node.label}</strong>
                {node.description && <p className="flow-detail">{node.description}</p>}
                <div className="chip-row">
                  {node.systems.map(name => (
                    <button type="button" key={name} className="chip"
                      data-built={built.has(name)} onClick={() => onSelect(name)}>
                      {name}
                    </button>
                  ))}
                  {supporting === 0 && <span className="chip chip-quiet">no system</span>}
                </div>
                {node.gate && (
                  <span className="flow-gate">{GATE_LABEL[node.gate] ?? node.gate}</span>
                )}
              </div>
            </li>
          )
        })}
      </ol>

      <div className="gate-ladder">
        <span className="micro-label">Playability</span>
        <ul>
          {plan.gates.map((gate: PlayabilityGate) => (
            <li key={gate.gate} data-passed={gate.passed}>
              <span className="gate-mark" aria-hidden="true">{gate.passed ? '✓' : '○'}</span>
              <span className="gate-name">{GATE_LABEL[gate.gate] ?? gate.gate}</span>
              <span className="gate-detail">{gate.detail}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}


export function BuildOrderView({ plan, current, onSelect }: {
  plan: Plan
  current: string | null
  onSelect: (system: string) => void
}) {
  // The order, and why each thing is where it is. The reason text comes from
  // the plan rather than being composed here.
  return (
    <ol className="order-list">
      {plan.states.map((state, index) => (
        <li key={state.name} data-state={state.state}
          data-current={state.name === current}>
          <span className="order-index">{index + 1}</span>
          <button type="button" className="order-name" onClick={() => onSelect(state.name)}>
            {state.name}
          </button>
          <span className="order-state" data-state={state.state}>{state.state}</span>
          <p className="order-why">
            {state.waiting_for.length
              ? `Waiting on ${state.waiting_for.join(', ')}.`
              : plan.why_now[state.name] ?? ''}
          </p>
        </li>
      ))}
    </ol>
  )
}
