/**
 * The Engineering Agent.
 *
 * Section 24 is the rule this file exists to hold: the visualisation shows
 * ACTUAL state. So the tests are about what the UI must NOT invent -- a node it
 * decided is building, a tick it awarded itself, progress that moves while the
 * backend is idle, a Studio tree that implies a sync nobody performed.
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EngineeringWorkspace } from './EngineeringWorkspace'
import {
  clusters, duration, layout,
  type BuildGraph, type Directive, type ExplorerRow, type GraphNode, type NodeState,
  type Steering,
} from './api'

function node(id: string, state: NodeState, depends: string[] = [], order = 0,
  layer: GraphNode['layer'] = 'server'): GraphNode {
  return {
    id, name: id, layer, path: `src/${layer}/${id}.luau`,
    purpose: `What ${id} is for.`, acceptance_criteria: [`${id} refuses a bad amount.`],
    depends_on: depends, state, detail: '', branch: null, commit: '', attempts: 0,
    studio_path: `ServerScriptService/Server/${id}`, studio_class: 'ModuleScript', order,
  }
}

function directive(overrides: Partial<Directive> = {}): Directive {
  return {
    id: 'steer-1', text: 'Cap every wave at eight infected.', system: '',
    created_at: '2026-09-17T23:30:00', status: 'pending', carried_into: [],
    reachable_when_written: ['InfectedService'], ...overrides,
  }
}

function steering(overrides: Partial<Steering> = {}): Steering {
  return {
    directives: [], reachable: ['InfectedService'], accepting: true, max_active: 6, ...overrides,
  }
}

function explorerRow(name: string, children: ExplorerRow[] = [],
  state: NodeState | '' = '', system = ''): ExplorerRow {
  return { name, children, state, system, class: children.length ? 'Folder' : 'ModuleScript' }
}

function graph(overrides: Partial<BuildGraph> = {}): BuildGraph {
  const nodes = overrides.nodes ?? [
    node('ResearchService', 'built', [], 0),
    node('WaveService', 'building', ['ResearchService'], 1),
    node('InfectedService', 'waiting', [], 2),
  ]
  return {
    build_id: 'build-1', status: 'generating', title: 'Zombie Quarantine Lab',
    spec_revision: 4, content_hash: 'cf986b30',
    goal: {
      title: 'Zombie Quarantine Lab', intent: 'A research bunker under siege.',
      included_features: ['Mutation Research'], excluded_features: ['Player Infection'],
      constraints: ['NO DATASTORE.'], acceptance_criteria_total: 9,
    },
    nodes,
    edges: nodes.flatMap(n => n.depends_on.map(from => ({ from, to: n.id }))),
    current: 'WaveService',
    counts: { built: 1, building: 1, waiting: 1, total: 3 },
    pace: {
      started_at: '2026-09-17T23:19:40', completed_at: '', systems_measured: 1,
      average_system_seconds: 128, slowest_system_seconds: 128,
      per_system_seconds: { ResearchService: 128 }, attempts_spent: 1, systems_accepted: 1,
    },
    sync: { batch_id: '', operations: 0, sent_at: '',
      applied: null, skipped: null, failed: null, reported_at: '' },
    steering: steering(),
    explorer: [explorerRow('ServerScriptService', [
      explorerRow('Server', [explorerRow('ResearchService', [], 'built', 'ResearchService')]),
    ])],
    events: [{ at: '2026-09-17T23:19:40', stage: 'system_built', detail: 'ResearchService: accepted' }],
    ...overrides,
  }
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  sessionStorage.clear()
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function reply(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(body)),
  } as Response)
}

function serve(options: { builds?: unknown; graph?: BuildGraph; studio?: unknown
  source?: unknown; steer?: unknown; steerStatus?: number } = {}) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes('/directives')) {
      return reply(options.steer ?? steering({ directives: [directive()] }),
        options.steerStatus ?? 200)
    }
    if (url.includes('/source')) {
      return reply(options.source ?? { system: 'ResearchService', state: 'built',
        source: 'local ResearchService = {}', path: 'src/server/ResearchService.luau',
        commit: 'abcdef1234567890', branch: 'engineer/x', detail: '', lines: 1 })
    }
    if (url.includes('/graph')) return reply(options.graph ?? graph())
    if (url.includes('/api/builds')) {
      return reply(options.builds ?? {
        builds: [{ id: 'build-1', title: 'Zombie Quarantine Lab', status: 'generating',
          spec_revision: 4, content_hash: 'cf986b30', created_at: '2026-09-17T23:19:40',
          completed_at: null }],
      })
    }
    if (url.includes('/api/studio')) {
      return reply(options.studio ?? { bridge: 'offline', plugin_connected: false,
        detail: 'The local bridge is not running.' })
    }
    return reply({ detail: 'unexpected' }, 404)
  })
}

describe('layout', () => {
  it('puts a system one column right of everything it needs', () => {
    const placed = layout([
      node('A', 'built'), node('B', 'waiting', ['A']), node('C', 'waiting', ['B']),
    ])
    const column = Object.fromEntries(placed.map(n => [n.id, n.column]))

    expect(column.A).toBe(0)
    expect(column.B).toBe(1)
    expect(column.C).toBe(2)
  })

  it('places independent systems in the same column', () => {
    const placed = layout([node('A', 'built'), node('B', 'waiting')])
    expect(placed[0].column).toBe(placed[1].column)
    expect(placed[0].row).not.toBe(placed[1].row)
  })
})

describe('clusters', () => {
  it('gives each layer its own band of rows', () => {
    const { placed, bands } = clusters([
      node('A', 'built', [], 0, 'server'), node('B', 'waiting', [], 1, 'server'),
      node('C', 'waiting', [], 2, 'client'),
    ])
    const row = Object.fromEntries(placed.map(n => [n.id, n.row]))

    expect(bands.map(band => band.layer)).toEqual(['server', 'client'])
    expect(row.C).toBeGreaterThan(Math.max(row.A, row.B))
  })

  it('keeps the dependency column even across layers', () => {
    // A client controller that needs a server system still sits to its right,
    // because the column is build order and build order does not care which
    // machine the code runs on.
    const { placed } = clusters([
      node('Service', 'built', [], 0, 'server'),
      node('Controller', 'waiting', ['Service'], 1, 'client'),
    ])
    const column = Object.fromEntries(placed.map(n => [n.id, n.column]))

    expect(column.Controller).toBe(column.Service + 1)
  })
})

describe('duration', () => {
  it('says nothing rather than zero when there is nothing measured', () => {
    expect(duration(null)).toBe('—')
    expect(duration(undefined)).toBe('—')
  })

  it('reads as time', () => {
    expect(duration(45)).toBe('45s')
    expect(duration(128)).toBe('2m 08s')
  })
})

describe('the workspace', () => {
  it('shows an empty state rather than an empty graph', async () => {
    serve({ builds: { builds: [] } })
    const onNavigate = vi.fn()

    render(<EngineeringWorkspace onNavigate={onNavigate} />)

    await waitFor(() => expect(screen.getByText(/no build yet/i)).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: /open venture scout/i }))
    expect(onNavigate).toHaveBeenCalledWith('scout')
  })

  it('renders a node per system and an edge per dependency', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    const map = screen.getByTestId('architecture-map')
    expect(within(map).getByLabelText(/ResearchService, Built/)).toBeTruthy()
    expect(within(map).getByLabelText(/WaveService, Building/)).toBeTruthy()
    expect(within(map).getByLabelText(/InfectedService, Waiting/)).toBeTruthy()
  })

  it('takes every node state from the backend rather than deciding one', async () => {
    // The backend says WaveService is building. If the UI worked it out from
    // build order it could disagree, and nobody would see the disagreement.
    serve({ graph: graph({ current: null, status: 'succeeded',
      nodes: [node('ResearchService', 'built'), node('WaveService', 'refused')] }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    const map = screen.getByTestId('architecture-map')
    expect(within(map).getByLabelText(/WaveService, Refused by the gate/)).toBeTruthy()
  })

  it('says nothing is running when the backend says nothing is running', async () => {
    serve({ graph: graph({ current: null, status: 'succeeded' }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getAllByText('Not running').length).toBeGreaterThan(0))
  })

  it('names the system actually being worked on', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByText('Working on WaveService')).toBeTruthy())
  })

  it('shows the file being generated, in DataModel terms', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() =>
      expect(screen.getByText('ServerScriptService/Server/WaveService')).toBeTruthy())
  })

  it('opens the inspector for the node that was clicked', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())

    await userEvent.click(screen.getByLabelText(/ResearchService, Built/))

    const inspector = screen.getByLabelText('ResearchService inspector')
    expect(within(inspector).getByText('What ResearchService is for.')).toBeTruthy()
    expect(within(inspector).getByText('src/server/ResearchService.luau')).toBeTruthy()
  })

  it('shows the goal and what was excluded when no node is selected', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('A research bunker under siege.')).toBeTruthy())
    expect(screen.getByText('Player Infection')).toBeTruthy()
    expect(screen.getByText('NO DATASTORE.')).toBeTruthy()
  })

  it('counts progress from the backend counts', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getAllByText(/1 of 3 systems built/).length)
      .toBeGreaterThanOrEqual(1))
  })

  it('shows recorded activity and invents none', async () => {
    serve({ graph: graph({ events: [] }) })
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('No activity recorded yet.')).toBeTruthy())
  })

  it('shows Studio as disconnected with the reason', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('Not connected')).toBeTruthy())
    expect(screen.getByText('The local bridge is not running.')).toBeTruthy()
  })

  it('shows Studio as connected with the place and the measured latency', async () => {
    serve({ studio: { bridge: 'online', plugin_connected: true, latency_ms: 4.2,
      studio: { place_name: 'Number 1', mode: 'edit', plugin_version: '0.2.0' } } })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('Connected')).toBeTruthy())
    expect(screen.getByText(/Number 1/)).toBeTruthy()
    expect(screen.getByText('Bridge 4.2ms')).toBeTruthy()
  })

  it('shows a refused system with the reason the gate gave', async () => {
    const refused = node('WaveService', 'refused')
    refused.detail = 'All 6 attempts were refused'
    serve({ graph: graph({ nodes: [refused], current: null, status: 'partial',
      counts: { refused: 1, total: 1 } }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    await userEvent.click(screen.getByLabelText(/WaveService, Refused by the gate/))

    expect(screen.getAllByText('All 6 attempts were refused').length).toBeGreaterThan(0)
  })

  it('reports measured pace and never a prediction', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    // How many were measured is part of the claim: an average over one system
    // is not the same statement as an average over nine. Retried, because the
    // first paint happens before the graph lands and says nothing at all.
    await waitFor(() =>
      expect(screen.getByText(/average per system/).textContent).toMatch(/1 measured/))
    // The thing that must not appear. A countdown over systems nobody has
    // started is a guess wearing the same typeface as a measurement.
    expect(screen.queryByText(/remaining/i)).toBeNull()
  })

  it('leaves a measurement blank rather than showing zero for it', async () => {
    serve({ graph: graph({ pace: { started_at: '2026-09-17T23:19:40', completed_at: '',
      systems_measured: 0, average_system_seconds: null, slowest_system_seconds: null,
      per_system_seconds: {}, attempts_spent: 0, systems_accepted: 0 } }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2))
  })
})

describe('the Studio explorer', () => {
  it('says nothing has been sent when no batch was queued', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('Not sent yet')).toBeTruthy())
    expect(screen.getByText(/Nothing has been sent to Studio/)).toBeTruthy()
  })

  it('reports what Studio applied once Studio has reported', async () => {
    serve({ graph: graph({ sync: { batch_id: 'batch-7', operations: 21,
      sent_at: '2026-09-17T23:40:00', applied: 21, skipped: 0, failed: 0,
      reported_at: '2026-09-17T23:40:30' } }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('21 applied')).toBeTruthy())
    expect(screen.getByText(/21 applied · 0 unchanged · 0 failed/)).toBeTruthy()
  })

  it('carries each leaf state into the tree', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    const explorer = await screen.findByLabelText('Roblox Studio explorer')
    expect(within(explorer).getByText('ServerScriptService')).toBeTruthy()
    expect(within(explorer).getByText('built')).toBeTruthy()
  })
})

describe('the node inspector', () => {
  it('shows the accepted Luau, read from the commit the gate accepted', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    await userEvent.click(screen.getByLabelText(/ResearchService, Built/))

    await userEvent.click(screen.getByRole('tab', { name: /luau code/i }))

    await waitFor(() => expect(screen.getByText('local ResearchService = {}')).toBeTruthy())
  })

  it('refuses to show refused code under the system name', async () => {
    // The last rejected attempt is not this system's source. Showing it here
    // would present code six checks threw out as the code that was built.
    const refused = node('WaveService', 'refused')
    refused.detail = 'All 6 attempts were refused'
    serve({
      graph: graph({ nodes: [refused], current: null, status: 'partial',
        counts: { refused: 1, total: 1 } }),
      source: { system: 'WaveService', state: 'refused', source: '', path: '', commit: '',
        branch: 'engineer/wave', detail: 'All 6 attempts were refused', lines: 0 },
    })

    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    await userEvent.click(screen.getByLabelText(/WaveService, Refused/))
    await userEvent.click(screen.getByRole('tab', { name: /luau code/i }))

    await waitFor(() => expect(screen.getByText(/No accepted source/)).toBeTruthy())
  })

  it('lists acceptance criteria without ticking any of them', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    await userEvent.click(screen.getByLabelText(/ResearchService, Built/))
    await userEvent.click(screen.getByRole('tab', { name: /criteria/i }))

    expect(screen.getByText('ResearchService refuses a bad amount.')).toBeTruthy()
    expect(screen.getByText(/no per-criterion verdict/i)).toBeTruthy()
  })

  it('cannot ask Studio to open a script without the bridge token', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    await userEvent.click(screen.getByLabelText(/ResearchService, Built/))

    expect(screen.getByRole('button', { name: /open in studio/i })
      .hasAttribute('disabled')).toBe(true)
  })
})

describe('the steering panel', () => {
  it('lists what a directive will reach before it is written', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    const panel = await screen.findByLabelText('Live steering')
    // The claim the panel makes, made before the button rather than after it.
    expect(within(panel).getByText(/Will be carried into:/)).toBeTruthy()
    expect(within(panel).getByText('InfectedService')).toBeTruthy()
  })

  it('offers only the systems the backend says are reachable', async () => {
    // WaveService is in flight. Its prompt was built before this directive
    // existed, so it must not be offered as something the directive can steer.
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)

    const panel = await screen.findByLabelText('Live steering')
    const options = within(panel).getAllByRole('option').map(option => option.textContent)

    expect(options).toEqual(['Every system still to be written', 'InfectedService only'])
  })

  it('will not send an instruction too short to be one', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    const panel = await screen.findByLabelText('Live steering')

    await userEvent.type(within(panel).getByRole('textbox'), 'faster')

    expect(within(panel).getByRole('button', { name: /add directive/i })
      .hasAttribute('disabled')).toBe(true)
  })

  it('sends the instruction and the system it applies to', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    const panel = await screen.findByLabelText('Live steering')

    await userEvent.type(within(panel).getByRole('textbox'),
      'Cap every wave at eight infected.')
    await userEvent.selectOptions(within(panel).getByRole('combobox'), 'InfectedService')
    await userEvent.click(within(panel).getByRole('button', { name: /add directive/i }))

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(call =>
        String(call[0]).includes('/directives') && call[1]?.method === 'POST')
      expect(posted).toBeTruthy()
      expect(JSON.parse(posted[1].body as string))
        .toEqual({ text: 'Cap every wave at eight infected.', system: 'InfectedService' })
    })
  })

  it('shows the refusal the backend gave rather than swallowing it', async () => {
    serve({ steer: { detail: 'InfectedService has already been started' }, steerStatus: 409 })
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    const panel = await screen.findByLabelText('Live steering')

    await userEvent.type(within(panel).getByRole('textbox'), 'Cap every wave at eight.')
    await userEvent.click(within(panel).getByRole('button', { name: /add directive/i }))

    await waitFor(() => expect(screen.getByText(/already been started/)).toBeTruthy())
  })

  it('names the systems a carried directive actually went into', async () => {
    // "Applied" beside a tick is a claim. Which prompts it reached is the fact
    // that makes the claim checkable afterwards.
    serve({ graph: graph({ steering: steering({ directives: [directive({
      status: 'carried', carried_into: ['InfectedService', 'AirlockService'] })] }) }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    const panel = await screen.findByLabelText('Live steering')
    expect(within(panel).getByText(/Carried into InfectedService, AirlockService/)).toBeTruthy()
  })

  it('does not offer to withdraw a directive that already went into a prompt', async () => {
    serve({ graph: graph({ steering: steering({ directives: [directive({
      status: 'carried', carried_into: ['InfectedService'] })] }) }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    const panel = await screen.findByLabelText('Live steering')
    expect(within(panel).queryByRole('button', { name: /withdraw/i })).toBeNull()
  })

  it('says a directive reached nothing rather than leaving it looking imminent', async () => {
    serve({ graph: graph({ status: 'succeeded', current: null,
      steering: steering({ accepting: false, reachable: [],
        directives: [directive({ status: 'stale' })] }) }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    const panel = await screen.findByLabelText('Live steering')
    expect(within(panel).getByText(/The build ended before this reached anything/)).toBeTruthy()
    // And no form, because there is no prompt left to put an instruction into.
    expect(within(panel).queryByRole('textbox')).toBeNull()
  })
})

describe('a system the project already had', () => {
  it('is not drawn as waiting, and never becomes built', async () => {
    // The bug this state exists for: the graph used to assume the Engineer
    // works through build order, so a system it skips showed as in flight for
    // the whole run.
    serve({ graph: graph({ current: 'InfectedService', nodes: [
      node('WaveService', 'existing'), node('InfectedService', 'building')] }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    const map = screen.getByTestId('architecture-map')
    expect(within(map).getByLabelText(/WaveService, Already in the project/)).toBeTruthy()
    expect(screen.getByText('Working on InfectedService')).toBeTruthy()
  })
})
