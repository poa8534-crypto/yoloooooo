/**
 * The Blueprint stage, in the browser.
 *
 * The thing worth testing is honesty. A UI can show a percentage, a selected
 * card and a Build button that all look right while meaning nothing, and the
 * failure is invisible. So: a rejected feature stays rejected, readiness comes
 * from the backend rather than from arithmetic here, the build button is
 * disabled while Studio is not connected, and a failure shows the backend's own
 * sentence rather than "something went wrong".
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BlueprintWorkspace } from './BlueprintWorkspace'
import { FeatureCard } from './FeatureCard'
import { BuildSummary } from './BuildSummary'
import type { BlueprintView, FeatureSuggestion } from './api'

function feature(id: string, overrides: Partial<FeatureSuggestion> = {}): FeatureSuggestion {
  return {
    id, title: `Feature ${id}`, description: 'something concrete about this game',
    reason: 'why it helps this game specifically', player_value: '',
    implementation_complexity: 'medium', mvp_priority: 'high', retention_impact: 'medium',
    risk: '', dependencies: [], required_systems: [], selected: null, origin: 'architect',
    ...overrides,
  }
}

function view(overrides: Partial<BlueprintView> = {}): BlueprintView {
  const base: BlueprintView = {
    blueprint: {
      id: 'bp1', project_id: 'p1', audit_id: 'audit-1', title: 'Zombie Quarantine Lab',
      revision: 1, user_intent: '', summary: '', suggestions: [], systems: [],
      asset_requirements: [], status: 'draft',
      config: {
        scope: 'vertical_slice', min_players: 1, max_players: 4, platforms: ['desktop'],
        persistence: 'session_only', world: 'single_arena', build_target: 'clean_prototype',
        tone: '', emphasis: {},
      },
    },
    readiness: { percent: 50, ready: false, missing: [{ key: 'intent', prompt: 'Say what you want' }], satisfied: [] },
    scope: { systems: 0, budget: 10, weighted: 0, verdict: 'lean', suggested_cut: [] },
    controls: [],
    counts: { selected_features: 0, rejected_features: 0, undecided_features: 0, systems: 0 },
  }
  return { ...base, ...overrides }
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  sessionStorage.clear()
})
afterEach(() => {
  // Vitest runs without `globals`, so React Testing Library never registers its
  // own cleanup and one test's DOM is still mounted during the next.
  cleanup()
  vi.unstubAllGlobals()
})

function reply(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status < 400, status, text: () => Promise.resolve(JSON.stringify(body)),
  } as Response)
}

function route(handlers: Record<string, unknown>) {
  fetchMock.mockImplementation((url: string) => {
    for (const [fragment, body] of Object.entries(handlers)) {
      if (url.includes(fragment)) {
        const value = body as { __status?: number }
        return reply(body, value?.__status ?? 200)
      }
    }
    return reply({ detail: `no stub for ${url}` }, 404)
  })
}

describe('feature cards', () => {
  it('shows selected and unselected unmistakably', () => {
    const { rerender } = render(
      <FeatureCard feature={feature('a')} onSelect={() => {}} />)
    expect(screen.getByTestId('feature-a').className).toContain('feature-undecided')

    rerender(<FeatureCard feature={feature('a', { selected: true })} onSelect={() => {}} />)
    expect(screen.getByTestId('feature-a').className).toContain('feature-selected')

    rerender(<FeatureCard feature={feature('a', { selected: false })} onSelect={() => {}} />)
    expect(screen.getByTestId('feature-a').className).toContain('feature-rejected')
  })

  it('can return a decided feature to undecided', async () => {
    const onSelect = vi.fn()
    render(<FeatureCard feature={feature('a', { selected: true })} onSelect={onSelect} />)

    await userEvent.click(screen.getByRole('button', { name: /selected/i }))

    // Not `false`: unpicking is not rejecting, and the two mean different
    // things to the specification.
    expect(onSelect).toHaveBeenCalledWith(null)
  })

  it('offers rejecting as its own action', async () => {
    const onSelect = vi.fn()
    render(<FeatureCard feature={feature('a')} onSelect={onSelect} />)

    await userEvent.click(screen.getByRole('button', { name: /not this/i }))

    expect(onSelect).toHaveBeenCalledWith(false)
  })

  it('marks a feature the person wrote', () => {
    render(<FeatureCard feature={feature('a', { origin: 'user' })} onSelect={() => {}} />)
    expect(screen.getByText('Yours')).toBeTruthy()
  })
})

describe('build summary', () => {
  it('shows the readiness the backend counted, not one of its own', () => {
    render(<BuildSummary view={view({
      readiness: { percent: 82, ready: false, missing: [{ key: 'persistence', prompt: 'Choose progression mode' }], satisfied: [] },
    })} />)

    expect(screen.getByText('82%')).toBeTruthy()
    expect(screen.getByText('Choose progression mode')).toBeTruthy()
  })

  it('says ready only when the backend says ready', () => {
    render(<BuildSummary view={view({
      readiness: { percent: 100, ready: true, missing: [], satisfied: [] },
    })} />)

    expect(screen.getByText(/ready to build/i)).toBeTruthy()
  })

  it('warns about scope with the counted numbers', () => {
    render(<BuildSummary view={view({
      scope: { systems: 17, budget: 10, weighted: 19.4, verdict: 'too_large', suggested_cut: ['Alpha', 'Beta'] },
    })} />)

    expect(screen.getByText(/17 systems against a budget of 10/)).toBeTruthy()
    expect(screen.getByText(/Alpha, Beta/)).toBeTruthy()
  })
})

describe('the workspace', () => {
  it('opens the blueprint for the idea it was given', async () => {
    route({ '/api/blueprints/proceed': view(), '/api/studio': { bridge: 'offline', plugin_connected: false, detail: 'not running' } })

    render(<BlueprintWorkspace auditId="audit-1" title="Zombie Quarantine Lab" onClose={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('blueprint-workspace')).toBeTruthy())
    // Twice on purpose: the header says what is being shaped, and the summary
    // panel says what is being built. Both are the same idea.
    expect(screen.getAllByText('Zombie Quarantine Lab')).toHaveLength(2)
    const proceedCall = fetchMock.mock.calls.find(call => String(call[0]).includes('proceed'))
    expect(JSON.parse(String((proceedCall?.[1] as RequestInit).body)).audit_id).toBe('audit-1')
  })

  it('keeps the original Scout idea reachable', async () => {
    route({ '/api/blueprints/proceed': view(), '/api/studio': { bridge: 'offline', plugin_connected: false } })

    render(<BlueprintWorkspace auditId="audit-1" title="Lab" onClose={() => {}}
      originalIdea={{ core_loop: 'scavenge then research', differentiator: 'mutating enemies' }} />)

    await waitFor(() => expect(screen.getByText('Original Scout idea')).toBeTruthy())
    expect(screen.getByText('scavenge then research')).toBeTruthy()
  })

  it('shows the backend sentence when a call fails, not a generic message', async () => {
    route({
      '/api/blueprints/proceed': { detail: 'Audit 1 produced no design (it was blocked)', __status: 400 },
      '/api/studio': { bridge: 'offline', plugin_connected: false },
    })
    fetchMock.mockImplementation((url: string) => url.includes('proceed')
      ? reply({ detail: 'Audit 1 produced no design (it was blocked)' }, 400)
      : reply({ bridge: 'offline', plugin_connected: false }))

    render(<BlueprintWorkspace auditId="audit-1" title="Lab" onClose={() => {}} />)

    await waitFor(() =>
      expect(screen.getByText(/produced no design/)).toBeTruthy())
  })

  it('will not let a build start while Studio is not connected', async () => {
    const ready = view({
      blueprint: { ...view().blueprint, user_intent: 'a bunker', status: 'ready_to_build' },
      readiness: { percent: 100, ready: true, missing: [], satisfied: [] },
    })
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('proceed')) return reply(ready)
      if (url.includes('/api/studio')) return reply({ bridge: 'offline', plugin_connected: false, detail: 'The local bridge is not running. Start it with: python -m app.bridge.run' })
      if (url.includes('specification')) return reply({
        specification: {}, plan: { building: [], skipping_because_they_exist: [] }, tasks: [],
        preview: {
          title: 'Lab', players: '1-4', world: 'single_arena', scope: 'vertical_slice',
          persistence: 'session_only', core_loop: '', systems_total: 0,
          server_systems: [], client_systems: [], shared_systems: [], build_order: [],
          included_features: [], excluded_features: [], asset_requirements: [],
          technical_constraints: [], acceptance_criteria_count: 0, estimated_complexity: 'low',
        },
      })
      return reply({ detail: 'unexpected' }, 404)
    })

    render(<BlueprintWorkspace auditId="audit-1" title="Lab" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('blueprint-workspace')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Review' }))

    await waitFor(() => expect(screen.getByRole('button', { name: /build in roblox studio/i })).toBeTruthy())
    const button = screen.getByRole('button', { name: /build in roblox studio/i }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })

  it('shows the bridge command when the bridge is not running', async () => {
    fetchMock.mockImplementation((url: string) => url.includes('proceed')
      ? reply(view())
      : reply({ bridge: 'offline', plugin_connected: false, detail: 'The local bridge is not running. Start it with: python -m app.bridge.run' }))

    render(<BlueprintWorkspace auditId="audit-1" title="Lab" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('blueprint-workspace')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Review' }))

    await waitFor(() => expect(screen.getByText('python -m app.bridge.run')).toBeTruthy())
  })

  it('reports Studio as connected with what it is looking at', async () => {
    fetchMock.mockImplementation((url: string) => url.includes('proceed')
      ? reply(view())
      : reply({
        bridge: 'online', plugin_connected: true,
        studio: { plugin_version: '0.2.0', studio_version: '0.739', place_name: 'Number 1', mode: 'edit' },
      }))

    render(<BlueprintWorkspace auditId="audit-1" title="Lab" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('blueprint-workspace')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Review' }))

    await waitFor(() => expect(screen.getByText('Connected')).toBeTruthy())
    const panel = screen.getByText('Roblox Studio').closest('section') as HTMLElement
    expect(within(panel).getByText('Number 1')).toBeTruthy()
  })

  it('says beside a disabled Build button what it is waiting for, and goes there', async () => {
    // It sat disabled with its reasons only in the side panel, and was taken
    // for broken: three features undecided and no systems worked out yet.
    const unfinished = view({
      blueprint: { ...view().blueprint, user_intent: 'a mine', suggestions: [feature('a')] },
      readiness: {
        percent: 71, ready: false, satisfied: [], missing: [
          { key: 'systems', prompt: 'Keep at least one system; there is nothing to build otherwise' },
          { key: 'features_decided', prompt: 'Decide on every suggested feature: a feature nobody chose is not a plan' },
        ],
      },
      counts: { selected_features: 0, rejected_features: 0, undecided_features: 1, systems: 0 },
    })
    fetchMock.mockImplementation((url: string) => url.includes('proceed')
      ? reply(unfinished)
      : reply({ bridge: 'online', plugin_connected: true,
        studio: { plugin_version: '0.4.1', studio_version: '0.739', place_name: 'Place1', mode: 'edit' } }))

    render(<BlueprintWorkspace auditId="audit-1" title="NeuroMine" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('blueprint-workspace')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Review' }))

    const blockers = await screen.findByTestId('build-blockers')
    expect(within(blockers).getByText(/Decide on every suggested feature/)).toBeTruthy()
    expect(within(blockers).getByText(/Keep at least one system/)).toBeTruthy()
    const button = screen.getByRole('button', { name: /build in roblox studio/i }) as HTMLButtonElement
    expect(button.disabled).toBe(true)

    await userEvent.click(within(blockers).getAllByRole('button', { name: 'Go to Features' })[0])
    await waitFor(() => expect(screen.getByText(/still undecided/)).toBeTruthy())
  })

  it('says nothing is missing once the backend says ready', async () => {
    const ready = view({
      blueprint: { ...view().blueprint, user_intent: 'a mine', status: 'ready_to_build' },
      readiness: { percent: 100, ready: true, missing: [], satisfied: [] },
    })
    fetchMock.mockImplementation((url: string) => url.includes('proceed')
      ? reply(ready) : reply({ bridge: 'offline', plugin_connected: false }))

    render(<BlueprintWorkspace auditId="audit-1" title="NeuroMine" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('blueprint-workspace')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Review' }))

    await waitFor(() => expect(screen.getByRole('button', { name: /build in roblox studio/i })).toBeTruthy())
    expect(screen.queryByTestId('build-blockers')).toBeNull()
  })

  it('blocks working out systems while a suggestion is undecided', async () => {
    const undecided = view({
      blueprint: { ...view().blueprint, suggestions: [feature('a')] },
      counts: { selected_features: 0, rejected_features: 0, undecided_features: 1, systems: 0 },
    })
    fetchMock.mockImplementation((url: string) => url.includes('proceed')
      ? reply(undecided) : reply({ bridge: 'offline', plugin_connected: false }))

    render(<BlueprintWorkspace auditId="audit-1" title="Lab" onClose={() => {}} />)

    await waitFor(() => expect(screen.getByText(/still undecided/)).toBeTruthy())
    const button = screen.getByRole('button', { name: /work out the systems/i }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })

  it('shows button to add missing systems when review fails with missing systems', async () => {
    const ready = view({
      blueprint: { ...view().blueprint, user_intent: 'a mine', status: 'ready_to_build' },
      readiness: { percent: 100, ready: true, missing: [], satisfied: [] },
    })
    const reconciled = view({
      blueprint: {
        ...view().blueprint,
        user_intent: 'a mine',
        status: 'ready_to_build',
        systems: [
          {
            id: 'sys_plot', name: 'PlotManagementService', layer: 'server',
            purpose: 'Manage starter plots', acceptance_criteria: ['Allocates plots without collisions.'],
            depends_on: [], complexity: 'medium', essential: true, from_feature: null,
          },
        ],
      },
      readiness: { percent: 100, ready: true, missing: [], satisfied: [] },
      missing_systems: [],
    })

    fetchMock.mockImplementation((url: string) => {
      if (url.includes('proceed')) return reply(ready)
      if (url.includes('/api/studio')) return reply({ bridge: 'offline', plugin_connected: false })
      if (url.includes('specification')) return reply({
        detail: "this plan could not produce the game it describes: 'Spawn' needs PlotManagementService, which is not in the plan",
      }, 409)
      if (url.includes('systems/reconcile')) return reply(reconciled)
      return reply({ detail: 'unexpected' }, 404)
    })

    render(<BlueprintWorkspace auditId="audit-1" title="NeuroMine" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('blueprint-workspace')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Systems' }))

    // Click Review the build which will fail with missing systems error
    await userEvent.click(screen.getByRole('button', { name: /review the build/i }))

    await waitFor(() => expect(screen.getByText(/which is not in the plan/)).toBeTruthy())
    const addBtn = screen.getByRole('button', { name: /add missing systems to plan/i })
    expect(addBtn).toBeTruthy()

    // Click to reconcile
    await userEvent.click(addBtn)
    const reconcileCall = fetchMock.mock.calls.find(call => String(call[0]).includes('systems/reconcile'))
    expect(reconcileCall).toBeTruthy()
  })

  it('shows missing systems banner in the systems step when missing systems exist', async () => {
    const withMissing = view({
      blueprint: { ...view().blueprint, user_intent: 'a mine', status: 'ready_to_build' },
      readiness: { percent: 100, ready: true, missing: [], satisfied: [] },
      missing_systems: [{ name: 'PlotManagementService', needed_for: 'Spawn into starter claim' }],
    })
    fetchMock.mockImplementation((url: string) => url.includes('proceed')
      ? reply(withMissing) : reply({ bridge: 'offline', plugin_connected: false }))

    render(<BlueprintWorkspace auditId="audit-1" title="NeuroMine" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('blueprint-workspace')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Systems' }))

    await waitFor(() =>
      expect(screen.getByText(/1 required system is missing from the plan/)).toBeTruthy())
    expect(screen.getByRole('button', { name: /add missing systems to plan/i })).toBeTruthy()
  })
})
