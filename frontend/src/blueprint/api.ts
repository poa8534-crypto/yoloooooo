// The Blueprint stage's side of the wire.
//
// One module so no component invents its own fetch. Every call here maps to an
// endpoint that exists (app/blueprint/api.py); nothing is stubbed, and a call
// that fails surfaces the backend's own message rather than "something went
// wrong", because the backend's messages say what to do about it.

export type Complexity = 'low' | 'medium' | 'high'
export type Priority = 'essential' | 'high' | 'medium' | 'low'
export type Layer = 'server' | 'client' | 'shared'

export interface FeatureSuggestion {
  id: string
  title: string
  description: string
  reason: string
  player_value: string
  implementation_complexity: Complexity
  mvp_priority: Priority
  retention_impact: Complexity
  risk: string
  dependencies: string[]
  required_systems: string[]
  selected: boolean | null
  origin: 'architect' | 'user'
}

export interface GameSystem {
  id: string
  name: string
  layer: Layer
  purpose: string
  acceptance_criteria: string[]
  depends_on: string[]
  complexity: Complexity
  essential: boolean
  from_feature: string | null
}

export interface BlueprintConfig {
  scope: 'quick_prototype' | 'vertical_slice' | 'expanded_prototype'
  min_players: number
  max_players: number
  platforms: Array<'desktop' | 'mobile' | 'console'>
  persistence: 'session_only' | 'saved_progression'
  world: 'single_arena' | 'small_world' | 'multiple_zones' | 'procedural'
  build_target: 'clean_prototype' | 'current_place'
  tone: string
  emphasis: Record<string, number>
}

export interface Blueprint {
  id: string
  project_id: string
  audit_id: string
  title: string
  revision: number
  user_intent: string
  summary: string
  suggestions: FeatureSuggestion[]
  systems: GameSystem[]
  config: BlueprintConfig
  asset_requirements: string[]
  status: string
}

export interface Readiness {
  percent: number
  ready: boolean
  missing: Array<{ key: string; prompt: string }>
  satisfied: string[]
}

export interface ScopeVerdict {
  systems: number
  budget: number
  weighted: number
  verdict: 'lean' | 'balanced' | 'too_large'
  suggested_cut: string[]
}

export interface BlueprintView {
  blueprint: Blueprint
  readiness: Readiness
  scope: ScopeVerdict
  controls: string[]
  counts: {
    selected_features: number
    rejected_features: number
    undecided_features: number
    systems: number
  }
  missing_systems?: Array<{ name: string; needed_for: string }>
  resumed?: boolean
}

export interface StudioState {
  bridge: 'offline' | 'online' | 'unknown'
  plugin_connected: boolean
  detail?: string
  needs_token?: boolean
  token?: string
  studio?: { plugin_version: string; studio_version: string; place_name: string; mode: string } | null
  runtime_errors?: number
}

export interface SpecificationView {
  specification: Record<string, unknown>
  preview: {
    title: string
    players: string
    world: string
    scope: string
    persistence: string
    core_loop: string
    systems_total: number
    server_systems: string[]
    client_systems: string[]
    shared_systems: string[]
    build_order: string[]
    included_features: string[]
    excluded_features: string[]
    asset_requirements: string[]
    technical_constraints: string[]
    acceptance_criteria_count: number
    estimated_complexity: string
  }
  plan: { building: string[]; skipping_because_they_exist: string[] }
  tasks: Array<{ system: string; criteria: number }>
}

export interface BuildStarted {
  build_id: string
  blueprint_id: string
  follow: string
}

export interface BuildRecord {
  id: string
  title: string
  status: string
  spec_revision: number
  content_hash: string
  batch_id: string | null
  operations: number
  systems: Record<string, { status: string; detail?: string; reason?: string; branch?: string }>
  events: Array<{ at: string; stage: string; detail: string }>
  completed_at: string | null
}

export interface BuildResult {
  status: 'pending' | 'applied' | 'failed'
  detail?: string
  results?: Array<{ operation_id: string; status: string; detail: string; unchanged: boolean }>
  place_name?: string
  applied?: number
  skipped?: number
  failed_count?: number
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function call<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options })
  const text = await response.text()
  let body: unknown = null
  try { body = text ? JSON.parse(text) : null } catch { body = text }
  if (!response.ok) {
    // FastAPI puts the useful sentence in `detail`, and those sentences say
    // what to do. Losing them to a generic message is losing the instruction.
    const detail = (body as { detail?: unknown })?.detail
    const message = typeof detail === 'string' ? detail
      : detail ? JSON.stringify(detail)
        : typeof body === 'string' && body ? body
          : `Request failed (${response.status})`
    throw new ApiError(message, response.status)
  }
  return body as T
}

export const blueprintApi = {
  proceed: (auditId: string, title?: string) =>
    call<BlueprintView>('/api/blueprints/proceed', {
      method: 'POST', body: JSON.stringify({ audit_id: auditId, title: title ?? '' }),
    }),

  read: (id: string) => call<BlueprintView>(`/api/blueprints/${id}`),

  setIntent: (id: string, intent: string, config?: BlueprintConfig) =>
    call<BlueprintView>(`/api/blueprints/${id}/intent`, {
      method: 'POST', body: JSON.stringify({ intent, config: config ?? null }),
    }),

  suggest: (id: string, replace = false) =>
    call<BlueprintView>(`/api/blueprints/${id}/suggestions?replace=${replace}`, { method: 'POST' }),

  select: (id: string, selections: Record<string, boolean | null>) =>
    call<BlueprintView>(`/api/blueprints/${id}/selections`, {
      method: 'POST', body: JSON.stringify({ selections }),
    }),

  addFeature: (id: string, title: string, description: string) =>
    call<BlueprintView>(`/api/blueprints/${id}/features`, {
      method: 'POST', body: JSON.stringify({ title, description }),
    }),

  editFeature: (id: string, featureId: string, changes: { title?: string; description?: string }) =>
    call<BlueprintView>(`/api/blueprints/${id}/features/${featureId}`, {
      method: 'PATCH', body: JSON.stringify(changes),
    }),

  setConfig: (id: string, config: BlueprintConfig) =>
    call<BlueprintView>(`/api/blueprints/${id}/config`, {
      method: 'POST', body: JSON.stringify(config),
    }),

  systems: (id: string) =>
    call<BlueprintView>(`/api/blueprints/${id}/systems`, { method: 'POST' }),

  reconcileSystems: (id: string) =>
    call<BlueprintView>(`/api/blueprints/${id}/systems/reconcile`, { method: 'POST' }),

  toggleSystems: (id: string, enabled: Record<string, boolean>) =>
    call<BlueprintView>(`/api/blueprints/${id}/systems/toggle`, {
      method: 'POST', body: JSON.stringify({ enabled }),
    }),

  specification: (id: string) => call<SpecificationView>(`/api/blueprints/${id}/specification`),

  studio: (token: string) =>
    call<StudioState>(`/api/studio${token ? `?token=${encodeURIComponent(token)}` : ''}`),

  build: (blueprintId: string, token: string, play: boolean) =>
    call<BuildStarted>('/api/builds', {
      method: 'POST', body: JSON.stringify({ blueprint_id: blueprintId, token, play }),
    }),

  buildRecord: (buildId: string) => call<BuildRecord>(`/api/builds/${buildId}`),

  buildResult: (batchId: string, token: string) =>
    call<BuildResult>(`/api/builds/batches/${batchId}?token=${encodeURIComponent(token)}`),
}
