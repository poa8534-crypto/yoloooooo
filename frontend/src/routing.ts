export type PageId = 'home' | 'ideas' | 'sources' | 'history' | 'market' | 'matching' | 'meta' | 'scout' | 'engineering' | 'calibration' | 'health'
const pages = new Set(['home', 'ideas', 'sources', 'history', 'market', 'matching', 'meta', 'scout', 'engineering', 'calibration', 'health'])
export type Route = { page: PageId; candidate: string; proposal: string; audit: string; run: string; build: string }
export function readRoute(hash = window.location.hash): Route {
  const [path, search = ''] = hash.replace(/^#\/?/, '').split('?')
  const params = new URLSearchParams(search)
  return { page: pages.has(path) ? path as PageId : 'home', candidate: params.get('candidate') || '', proposal: params.get('proposal') || '', audit: params.get('audit') || '', run: params.get('run') || '', build: params.get('build') || '' }
}
export function routeHash(page: PageId, params: Partial<Omit<Route, 'page'>> = {}): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value) query.set(key, value) })
  return `#/${page}${query.size ? '?' + query : ''}`
}
export function matchesDecision(decision: string, filter: string): boolean {
  return filter === 'all' || (filter === 'research_more' ? ['collection_only', 'research_more'].includes(decision) : decision === filter)
}

// The pipeline list used to be eight invented documentation steps with nothing
// highlighted, because the backend only reported one message per run and
// guessing which step was live would have been a fabrication. It now reports a
// real stage string, so these are the phases it actually moves through, and an
// unrecognised stage returns -1 rather than being forced into one of them.
export const RESEARCH_PHASES = [
  { id: 'queued', label: 'Queued', detail: 'Waiting to start' },
  { id: 'investigating', label: 'Investigating', detail: 'Discovering, capturing and answering open questions' },
  { id: 'concepts', label: 'Drafting concepts', detail: 'Comparing evidence and writing research concepts' },
  { id: 'handoff', label: 'Handed to Scout', detail: 'Concepts waiting in the audit queue for your decision' },
  { id: 'finished', label: 'Finished', detail: 'Report written to the ledger' },
] as const

const FINISHED = new Set(['complete', 'partial', 'interrupted', 'failed'])

export function researchPhase(stage: string, status: string): number {
  if (FINISHED.has(status)) return RESEARCH_PHASES.length - 1
  const text = (stage || '').trim().toLowerCase()
  if (text.startsWith('investigating') || text.startsWith('scout follow-up')) return 1
  if (text.startsWith('comparing evidence')) return 2
  if (text.startsWith('concept drafted')) return 3
  // Runs recorded before the handoff split still carry this stage; the phase
  // strip has to keep rendering their history rather than showing "unknown".
  if (text.startsWith('venture scout')) return 3
  if (status === 'queued' || text === '' || text === 'queued') return 0
  return -1
}
