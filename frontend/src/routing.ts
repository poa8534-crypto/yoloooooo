export type PageId = 'home' | 'ideas' | 'sources' | 'history' | 'matching' | 'meta' | 'scout' | 'calibration' | 'health'
const pages = new Set(['home', 'ideas', 'sources', 'history', 'matching', 'meta', 'scout', 'calibration', 'health'])
export type Route = { page: PageId; candidate: string; proposal: string; audit: string; run: string }
export function readRoute(hash = window.location.hash): Route {
  const [path, search = ''] = hash.replace(/^#\/?/, '').split('?')
  const params = new URLSearchParams(search)
  return { page: pages.has(path) ? path as PageId : 'home', candidate: params.get('candidate') || '', proposal: params.get('proposal') || '', audit: params.get('audit') || '', run: params.get('run') || '' }
}
export function routeHash(page: PageId, params: Partial<Omit<Route, 'page'>> = {}): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value) query.set(key, value) })
  return `#/${page}${query.size ? '?' + query : ''}`
}
export function matchesDecision(decision: string, filter: string): boolean {
  return filter === 'all' || (filter === 'research_more' ? ['collection_only', 'research_more'].includes(decision) : decision === filter)
}
