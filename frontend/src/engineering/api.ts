// The Engineering Agent's side of the wire.
//
// Everything here maps to an endpoint that exists. The graph in particular is
// computed by the backend from the specification and the build record -- the
// frontend does not decide what state a system is in, because a UI that works
// that out for itself can disagree with the build and nobody would see it.

export type NodeState = 'built' | 'building' | 'waiting' | 'refused' | 'error'

export interface GraphNode {
  id: string
  name: string
  layer: 'server' | 'client' | 'shared'
  path: string
  purpose: string
  acceptance_criteria: string[]
  depends_on: string[]
  state: NodeState
  detail: string
  branch: string | null
  order: number
}

export interface BuildGraph {
  build_id: string
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
  counts: Record<string, number>
  events: Array<{ at: string; stage: string; detail: string }>
}

export interface BuildSummaryRow {
  id: string
  title: string
  status: string
  spec_revision: number
  content_hash: string
  created_at: string
  completed_at: string | null
}

async function call<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' } })
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
  graph: (buildId: string) => call<BuildGraph>(`/api/builds/${buildId}/graph`),
  studio: (token: string) =>
    call<{ bridge: string; plugin_connected: boolean; detail?: string; studio?: Record<string, string> | null }>(
      `/api/studio${token ? `?token=${encodeURIComponent(token)}` : ''}`),
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
