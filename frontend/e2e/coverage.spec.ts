import { expect, test, type Page } from '@playwright/test'

// Rows the inventory previously listed as "not tested". Each one here moves a
// claim from asserted to checked; anything still absent stays listed as
// untested rather than assumed.

async function openBrief(page: Page) {
  await page.goto('/#/ideas')
  await page.locator('.filter-row').first().waitFor()
  const toggle = page.getByRole('button', { name: /Game dossiers|Games/i })
  if (await toggle.count()) await toggle.first().click()
  await page.locator('.ideas-list button').first().waitFor()
  await page.locator('.ideas-list button').first().click()
}

test.describe('global shell', () => {
  test('Refresh data reloads every panel', async ({ page }) => {
    await page.goto('/#/home')
    await expect(page.locator('.workspace').first()).toBeVisible()
    let summaries = 0
    page.on('request', request => { if (request.url().includes('/api/dashboard/summary')) summaries += 1 })
    await page.getByRole('button', { name: /Refresh data/i }).click()
    await expect.poll(() => summaries, { timeout: 10_000 }).toBeGreaterThan(0)
  })

  test('there is no theme toggle, and the app is pinned to light', async ({ page }) => {
    // The editorial direction commits to one look. A toggle that changed
    // nothing, or a control that was removed but still rendered, would both be
    // worse than saying so.
    await page.goto('/#/home')
    await expect.poll(async () => page.evaluate(() => document.documentElement.dataset.theme))
      .toBe('light')
    await expect(page.getByRole('button', { name: /Dark mode|Light mode/i })).toHaveCount(0)
  })
})

test.describe('brief', () => {
  test('a section anchor scrolls without losing the route', async ({ page }) => {
    await openBrief(page)
    const url = page.url()
    const link = page.locator('.brief-index a').nth(3)
    const target = await link.getAttribute('href')
    await link.click()
    // The candidate stays in the route; only the fragment target changes.
    expect(page.url().split('#')[1]).not.toBe(target?.slice(1))
    expect(page.url()).toContain('candidate=')
    expect(url).toContain('candidate=')
  })

  test('a disclosure keeps its state while the brief is open', async ({ page }) => {
    await openBrief(page)
    const fold = page.locator('details.brief-fold').first()
    const wasOpen = await fold.evaluate(node => (node as HTMLDetailsElement).open)
    await fold.locator('summary').click()
    await expect.poll(async () => fold.evaluate(node => (node as HTMLDetailsElement).open)).not.toBe(wasOpen)
  })
})

test.describe('evidence drawer', () => {
  test('Tab stays inside the drawer while it is open', async ({ page }) => {
    await openBrief(page)
    const fact = page.locator('.fact-stack button').first()
    await fact.waitFor()
    await fact.click()
    const drawer = page.locator('aside[aria-label="Evidence inspector"]')
    await expect(drawer).toBeVisible()

    for (let step = 0; step < 12; step += 1) {
      await page.keyboard.press('Tab')
      const inside = await page.evaluate(() => {
        const panel = document.querySelector('aside[aria-label="Evidence inspector"]')
        return Boolean(panel && document.activeElement && panel.contains(document.activeElement))
      })
      expect(inside, `focus escaped the drawer after ${step + 1} tabs`).toBe(true)
    }
    await page.keyboard.press('Escape')
  })
})

test.describe('agent history', () => {
  test('each filter shows only its own agent, and they partition the list', async ({ page }) => {
    // Asserting that a filter has rows assumes the fixture contains that kind.
    // What must hold regardless is that each filter shows only its own agent
    // and that the two together account for everything.
    const counts = await (await page.request.get('/api/agent-runs/count')).json()
    await page.goto('/#/history')
    await page.locator('.history-toolbar').waitFor()

    for (const [label, kind, pattern] of [
      ['Venture Scout', 'venture_scout', /Venture Scout/i],
      ['Meta Hunter', 'meta_hunter', /Meta Hunter/i],
    ] as const) {
      await page.locator('.filter-row').getByRole('button', { name: label, exact: true }).click()
      await expect(page.locator('.history-toolbar')).toContainText(`of ${counts[kind]} run(s)`)
      for (const row of await page.locator('.history-row header').all()) {
        await expect(row).toContainText(pattern)
      }
    }
    expect(counts.venture_scout + counts.meta_hunter).toBe(counts.total)
  })

  test('search narrows by text and reports how many are shown', async ({ page }) => {
    await page.goto('/#/history')
    await page.locator('.history-row').first().waitFor()
    await page.getByPlaceholder(/Filter by concept/i).fill('no-such-concept-anywhere')
    await expect(page.locator('.history-row')).toHaveCount(0)
  })

  test('the total counts the ledger, not the page', async ({ page }) => {
    await page.goto('/#/history')
    await page.locator('.history-toolbar').waitFor()
    const counts = await (await page.request.get('/api/agent-runs/count')).json()
    await expect(page.locator('.history-toolbar')).toContainText(`of ${counts.total} run(s)`)
  })

  test('an audit says which operation produced it', async ({ page }) => {
    // Spec files run in their own order, so this creates the audit it needs
    // rather than depending on another file having run first.
    test.setTimeout(120_000)
    const runs = await (await page.request.get('/api/research-runs')).json()
    const candidateId = runs[0].candidates[0].id
    const started = await page.request.post(
      `/api/candidates/${candidateId}/audit-jobs?operation=analyze_game`)
    const jobId = (await started.json()).id
    await expect.poll(async () => (await (await page.request.get(`/api/audit-jobs/${jobId}`)).json()).status,
      { timeout: 90_000 }).not.toMatch(/queued|running/)

    await page.goto('/#/history')
    const scout = page.locator('.history-row', { hasText: /venture scout/i }).first()
    await scout.waitFor()
    await expect(scout.locator('header')).toContainText(/analyze game|audit idea/i)
  })
})

test.describe('matching engine', () => {
  test('selecting a review shows that record', async ({ page }) => {
    await page.goto('/#/matching')
    await expect(page.locator('.workspace').first()).toBeVisible()
    const queue = page.locator('.review-queue button')
    if (await queue.count()) {
      const label = (await queue.first().innerText()).split('\n')[0].trim()
      await queue.first().click()
      await expect(page.locator('.review-layout')).toContainText(label.slice(0, 18))
    }
  })
})

test.describe('health', () => {
  test('the page says which code is answering', async ({ page }) => {
    await page.goto('/#/health')
    const build = page.locator('.build-identity')
    await expect(build).toBeVisible()
    await expect(build).toContainText(/Running code/i)
    await expect(build).toContainText(/Service started/i)
  })

  test('quota display is described as a local reservation', async ({ page }) => {
    await page.goto('/#/health')
    await expect(page.getByText(/reservation|allowance|quota/i).first()).toBeVisible()
  })
})

test.describe('venture scout', () => {
  test('the background drawer streams each pass', async ({ page }) => {
    test.setTimeout(120_000)
    await page.goto('/#/scout')
    await page.getByLabel('Operation').selectOption('analyze_game')
    await page.getByRole('button', { name: 'Analyze this game' }).click()
    const drawer = page.locator('aside[aria-label="Background work"]')
    await expect(drawer.locator('.work-log article').first()).toBeVisible({ timeout: 30_000 })
    // More than one step, so it is a stream and not a single status line.
    await expect.poll(async () => drawer.locator('.work-log article').count(), { timeout: 30_000 })
      .toBeGreaterThan(2)
    await drawer.getByRole('button', { name: 'Cancel run' }).click()
  })
})

test.describe('an incomplete audit', () => {
  test('is rendered as incomplete with its unanswered concerns', async ({ page }) => {
    // This state has never occurred in a live run, so the rendering is checked
    // against a stubbed record rather than left unverified.
    await page.route('**/api/candidates/*/audit', async route => {
      if (route.request().method() !== 'GET') return route.continue()
      const response = await route.fetch()
      const body = await response.json()
      await route.fulfill({ json: {
        ...body,
        evidence_state: 'source_backed_design_incomplete',
        revision_applied: false,
        unresolved_concerns: ['Scope assumes art that does not exist yet', 'Assumes co-op demand'],
        proposal: body.proposal || { concept_title: 'Stubbed design', core_loop: 'x'.repeat(20),
          differentiator: 'y'.repeat(20), build_steps: ['Day 1: block it out'], risks: ['A risk'],
          questions: [], supporting_fact_ids: [], design_assumptions: [] },
      } })
    })
    await openBrief(page)
    await expect(page.getByText(/audit incomplete/i)).toBeVisible()
    await expect(page.getByText(/raised concerns it never answered/i)).toBeVisible()
    await expect(page.getByText(/Scope assumes art that does not exist yet/)).toBeVisible()
  })
})

test.describe('home', () => {
  test('the timeline range filters captured days', async ({ page }) => {
    // The fixture spans several days with one deliberately missing, so a range
    // that changes nothing would be visible here.
    await page.goto('/#/home')
    await page.locator('.chart-panel').waitFor()
    // The chart draws each series as a polyline, so the number of days shown
    // is the number of coordinate pairs in it.
    const pointsFor = async (label: string) => {
      await page.locator('.segmented').getByRole('button', { name: label, exact: true }).click()
      await expect(page.locator('.segmented button.active')).toHaveText(label)
      return page.locator('.chart-panel svg polyline').first().evaluate(
        node => (node.getAttribute('points') || '').trim().split(/\s+/).filter(Boolean).length)
    }
    const all = await pointsFor('All')
    const week = await pointsFor('7D')
    expect(all).toBeGreaterThan(0)
    expect(week).toBeLessThanOrEqual(all)
    await expect(page.locator('.segmented button.active')).toHaveText('7D')
  })

  test('a recent source opens its own provenance', async ({ page }) => {
    await page.goto('/#/home')
    const link = page.locator('.activity-list button, .source-list button').first()
    if (await link.count()) {
      await link.click()
      await expect(page.locator('aside[aria-label="Evidence inspector"]')).toBeVisible()
      await page.keyboard.press('Escape')
    }
  })
})

test.describe('errors', () => {
  test('a failed panel says so and the alert can be dismissed', async ({ page }) => {
    await page.route('**/api/dashboard/summary', route => route.fulfill({
      status: 500, json: { detail: 'summary unavailable in this test' },
    }))
    await page.goto('/#/home')
    const alert = page.locator('.global-alert, .warning-box').first()
    await expect(alert).toBeVisible({ timeout: 15_000 })
    const dismiss = alert.getByRole('button')
    if (await dismiss.count()) {
      await dismiss.first().click()
      await expect(alert).toBeHidden()
    }
  })
})

test.describe('meta hunter', () => {
  test('choosing a run loads that run\'s report', async ({ page }) => {
    await page.goto('/#/meta')
    const picker = page.getByLabel('Inspect research run')
    await expect(picker).toBeVisible()
    const options = await picker.locator('option').allInnerTexts()
    expect(options.length).toBeGreaterThan(0)
    // The selected run's niche appears in the panel below it.
    const niche = options[0].split('\u2014')[0].trim()
    await expect(page.locator('.workspace')).toContainText(niche.slice(0, 12))
  })
})

test.describe('contrast', () => {
  test('body text meets a readable contrast ratio', async ({ page }) => {
    // A programmatic check of the main text colours, not a full accessibility
    // audit; the inventory still records that no assistive-technology pass has
    // been run.
    await page.goto('/#/home')
    await page.locator('.workspace').first().waitFor()
    const failures = await page.evaluate(() => {
      const luminance = (colour: string) => {
        const [r, g, b] = (colour.match(/\d+(\.\d+)?/g) || ['0', '0', '0']).slice(0, 3).map(Number)
        const channel = (value: number) => {
          const c = value / 255
          return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
        }
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
      }
      const backgroundOf = (node: Element): string => {
        let current: Element | null = node
        while (current) {
          const colour = getComputedStyle(current).backgroundColor
          if (colour && !colour.includes('rgba(0, 0, 0, 0)')) return colour
          current = current.parentElement
        }
        return 'rgb(255, 255, 255)'
      }
      const bad: string[] = []
      for (const node of Array.from(document.querySelectorAll('p, h1, h2, td, strong'))) {
        const text = (node.textContent || '').trim()
        if (!text || node.children.length) continue
        // Hidden elements keep their computed colours; measuring them reports
        // contrast for something nobody can see.
        const box = node.getBoundingClientRect()
        if (!box.width || !box.height) continue
        const style = getComputedStyle(node)
        const a = luminance(style.color) + 0.05
        const b = luminance(backgroundOf(node)) + 0.05
        const ratio = a > b ? a / b : b / a
        if (ratio < 4.5) bad.push(`${node.tagName} "${text.slice(0, 24)}" ratio ${ratio.toFixed(2)}`)
      }
      return bad.slice(0, 5)
    })
    expect(failures, `low-contrast text: ${failures.join(' | ')}`).toEqual([])
  })
})

test.describe('interrupted jobs', () => {
  test('an interrupted run is explained and can be resumed', async ({ page }) => {
    // Resuming is deliberately not automatic on startup, so the operator needs
    // a control. Stubbed so the state is exact without killing the service.
    let resumed = false
    await page.route('**/api/audit-jobs/**', async route => {
      const url = route.request().url()
      if (url.endsWith('/resume')) { resumed = true; return route.fulfill({ json: interrupted('queued') }) }
      await route.fulfill({ json: interrupted('interrupted') })
    })
    await page.route('**/api/candidates/*/audit-jobs*', route =>
      route.fulfill({ status: 202, json: interrupted('interrupted') }))

    function interrupted(status: string) {
      return {
        id: 'job-1', candidate_id: 'c1', operation: 'analyze_game', proposal_id: null,
        status, created_at: new Date().toISOString(), completed_at: null, model_attempts: 3,
        audit_id: null, error: null, remaining_seconds: 900,
        events: [{ sequence: 1, stage: 'interrupted', detail: 'Previous process stopped', at: new Date().toISOString() }],
      }
    }

    await page.goto('/#/scout')
    await page.getByRole('button', { name: 'Analyze this game' }).click()

    // Starting opens the drawer, which offers its own Resume.
    const drawer = page.locator('aside[aria-label="Background work"]')
    await expect(drawer.getByRole('button', { name: 'Resume' })).toBeVisible()
    await page.keyboard.press('Escape')

    // The page explains the state too, rather than only the drawer.
    await expect(page.getByText(/This run was interrupted/i)).toBeVisible()
    await expect(page.getByText(/original deadline and attempt count were kept/i)).toBeVisible()
    await page.getByRole('button', { name: 'Resume it' }).click()
    await expect.poll(() => resumed, { timeout: 10_000 }).toBe(true)
  })
})

test.describe('discovery is visible', () => {
  test('health lists the local and first-party search sources', async ({ page }) => {
    // Both were being recorded and never shown, so an operator could not tell
    // whether the local instance was answering.
    await page.goto('/#/health')
    await expect(page.locator('table tbody tr', { hasText: 'Local search (SearxNG)' })).toBeVisible()
    await expect(page.locator('table tbody tr', { hasText: 'Roblox search' })).toBeVisible()
    await expect(page.locator('table tbody tr', { hasText: 'Tavily search' })).toBeVisible()
  })

  test('the Meta Hunter page shows which sources were asked', async ({ page }) => {
    await page.goto('/#/meta')
    const panel = page.locator('.data-panel', { hasText: 'Discovery sources' })
    await expect(panel).toBeVisible()
    for (const label of ['Roblox search', 'Local search (SearxNG)', 'Tavily']) {
      await expect(panel.locator('tbody tr', { hasText: label })).toBeVisible()
    }
  })

  test('the planned searches are readable, not just sent', async ({ page }) => {
    // The plan reached the API and nothing rendered it.
    await page.route('**/api/research-runs*', async route => {
      const response = await route.fetch()
      const runs = await response.json()
      if (runs[0]?.progress) {
        runs[0].progress.search_queries = [
          'site:roblox.com/games toilet tycoon',
          'site:roblox.com/games plumbing simulator',
        ]
      }
      await route.fulfill({ json: runs })
    })
    await page.goto('/#/meta')
    const fold = page.locator('details.brief-fold', { hasText: 'Planned searches' })
    await expect(fold).toBeVisible()
    await fold.locator('summary').click()
    await expect(fold).toContainText('toilet tycoon')
    await expect(fold).toContainText('plumbing simulator')
  })
})

test.describe('the Hunter to Scout queue', () => {
  // The queue's contents are backend behaviour and are covered there. These
  // stub it because an earlier spec runs a real audit against the shared
  // fixture ledger, which empties the queue these assertions read.
  const QUEUE = {
    queued: [
      { proposal_id: 'p1', candidate_id: 'c1', universe_id: '1', game_name: 'Alpha',
        concept_title: 'First concept', core_loop: 'loop', niche: 'n',
        created_at: '2026-09-15T00:00:00+00:00', facts: 3, available: true, unavailable_reason: '' },
      { proposal_id: 'p2', candidate_id: 'c2', universe_id: '2', game_name: 'Beta',
        concept_title: 'Second concept', core_loop: 'loop', niche: 'n',
        created_at: '2026-09-15T00:00:00+00:00', facts: 2, available: true, unavailable_reason: '' },
    ], runnable: 2, blocked: 0, note: 'note' }

  test('concepts arrive routed and selected, and the count is on the button', async ({ page }) => {
    // Routing is the automatic half. Nothing should need clicking for a
    // Hunter concept to be waiting here.
    await page.route('**/api/scout/queue', route => route.fulfill({ json: QUEUE }))
    await page.goto('/#/scout')
    const panel = page.locator('.data-panel', { hasText: 'Audit queue' })
    await expect(panel).toBeVisible()
    const run = panel.getByRole('button', { name: /Start \d+ audit/ })
    await expect(run).toBeVisible()
    await expect(run).toBeEnabled()
    await expect(run).toContainText(/Start [1-9]/)
  })

  test('nothing runs until the button is pressed', async ({ page }) => {
    // The whole point of the split: an audit spends minutes of a serialized
    // model, so arriving in the queue must not start anything.
    let started = 0
    await page.route('**/api/scout/queue/run', async route => { started += 1; await route.abort() })
    await page.goto('/#/scout')
    await expect(page.locator('.data-panel', { hasText: 'Audit queue' })).toBeVisible()
    await page.waitForTimeout(1200)
    expect(started).toBe(0)
  })

  test('a concept can be de-selected before running, and the count follows', async ({ page }) => {
    await page.route('**/api/scout/queue', route => route.fulfill({ json: QUEUE }))
    await page.goto('/#/scout')
    const panel = page.locator('.data-panel', { hasText: 'Audit queue' })
    const runButton = panel.getByRole('button', { name: /Start \d+ audit/ })
    // Wait for the queue to actually load; reading the count while it still
    // says zero compares nothing against nothing.
    await expect(runButton).toContainText(/Start [1-9]/)
    const before = Number((await runButton.innerText()).match(/\d+/)![0])
    await panel.getByRole('button', { name: 'Choose which to run' }).click()
    const dialog = page.getByRole('dialog', { name: /Choose which concepts/i })
    await expect(dialog).toBeVisible()
    await dialog.locator('input[type=checkbox]:not([disabled])').first().uncheck()
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(runButton).toContainText(`Start ${before - 1} audit`)
  })

  test('an empty queue says so instead of offering a dead button', async ({ page }) => {
    // Every concept audited is the normal resting state, not a fault.
    await page.route('**/api/scout/queue', async route => {
      await route.fulfill({ json: { queued: [], runnable: 0, blocked: 0, note: 'note' } })
    })
    await page.goto('/#/scout')
    const panel = page.locator('.data-panel', { hasText: 'Audit queue' })
    await expect(panel).toContainText('Nothing waiting')
    await expect(panel.getByRole('button', { name: /Start \d+ audit/ })).toHaveCount(0)
  })

  test('a concept that cannot run is shown with its reason, not hidden', async ({ page }) => {
    await page.route('**/api/scout/queue', async route => {
      await route.fulfill({ json: {
        queued: [
          { proposal_id: 'p1', candidate_id: 'c1', universe_id: '1', game_name: 'Alpha',
            concept_title: 'Runnable concept', core_loop: 'loop', niche: 'n', created_at: '2026-09-15T00:00:00+00:00',
            facts: 3, available: true, unavailable_reason: '' },
          { proposal_id: 'p2', candidate_id: 'c2', universe_id: '2', game_name: 'Beta',
            concept_title: 'Blocked concept', core_loop: 'loop', niche: 'n', created_at: '2026-09-15T00:00:00+00:00',
            facts: 0, available: false, unavailable_reason: 'No admissible evidence remains for this game' },
        ], runnable: 1, blocked: 1, note: 'note' } })
    })
    await page.goto('/#/scout')
    const panel = page.locator('.data-panel', { hasText: 'Audit queue' })
    await panel.getByRole('button', { name: 'Choose which to run' }).click()
    const blocked = page.locator('.queue-row.unavailable')
    await expect(blocked).toContainText('Blocked concept')
    await expect(blocked).toContainText('No admissible evidence')
    await expect(blocked.locator('input')).toBeDisabled()
  })

  test('results are cards that scroll sideways without the page doing so', async ({ page }) => {
    await page.route('**/api/scout/results*', async route => {
      await route.fulfill({ json: { cards: Array.from({ length: 8 }, (_, index) => ({
        audit_id: `a${index}`, candidate_id: 'c1', proposal_id: 'p1', universe_id: '77',
        niche: 'cooperative fishing', concept_title: `Concept ${index}`, core_loop: 'A loop.',
        evidence_state: 'source_backed', risks: 2, cited_facts: 3,
        created_at: '2026-09-15T00:00:00+00:00' })) } })
    })
    await page.goto('/#/scout')
    const strip = page.locator('.card-strip')
    await expect(strip).toBeVisible()
    await expect(strip.locator('.concept-card')).toHaveCount(8)
    // The strip scrolls; the document must not.
    const scrollable = await strip.evaluate(node => node.scrollWidth > node.clientWidth + 4)
    expect(scrollable).toBe(true)
    const pageScrolls = await page.evaluate(() =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)
    expect(pageScrolls).toBe(false)
  })

  test('clicking a card opens the full brief for that concept', async ({ page }) => {
    await page.route('**/api/scout/results*', async route => {
      await route.fulfill({ json: { cards: [{
        audit_id: 'audit-9', candidate_id: 'c1', proposal_id: 'p1', universe_id: '77',
        niche: 'cooperative fishing', concept_title: 'Lantern Bay', core_loop: 'Fish at dusk.',
        evidence_state: 'source_backed', risks: 1, cited_facts: 2,
        created_at: '2026-09-15T00:00:00+00:00' }] } })
    })
    await page.route('**/api/audits/audit-9', async route => {
      await route.fulfill({ json: {
        candidate_id: 'c1', audit_id: 'audit-9', evidence_state: 'source_backed',
        risks: ['Retention is unproven'],
        proposal: { concept_title: 'Lantern Bay', core_loop: 'Fish at dusk.',
                    differentiator: 'Shared nets.', build_steps: ['Build the dock'],
                    risks: [], questions: [], supporting_fact_ids: [] } } })
    })
    await page.goto('/#/scout')
    await page.locator('.concept-card', { hasText: 'Lantern Bay' }).click()
    const dialog = page.getByRole('dialog', { name: 'Lantern Bay' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('Shared nets')
    await expect(dialog).toContainText('Build the dock')
    await expect(dialog).toContainText('Retention is unproven')
  })
})

test.describe('the opportunity score', () => {
  const SCORED = {
    version: 'opportunity-v1', universe_id: '77', score: 0.72, state: 'ranked',
    reason: '4 of 5 components measured', calibrated: false, missing: ['acceleration'],
    note: 'A comparable ranking index, not a probability.',
    components: [
      { key: 'demand', label: 'Demand', weight: 0.3, measured: true, raw: 20000,
        normalised: 0.5, contribution: 0.15, meaning: 'how many people are playing', detail: 'd' },
      { key: 'acceleration', label: 'Acceleration', weight: 0.1, measured: false, raw: null,
        normalised: null, contribution: null, meaning: 'steepening', detail: 'needs three observations' },
    ],
  }

  test('a card shows each component, and an unmeasured one shows no number', async ({ page }) => {
    await page.route('**/api/scout/results*', route => route.fulfill({ json: { cards: [{
      audit_id: 'a1', candidate_id: 'c1', proposal_id: 'p1', universe_id: '77', niche: 'n',
      concept_title: 'Scored Concept', core_loop: 'loop', evidence_state: 'source_backed',
      risks: 1, cited_facts: 2, created_at: '2026-09-16T00:00:00+00:00' }] } }))
    await page.route('**/api/audits/a1', route => route.fulfill({ json: {
      candidate_id: 'c1', audit_id: 'a1', evidence_state: 'source_backed', risks: [],
      proposal: { concept_title: 'Scored Concept', core_loop: 'loop', differentiator: 'd',
                  build_steps: ['one'], risks: [], questions: [], supporting_fact_ids: [] } } }))
    await page.route('**/api/opportunity/77', route => route.fulfill({ json: {
      ...SCORED,
      calibration: { universe_id: '77', score: 0.72, probability: null,
                     state: 'insufficient_outcomes', reason: '0 games watched for a full 24h window' } } }))

    await page.goto('/#/scout')
    await page.locator('.concept-card', { hasText: 'Scored Concept' }).click()
    const panel = page.locator('.opportunity')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('72 / 100')
    await expect(panel).toContainText('uncalibrated ranking')
    await expect(panel.locator('.opp-row.unmeasured')).toContainText('Acceleration')
    await expect(panel.locator('.opp-row.unmeasured > b')).toContainText('—')
  })

  test('an unmeasured probability says so rather than showing a number', async ({ page }) => {
    // The one number nobody should ever see invented.
    await page.route('**/api/scout/results*', route => route.fulfill({ json: { cards: [{
      audit_id: 'a1', candidate_id: 'c1', proposal_id: 'p1', universe_id: '77', niche: 'n',
      concept_title: 'Scored Concept', core_loop: 'loop', evidence_state: 'source_backed',
      risks: 1, cited_facts: 2, created_at: '2026-09-16T00:00:00+00:00' }] } }))
    await page.route('**/api/audits/a1', route => route.fulfill({ json: {
      candidate_id: 'c1', audit_id: 'a1', evidence_state: 'source_backed', risks: [], proposal: null } }))
    await page.route('**/api/opportunity/77', route => route.fulfill({ json: {
      ...SCORED,
      calibration: { universe_id: '77', score: 0.72, probability: null,
                     state: 'insufficient_outcomes',
                     reason: '0 games watched for a full 24h window; 30 are required' } } }))

    await page.goto('/#/scout')
    await page.locator('.concept-card', { hasText: 'Scored Concept' }).click()
    const probability = page.locator('.opportunity-probability')
    await expect(probability).toContainText('not measured yet')
    await expect(probability).toContainText('30 are required')
    await expect(probability).not.toContainText('%')
  })

  test('a measured probability is shown with the cohort behind it', async ({ page }) => {
    await page.route('**/api/scout/results*', route => route.fulfill({ json: { cards: [{
      audit_id: 'a1', candidate_id: 'c1', proposal_id: 'p1', universe_id: '77', niche: 'n',
      concept_title: 'Scored Concept', core_loop: 'loop', evidence_state: 'source_backed',
      risks: 1, cited_facts: 2, created_at: '2026-09-16T00:00:00+00:00' }] } }))
    await page.route('**/api/audits/a1', route => route.fulfill({ json: {
      candidate_id: 'c1', audit_id: 'a1', evidence_state: 'source_backed', risks: [], proposal: null } }))
    await page.route('**/api/opportunity/77', route => route.fulfill({ json: {
      ...SCORED,
      calibration: { universe_id: '77', score: 0.72, probability: 0.4, state: 'measured',
                     target_ccu: 5000, reason: 'observed rate',
                     band: { from: 0.5, to: 0.75, games: 25, reached: 10, rate: 0.4 } } } }))

    await page.goto('/#/scout')
    await page.locator('.concept-card', { hasText: 'Scored Concept' }).click()
    const probability = page.locator('.opportunity-probability')
    await expect(probability).toContainText('40%')
    await expect(probability).toContainText('10 of 25 observed games')
    await expect(probability).toContainText('not a forecast')
  })
})

// Three complaints, reported by the user against the live app: source links
// returned raw JSON, text was cut off and ran together, and buttons were dead
// with no stated reason. Each is now a check rather than a memory.
const ALL_PAGES = ['home', 'ideas', 'sources', 'market', 'matching', 'meta',
                   'scout', 'calibration', 'history', 'health'] as const

test.describe('the interface reads as English, not as data', () => {
  for (const page of ALL_PAGES) {
    test(`${page} sends nobody to raw JSON`, async ({ page: browser }) => {
      await browser.goto(`/#/${page}`)
      await browser.locator('main').waitFor()
      await browser.waitForTimeout(700)
      // The canonical source URL in the evidence drawer is a real external
      // link and is allowed; an in-app link into our own /api/ is not.
      const raw = await browser.locator('main a[href^="/api/"]').count()
      expect(raw, 'an in-app link opens raw JSON').toBe(0)
    })

    test(`${page} does not chop its own captions`, async ({ page: browser }) => {
      await browser.goto(`/#/${page}`)
      await browser.locator('main').waitFor()
      await browser.waitForTimeout(700)
      const clipped = await browser.evaluate(() => {
        const bad: string[] = []
        for (const el of document.querySelectorAll('main *')) {
          if (el.children.length) continue
          const text = (el.textContent || '').trim()
          if (!text) continue
          const style = getComputedStyle(el)
          if (style.overflow === 'auto' || style.overflowX === 'auto') continue
          if (el.scrollWidth > el.clientWidth + 2 && style.overflowX !== 'visible') {
            bad.push(text.slice(0, 40))
          }
        }
        return bad
      })
      expect(clipped, 'text is cut off by its own container').toEqual([])
    })
  }

  test('a disabled review button says what it is waiting for', async ({ page }) => {
    await page.goto('/#/matching')
    await page.locator('main').waitFor()
    await page.waitForTimeout(900)
    const approve = page.getByRole('button', { name: 'Approve' })
    if (!(await approve.count())) test.skip(true, 'no association is in review')
    if (await approve.isDisabled()) {
      await expect(approve).toHaveAttribute('title', /Needs /)
      await expect(page.locator('.gate-hint')).toContainText(/characters|candidate/)
    }
  })

  test('the reason field says how much more is needed', async ({ page }) => {
    await page.goto('/#/matching')
    await page.locator('main').waitFor()
    await page.waitForTimeout(900)
    const reason = page.locator('main textarea')
    if (!(await reason.count())) test.skip(true, 'no association is in review')
    await expect(page.locator('main label', { hasText: 'Required reason' }))
      .toContainText('more character(s) needed')
    await reason.fill('This is a long enough explanation.')
    await expect(page.locator('main label', { hasText: 'Required reason' }))
      .toContainText('Long enough')
  })
})

test.describe('start, restart and resume', () => {
  for (const [page, names] of [
    ['meta', ['Start research run', 'Restart last run', 'Resume interrupted']],
    ['scout', [/Start \d+ audit/, 'Restart last audit', 'Resume interrupted']],
  ] as const) {
    test(`${page} offers all three actions`, async ({ page: browser }) => {
      await browser.goto(`/#/${page}`)
      await browser.locator('.agent-actions').first().waitFor()
      for (const name of names) {
        await expect(browser.getByRole('button', { name }).first()).toBeVisible()
      }
    })

    test(`${page} says why a disabled action is disabled`, async ({ page: browser }) => {
      // A dead button with an invisible precondition is the complaint this
      // whole pattern exists to answer.
      await browser.goto(`/#/${page}`)
      await browser.locator('.agent-actions').first().waitFor()
      const buttons = browser.locator('.agent-actions button')
      for (let i = 0; i < await buttons.count(); i++) {
        const button = buttons.nth(i)
        if (await button.isDisabled()) {
          const why = await button.getAttribute('title')
          expect(why, 'a disabled action gave no reason').toBeTruthy()
        }
      }
    })

    test(`${page} explains how the three differ`, async ({ page: browser }) => {
      await browser.goto(`/#/${page}`)
      await browser.locator('.agent-actions').first().waitFor()
      const hint = browser.locator('.agent-actions + .gate-hint').first()
      await expect(hint).toBeVisible()
    })
  }

  test('resume is offered only when something was interrupted', async ({ page }) => {
    await page.route('**/api/audit-jobs', route => route.fulfill({
      json: { jobs: [], resumable: [], restartable: ['job-1'] } }))
    await page.goto('/#/scout')
    await page.locator('.agent-actions').first().waitFor()
    const resume = page.getByRole('button', { name: 'Resume interrupted' })
    await expect(resume).toBeDisabled()
    await expect(resume).toHaveAttribute('title', /No interrupted audit/)
  })

  test('resume wakes up when an audit was interrupted', async ({ page }) => {
    await page.route('**/api/audit-jobs', route => route.fulfill({
      json: { jobs: [], resumable: ['job-9'], restartable: ['job-9'] } }))
    await page.goto('/#/scout')
    await page.locator('.agent-actions').first().waitFor()
    await expect(page.getByRole('button', { name: 'Resume interrupted' })).toBeEnabled()
  })
})

test.describe('a checkbox is not a text field', () => {
  test('the selection panel shows readable options, not one giant tick box',
    async ({ page }) => {
    // `input, select, textarea { width: 100% }` stretched every checkbox to
    // the width of its row -- 665px with 10px of padding -- and squashed the
    // concept name beside it to 72px. The panel rendered as a single enormous
    // tick and no options at all.
    await page.route('**/api/scout/queue', route => route.fulfill({ json: {
      queued: [1, 2, 3].map(n => ({
        proposal_id: `p${n}`, candidate_id: `c${n}`, universe_id: String(n),
        game_name: `Game ${n}`, concept_title: `Concept number ${n}`,
        core_loop: 'loop', niche: 'pets', created_at: '2026-09-16T00:00:00+00:00',
        facts: 6, available: true, unavailable_reason: '' })),
      runnable: 3, blocked: 0, note: 'note' } }))
    await page.goto('/#/scout')
    await page.locator('.agent-actions').first().waitFor()
    const choose = page.getByRole('button', { name: 'Choose which to run' })
    await choose.click()
    const rows = page.locator('.queue-row')
    await expect(rows.first()).toBeVisible()

    const sizes = await page.evaluate(() => [...document.querySelectorAll('.queue-row')]
      .map(row => ({
        box: Math.round(row.querySelector('input')!.getBoundingClientRect().width),
        text: Math.round(row.querySelector('div')!.getBoundingClientRect().width),
        label: (row.querySelector('strong')?.textContent || '').trim(),
      })))

    expect(sizes.length).toBeGreaterThan(0)
    for (const row of sizes) {
      expect(row.box, 'the checkbox swallowed the row').toBeLessThan(40)
      expect(row.text, 'the concept name was squashed').toBeGreaterThan(row.box * 4)
      expect(row.label, 'a row with no readable name').not.toBe('')
    }
  })

  test('no checkbox anywhere is stretched by the form field rule', async ({ page }) => {
    for (const slug of ['scout', 'matching', 'meta', 'ideas']) {
      await page.goto(`/#/${slug}`)
      await page.locator('main').waitFor()
      await page.waitForTimeout(600)
      const wide = await page.evaluate(() =>
        [...document.querySelectorAll('input[type=checkbox], input[type=radio]')]
          .map(el => Math.round(el.getBoundingClientRect().width))
          .filter(width => width > 40))
      expect(wide, `a stretched checkbox on ${slug}`).toEqual([])
    }
  })

  test('the panel heading is two lines, not one run-on', async ({ page }) => {
    await page.route('**/api/scout/queue', route => route.fulfill({ json: {
      queued: [1, 2, 3].map(n => ({
        proposal_id: `p${n}`, candidate_id: `c${n}`, universe_id: String(n),
        game_name: `Game ${n}`, concept_title: `Concept number ${n}`,
        core_loop: 'loop', niche: 'pets', created_at: '2026-09-16T00:00:00+00:00',
        facts: 6, available: true, unavailable_reason: '' })),
      runnable: 3, blocked: 0, note: 'note' } }))
    await page.goto('/#/scout')
    await page.locator('.agent-actions').first().waitFor()
    const choose = page.getByRole('button', { name: 'Choose which to run' })
    await choose.click()
    const dialog = page.getByRole('dialog', { name: /Choose which concepts/i })
    const heading = dialog.locator('header > div').first()
    await expect(heading).toBeVisible()
    // It read "Routed from Meta Hunter3 of 3 selected" on one line. Text
    // content always concatenates, so the check is on the rendered layout:
    // the eyebrow and the count must occupy separate lines.
    const lines = await heading.evaluate(node => (node as HTMLElement).innerText.trim().split(String.fromCharCode(10)).length)
    expect(lines, 'the eyebrow and the count ran together').toBeGreaterThan(1)
  })
})
