import { expect, test } from '@playwright/test'

// Actions that change data. These run against the fixture ledger only: a
// temporary database, a temporary artifact directory, a stubbed model endpoint
// and no API credentials. Nothing here can reach the operator's ledger or
// spend quota.

test.describe('venture scout audit', () => {
  test('an audit runs, is stored, and survives a reload', async ({ page }) => {
    test.setTimeout(120_000)
    await page.goto('/#/scout')
    await page.getByLabel('Operation').selectOption('analyze_game')
    await page.getByRole('button', { name: 'Analyze this game' }).click()

    // Queueing is distinguishable from inference while it runs.
    await expect(page.getByRole('button', { name: /deliberating/i })).toBeVisible()

    const output = page.locator('.audit-output, .data-panel', { hasText: 'Harbour Lantern Cooperative' })
    await expect(output.first()).toBeVisible({ timeout: 90_000 })

    // Written to the ledger, not just rendered: it comes back after a reload.
    await page.reload()
    await expect(page.locator('body')).toContainText('Harbour Lantern Cooperative', { timeout: 20_000 })
  })

  test('the same audit appears in Agent History with its citation', async ({ page }) => {
    await page.goto('/#/history')
    const row = page.locator('.history-row', { hasText: 'Harbour Lantern Cooperative' }).first()
    await expect(row).toBeVisible({ timeout: 20_000 })
    await expect(row).toContainText(/venture scout/i)
    await expect(row).toContainText(/design/i)

    await row.locator('header').click()
    const body = row.locator('.history-body')
    await expect(body).toBeVisible()
    // The citation reads as the claim, not as a row id.
    await expect(body).toContainText(/Cited evidence/i)
  })

  test('one submission records exactly one audit, and cannot be double-clicked', async ({ page }) => {
    test.setTimeout(120_000)
    // The run is what must not double, so count before and after rather than
    // waiting for text: a previously stored audit renders identically to a new
    // one, which made an earlier version of this test pass for the wrong reason.
    const startCount = (await (await page.request.get('/api/agent-runs/count?kind=venture_scout')).json()).total

    await page.goto('/#/scout')
    await page.getByLabel('Operation').selectOption('analyze_game')
    const button = page.getByRole('button', { name: 'Analyze this game' })
    await button.click()

    const running = page.getByRole('button', { name: /deliberating/i })
    await expect(running).toBeVisible()
    // The control is unavailable while the operation is in flight, so a second
    // click cannot reach the backend at all.
    await expect(running).toBeDisabled()

    await expect(page.getByRole('button', { name: 'Analyze this game' })).toBeEnabled({ timeout: 90_000 })
    const after = (await (await page.request.get('/api/agent-runs/count?kind=venture_scout')).json()).total
    expect(after).toBe(startCount + 1)
  })
})

test.describe('research run', () => {
  test('starting a run is refused without a niche', async ({ page }) => {
    await page.goto('/#/meta')
    const start = page.getByRole('button', { name: /Start|Run research|Launch/i }).first()
    if (await start.count()) {
      const disabled = await start.isDisabled()
      if (!disabled) {
        // An empty niche must not reach the backend as a valid run.
        await start.click()
        await expect(page.locator('body')).not.toContainText('Investigating round', { timeout: 3_000 })
      }
    }
  })
})

test.describe('the ledger the browser tests use', () => {
  test('carries no credentials and no quota reservations', async ({ page }) => {
    const health = await (await page.request.get('/api/health')).json()
    expect(health.connectors.tavily_configured).toBe(false)
    expect(health.connectors.youtube_configured).toBe(false)
    expect(health.quotas).toEqual({})
  })

  test('every captured artifact URL is free of credentials', async ({ page }) => {
    const sources = await (await page.request.get('/api/sources?paged=true&limit=500')).json()
    for (const item of sources.items) {
      expect(item.url).not.toMatch(/[?&](key|api_key|access_token)=/i)
    }
  })
})

test.describe('background work', () => {
  test('a run is startable, watchable and cancellable from the page', async ({ page }) => {
    test.setTimeout(120_000)
    await page.goto('/#/scout')
    await page.getByLabel('Operation').selectOption('analyze_game')
    await page.getByRole('button', { name: 'Analyze this game' }).click()

    // The drawer opens with the job identified, rather than a bare spinner.
    const drawer = page.locator('aside[aria-label="Background work"]')
    await expect(drawer).toBeVisible()
    await expect(drawer.locator('header')).toContainText(/queued|running/)
    await expect(drawer.locator('.work-log article').first()).toBeVisible({ timeout: 30_000 })

    // Cancelling is reachable, which the synchronous endpoint never allowed.
    await drawer.getByRole('button', { name: 'Cancel run' }).click()
    await expect(drawer.locator('header')).toContainText(/cancelled/i, { timeout: 30_000 })
    await expect(page.getByRole('button', { name: 'Analyze this game' })).toBeEnabled()
  })

  test('the drawer is reachable before anything has run', async ({ page }) => {
    await page.goto('/#/scout')
    await page.getByRole('button', { name: /See what it is doing/ }).click()
    const drawer = page.locator('aside[aria-label="Background work"]')
    await expect(drawer).toBeVisible()
    await expect(drawer).toContainText(/Nothing running/i)
    await page.keyboard.press('Escape')
    await expect(page.locator('.drawer-scrim.open')).toHaveCount(0)
  })
})

test.describe('idea panel runs the same way', () => {
  test('a brief starts a durable job and can be watched and cancelled', async ({ page }) => {
    test.setTimeout(120_000)
    await page.goto('/#/ideas')
    await page.locator('.filter-row').first().waitFor()
    const toggle = page.getByRole('button', { name: /Game dossiers|Games/i })
    if (await toggle.count()) await toggle.first().click()
    await page.locator('.ideas-list button').first().waitFor()
    await page.locator('.ideas-list button').first().click()

    // The label reflects which operation this game supports.
    const action = page.getByRole('button', { name: /Audit this idea|Analyze this game/ })
    await expect(action).toBeVisible()
    await action.click()

    const drawer = page.locator('aside[aria-label="Background work"]')
    await expect(drawer).toBeVisible()
    await expect(drawer.locator('header')).toContainText(/queued|running/)
    await drawer.getByRole('button', { name: 'Cancel run' }).click()
    await expect(drawer.locator('header')).toContainText(/cancelled/i, { timeout: 30_000 })
  })

  test('both pages share one drawer implementation', async ({ page }) => {
    // Two drawers meant two behaviours; the candidate-keyed one did not
    // survive a restart, and only one of them could cancel.
    for (const route of ['/#/scout', '/#/ideas']) {
      await page.goto(route)
      if (route === '/#/ideas') {
        await page.locator('.filter-row').first().waitFor()
        const toggle = page.getByRole('button', { name: /Game dossiers|Games/i })
        if (await toggle.count()) await toggle.first().click()
        await page.locator('.ideas-list button').first().waitFor()
        await page.locator('.ideas-list button').first().click()
      }
      await page.getByRole('button', { name: /See what it is doing/ }).click()
      await expect(page.locator('aside[aria-label="Background work"]')).toBeVisible()
      await page.keyboard.press('Escape')
    }
  })
})
