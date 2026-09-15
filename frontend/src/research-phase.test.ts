import { describe, expect, it } from 'vitest'
import { RESEARCH_PHASES, researchPhase } from './routing'

describe('researchPhase', () => {
  it('follows the stages the backend actually reports', () => {
    expect(researchPhase('', 'queued')).toBe(0)
    expect(researchPhase('Investigating round 1', 'running')).toBe(1)
    expect(researchPhase('Investigating round 4', 'running')).toBe(1)
    expect(researchPhase('Scout follow-up: checking remaining evidence gaps', 'running')).toBe(1)
    expect(researchPhase('Comparing evidence and drafting research concepts', 'running')).toBe(2)
    expect(researchPhase('Venture Scout: auditing selected proposal', 'running')).toBe(3)
  })

  it('treats every terminal status as finished', () => {
    for (const status of ['complete', 'partial', 'interrupted', 'failed']) {
      expect(researchPhase('anything', status)).toBe(RESEARCH_PHASES.length - 1)
    }
  })

  it('refuses to guess an unrecognised stage', () => {
    // Forcing an unknown stage into a phase is what the old list did by
    // highlighting nothing at all; inventing one would be worse.
    expect(researchPhase('Doing something new', 'running')).toBe(-1)
  })
})
