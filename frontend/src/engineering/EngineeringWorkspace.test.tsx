/**
 * The Engineering Agent.
 *
 * Section 24 is the rule this file exists to hold: the visualisation shows
 * ACTUAL state. So the tests are about what the UI must NOT invent -- a node it
 * decided is building, a tick it awarded itself, progress that moves while the
 * backend is idle.
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EngineeringWorkspace } from './EngineeringWorkspace'
import { layout, type BuildGraph, type GraphNode, type NodeState } from './api'

function node(id: string, state: NodeState, depends: string[] = [], order = 0): GraphNode {
  return {
    id, name: id, layer: 'server', path: `src/server/${id}.luau`,
    purpose: `What ${id} is for.`, acceptance_criteria: [`${id} refuses a bad amount.`],
    depends_on: depends, state, detail: '', branch: null, order,
  }
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

function serve(options: { builds?: unknown; graph?: BuildGraph; studio?: unknown } = {}) {
  fetchMock.mockImplementation((url: string) => {
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

    await waitFor(() => expect(screen.getByText('Not running')).toBeTruthy())
  })

  it('names the system actually being worked on', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByText('Working on WaveService')).toBeTruthy())
  })

  it('opens the inspector for the node that was clicked', async () => {
    serve()
    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())

    await userEvent.click(screen.getByLabelText(/ResearchService, Built/))

    const inspector = screen.getByText('Goal').closest('aside') as HTMLElement
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

    // Twice on purpose: the timeline says it beside the list it describes, and
    // the goal panel says it beside the goal. Both read the same counts.
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

  it('shows Studio as connected with the place it is looking at', async () => {
    serve({ studio: { bridge: 'online', plugin_connected: true,
      studio: { place_name: 'Number 1', mode: 'edit' } } })

    render(<EngineeringWorkspace onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('Connected')).toBeTruthy())
    expect(screen.getByText(/Number 1/)).toBeTruthy()
  })

  it('shows a refused system with the reason the gate gave', async () => {
    const refused = node('WaveService', 'refused')
    refused.detail = 'All 6 attempts were refused'
    serve({ graph: graph({ nodes: [refused], current: null, status: 'partial',
      counts: { refused: 1, total: 1 } }) })

    render(<EngineeringWorkspace onNavigate={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('architecture-map')).toBeTruthy())
    await userEvent.click(screen.getByLabelText(/WaveService, Refused by the gate/))

    expect(screen.getByText('All 6 attempts were refused')).toBeTruthy()
  })
})
