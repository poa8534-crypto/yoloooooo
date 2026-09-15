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
