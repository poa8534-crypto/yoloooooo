import { describe, expect, it } from 'vitest'
import { matchesDecision, readRoute, routeHash } from './routing'

describe('bookmarkable routes and engine filters', () => {
  it('round trips exact entity and proposal/audit versions', () => {
    const params = { candidate: 'game-a', proposal: 'proposal-b', audit: 'audit-c', run: 'run-d' }
    expect(readRoute(routeHash('ideas', params))).toEqual({ page: 'ideas', ...params })
  })
  it('fails unknown pages safely', () => { expect(readRoute('#/unknown').page).toBe('home') })
  it('includes both collection and research states without recommendations', () => {
    expect(matchesDecision('collection_only', 'research_more')).toBe(true)
    expect(matchesDecision('research_more', 'research_more')).toBe(true)
    expect(matchesDecision('recommend', 'research_more')).toBe(false)
    expect(matchesDecision('blocked_conflict', 'blocked_conflict')).toBe(true)
  })
})
