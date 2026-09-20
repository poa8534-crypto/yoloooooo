// The Engineering Agent's side of the wire.
//
// Everything here maps to an endpoint that exists. The graph in particular is
// computed by the backend from the specification and the build record -- the
// frontend does not decide what state a system is in, because a UI that works
// that out for itself can disagree with the build and nobody would see it.

// 'existing' is not a kind of waiting: the project already had that system,
// so this build is never going to write it and it will never become built.
export type NodeState =
  'built' | 'building' | 'waiting' | 'existing' | 'refused' | 'error'

export interface GraphNode {
  id: string
  name: string
  layer: 'server' | 'client' | 'shared'
  path: string
  purpose: string
  acceptance_criteria: string[]
  depends_on: string[]
  state: NodeState
  // Whether the game repository holds this system's file right now.
  in_project: boolean
  detail: string
  branch: string | null
  commit: string
  attempts: number
  studio_path: string
  studio_class: string
  order: number
  priority_class: string
  core_loop_blocker: boolean
  required_for_vertical_slice: boolean
  player_flow_index: number
  builds_world: boolean
  dependents: string[]
}

// Measurements, not predictions. There is no "time remaining" field because
// the backend does not compute one: the systems still to be written are not
// the ones that were measured, so extrapolating from them would be a guess
// presented in the same typeface as a fact.
export interface Pace {
  started_at: string
  completed_at: string
  systems_measured: number
  average_system_seconds: number | null
  slowest_system_seconds: number | null
  per_system_seconds: Record<string, number>
  attempts_spent: number
  systems_accepted: number
  // The measured systems' own time added up. Beside the elapsed time it says
  // what writing systems at once saved. Absent from builds read before it.
  system_seconds_total?: number
}

// null means Studio was never asked, which is not the same as Studio applying
// nothing. The UI has to be able to say which.
export interface SyncState {
  batch_id: string
  operations: number
  sent_at: string
  applied: number | null
  skipped: number | null
  failed: number | null
  reported_at: string
}

// A steering directive, and what the backend says it can reach.
//
// `reachable` never includes the system in flight: its prompt was built before
// the directive existed. `carried_into` is what makes "applied" checkable --
// the systems the instruction actually went into.
export interface Directive {
  id: string
  text: string
  system: string
  created_at: string
  status: 'pending' | 'carried' | 'stale'
  carried_into: string[]
  reachable_when_written: string[]
}

export interface Steering {
  directives: Directive[]
  reachable: string[]
  accepting: boolean
  max_active: number
}

// The three planning layers the doctrine keeps apart: what the player
// experiences, what they do, and what the build is doing about it.
export interface PlayerJourney {
  entry_state: string
  spawn_context: string
  immediate_visuals: string[]
  first_affordance: string
  first_action: string
  first_feedback: string
  first_reward: string
  reward_destination: string
  next_decision: string
  core_loop: string[]
  progression_loop: string[]
  failure_state: string
  recovery_path: string
  session_end: string
  return_state: string
}

export interface PathNode {
  id: string
  label: string
  description: string
  systems: string[]
  data: string[]
  ui: string[]
  acceptance_criteria: string[]
  gate: string | null
}

export type TaskState = 'ready' | 'blocked' | 'building' | 'testing' | 'done' | 'failed'

export interface SystemState {
  name: string
  state: TaskState
  waiting_for: string[]
}

export interface PlayabilityGate {
  gate: string
  passed: boolean
  needs: string[]
  detail: string
}

export interface Plan {
  journey: PlayerJourney | null
  path: { nodes: PathNode[] } | null
  states: SystemState[]
  gates: PlayabilityGate[]
  // Composed by the backend from the plan. A reason invented in the browser is
  // a reason nobody checked.
  why_now: Record<string, string>
  next_up: string[]
  blocked_by_failure: string[]
}

// What the models were asked to do, and what is left of a limit the person
// configured. `limit` is null when none was set: no provider reports a
// remaining quota, so the strip says "no limit set" rather than drawing a bar
// against a number the backend invented. `measured` is false when nothing in
// range was recorded with hour buckets -- a missing answer, not a zero.
export interface UsageWindow {
  calls: number
  tokens: number
  models: Record<string, number>
  rate_limited: number
  measured: boolean
  limit: number | null
  remaining: number | null
  percent_used: number | null
}

export interface ModelUsage {
  generated_at: string
  hour: UsageWindow
  week: UsageWindow
  limits_configured: boolean
}

export interface ExplorerRow {
  name: string
  class: string
  system: string
  state: NodeState | ''
  children: ExplorerRow[]
}

export interface BuildGraph {
  build_id: string
  blueprint_id?: string
  status: string
  title: string
  spec_revision: number
  content_hash: string
  goal: {
    title: string
    intent: string
    included_features: string[]
    excluded_features: string[]
    constraints: string[]
    acceptance_criteria_total: number
  }
  nodes: GraphNode[]
  edges: Array<{ from: string; to: string }>
  current: string | null
  // Every system the Engineer has been asked for and not answered, in the
  // order it was asked. Read it through inFlight().
  in_flight?: string[]
  counts: Record<string, number>
  pace: Pace
  sync: SyncState
  plan: Plan
  steering: Steering
  explorer: ExplorerRow[]
  events: Array<{ at: string; stage: string; detail: string }>
}

// One row of build history. The counts are the backend's, read off the build
// record: a number worked out in the browser as well can disagree with the one
// on the build, and only one of them is looking at what happened.
export interface BuildSummaryRow {
  id: string
  blueprint_id: string
  title: string
  status: string
  spec_revision: number
  content_hash: string
  created_at: string
  completed_at: string | null
  systems_attempted: number
  systems_built: number
  systems_refused: number
  attempts_spent: number
  duration_seconds: number | null
}

export interface StudioStatus {
  bridge: string
  plugin_connected: boolean
  token?: string
  detail?: string
  latency_ms?: number
  bridge_version?: string
  protocol_version?: number
  queued_batches?: number
  runtime_errors?: number
  studio?: {
    plugin_version?: string
    studio_version?: string
    place_name?: string
    place_id?: number
    mode?: string
  } | null
}

// What the machine that runs builds can do. Read from that machine, which is
// usually not the one showing this page.
export interface ToolReport {
  name: string
  present: boolean
  path: string
  version: string
  detail: string
  purpose: string
  required: boolean
}

export interface Toolchain {
  ready: boolean
  missing: string[]
  tools: ToolReport[]
}

export interface SystemSource {
  system: string
  state: string
  source: string
  path: string
  commit: string
  branch: string
  detail: string
  lines: number
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  const text = await response.text()
  let body: unknown = null
  try { body = text ? JSON.parse(text) : null } catch { body = text }
  if (!response.ok) {
    const detail = (body as { detail?: unknown })?.detail
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`)
  }
  return body as T
}

export const engineeringApi = {
  builds: () => call<{ builds: BuildSummaryRow[] }>('/api/builds'),
  toolchain: () => call<Toolchain>('/api/engineer/toolchain'),
  usage: () => call<ModelUsage>('/api/builds/usage/models'),
  graph: (buildId: string) => call<BuildGraph>(`/api/builds/${buildId}/graph`),
  studio: (token: string) =>
    call<StudioStatus>(`/api/studio${token ? `?token=${encodeURIComponent(token)}` : ''}`),
  source: (buildId: string, system: string) =>
    call<SystemSource>(`/api/builds/${buildId}/systems/${encodeURIComponent(system)}/source`),
  // A real operation over the real bridge: it fails when Studio is not
  // connected rather than reporting that it opened something.
  open: (buildId: string, system: string, token: string) =>
    call<{ opened: string; batch_id: string }>(
      `/api/builds/${buildId}/systems/${encodeURIComponent(system)}/open`,
      { method: 'POST', body: JSON.stringify({ token }) }),
  steer: (buildId: string, text: string, system: string) =>
    call<Steering>(`/api/builds/${buildId}/directives`,
      { method: 'POST', body: JSON.stringify({ text, system }) }),
  unsteer: (buildId: string, directiveId: string) =>
    call<Steering>(`/api/builds/${buildId}/directives/${directiveId}`, { method: 'DELETE' }),
  startBuild: (blueprintId: string, token: string, play: boolean = false) =>
    call<{ build_id: string; blueprint_id: string; follow: string }>(
      '/api/builds',
      { method: 'POST', body: JSON.stringify({ blueprint_id: blueprintId, token, play }) }),
}

// Where each node sits, worked out once from the dependency graph rather than
// by a physics simulation: a layout that moves while you are reading it is
// harder to follow, and build order already gives a meaningful left-to-right.
export interface Placed extends GraphNode {
  column: number
  row: number
}

export function layout(nodes: GraphNode[]): Placed[] {
  const byName = new Map(nodes.map(node => [node.id, node]))
  const depth = new Map<string, number>()

  function depthOf(id: string, seen: Set<string> = new Set()): number {
    if (depth.has(id)) return depth.get(id) as number
    if (seen.has(id)) return 0 // a cycle cannot be laid out; the spec refuses one anyway
    seen.add(id)
    const node = byName.get(id)
    const parents = (node?.depends_on ?? []).filter(name => byName.has(name))
    const value = parents.length === 0
      ? 0
      : 1 + Math.max(...parents.map(name => depthOf(name, seen)))
    depth.set(id, value)
    return value
  }

  const rows = new Map<number, number>()
  return nodes.map(node => {
    const column = depthOf(node.id)
    const row = rows.get(column) ?? 0
    rows.set(column, row + 1)
    return { ...node, column, row }
  })
}

export interface Band {
  layer: string
  from: number
  to: number
  count: number
}

// The same columns as `layout`, with rows grouped into one band per layer.
// Columns still come from the whole graph, so a client system that needs a
// server one still sits to its right; only the row changes.
const LAYER_ORDER = ['server', 'shared', 'client']

export function clusters(nodes: GraphNode[]): { placed: Placed[]; bands: Band[] } {
  const columnOf = new Map(layout(nodes).map(node => [node.id, node.column]))
  const present = LAYER_ORDER.filter(layer => nodes.some(node => node.layer === layer))
  const placed: Placed[] = []
  const bands: Band[] = []
  let row = 0

  for (const layer of present) {
    const members = nodes.filter(node => node.layer === layer)
    const perColumn = new Map<number, number>()
    let tallest = 0
    for (const node of members) {
      const column = columnOf.get(node.id) ?? 0
      const offset = perColumn.get(column) ?? 0
      perColumn.set(column, offset + 1)
      tallest = Math.max(tallest, offset + 1)
      placed.push({ ...node, column, row: row + offset })
    }
    bands.push({ layer, from: row, to: row + Math.max(tallest, 1) - 1, count: members.length })
    row += Math.max(tallest, 1)
  }

  return { placed, bands }
}

// A graph from an older service, or a response that lost a field on the way,
// must not white-screen the page: an Engineering Agent that cannot render is
// worse than one that renders with a blank where a measurement would be.
export const NO_PACE: Pace = {
  started_at: '', completed_at: '', systems_measured: 0, average_system_seconds: null,
  slowest_system_seconds: null, per_system_seconds: {}, attempts_spent: 0, systems_accepted: 0,
}
export const NOT_SENT: SyncState = {
  batch_id: '', operations: 0, sent_at: '',
  applied: null, skipped: null, failed: null, reported_at: '',
}
export const NO_PLAN: Plan = {
  journey: null, path: null, states: [], gates: [], why_now: {},
  next_up: [], blocked_by_failure: [],
}
export const NO_STEERING: Steering = {
  directives: [], reachable: [], accepting: false, max_active: 0,
}

// Every system being written right now -- several when systems that do not
// depend on each other are written at once. A backend from before the list
// names at most one, in `current`, and is read the same way rather than as
// nothing in flight.
export function inFlight(graph: BuildGraph | null): string[] {
  if (!graph) return []
  return graph.in_flight ?? (graph.current ? [graph.current] : [])
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${String(Math.round(seconds % 60)).padStart(2, '0')}s`
}

export function elapsedSince(iso: string): number | null {
  const started = Date.parse(iso)
  return Number.isNaN(started) ? null : (Date.now() - started) / 1000
}
