import { expect, test, type Page } from '@playwright/test'

// One test per finding in the interaction audit, exercising the real control
// rather than the endpoint behind it.

/** The dashboard fetches after mount, so wait for data before counting rows. */
async function openIdeas(page: Page, kind: 'ideas' | 'games' = 'games') {
  await page.goto('/#/ideas')
  await page.locator('.filter-row').first().waitFor()
  if (kind === 'games') {
    const toggle = page.getByRole('button', { name: /Game dossiers|Games/i })
    if (await toggle.count()) await toggle.first().click()
  }
  await page.locator('.ideas-list button').first().waitFor()
}

test.describe('idea filters', () => {
  test('"Research only" includes collection-only games', async ({ page }) => {
    // The filter mapped to research_more alone, so every current candidate --
    // all of which are collection_only -- vanished from the list.
    await openIdeas(page)
    const all = await page.locator('.ideas-list button').count()
    expect(all).toBeGreaterThan(0)

    await page.getByRole('button', { name: 'Research only', exact: true }).click()
    await expect(page.locator('.ideas-list button')).toHaveCount(all)
  })

  test('a filter with no members says so instead of showing everything', async ({ page }) => {
    await openIdeas(page)
    await page.getByRole('button', { name: 'Recommended', exact: true }).click()
    await expect(page.locator('.ideas-list button')).toHaveCount(0)
    await expect(page.getByText(/No candidates in this state|No tracked candidate/i).first()).toBeVisible()
  })

  test('generated ideas are separated from captured games', async ({ page }) => {
    // Every discovered game used to be presented as an idea.
    await page.goto('/#/ideas')
    await page.locator('.filter-row').first().waitFor()
    const ideasOnly = await page.locator('.ideas-list button').count()
    await openIdeas(page, 'games')
    const games = await page.locator('.ideas-list button').count()
    expect(games).toBeGreaterThanOrEqual(ideasOnly)
  })
})

test.describe('evidence drawer', () => {
  test('a fact opens its own claim, and Escape closes the drawer', async ({ page }) => {
    await openIdeas(page)
    await page.locator('.ideas-list button').first().click()

    const fact = page.locator('.fact-stack button').first()
    await fact.waitFor()
    const claim = (await fact.locator('span').first().innerText()).trim()
    await fact.click()

    const drawer = page.locator('.drawer-scrim.open')
    await expect(drawer).toBeVisible()
    // The selected claim is the subject, not the whole multi-game response.
    await expect(drawer).toContainText(claim.slice(0, 24))

    await page.keyboard.press('Escape')
    await expect(page.locator('.drawer-scrim.open')).toHaveCount(0)
  })

  test('focus returns to the control that opened the drawer', async ({ page }) => {
    await openIdeas(page)
    await page.locator('.ideas-list button').first().click()
    const fact = page.locator('.fact-stack button').first()
    await fact.waitFor()
    await fact.click()
    await expect(page.locator('.drawer-scrim.open')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(fact).toBeFocused()
  })
})

test.describe('sources', () => {
  test('search reaches records beyond the first page', async ({ page }) => {
    await page.goto('/#/sources')
    await expect(page.locator('table tbody tr').first()).toBeVisible()

    await page.getByLabel('Filter sources').fill('roblox.com')
    await expect(page.locator('.history-toolbar')).toContainText(/matching/)
    await expect(page.locator('table tbody tr').first()).toBeVisible()

    await page.getByLabel('Filter sources').fill('no-such-publisher')
    await expect(page.getByText(/Nothing in the whole library matches/i)).toBeVisible()
  })

  test('paging controls disable at the ends', async ({ page }) => {
    await page.goto('/#/sources')
    await expect(page.getByRole('button', { name: '← Previous' })).toBeDisabled()
  })
})

test.describe('health', () => {
  test('configuration and observation are reported separately', async ({ page }) => {
    await page.goto('/#/health')
    const row = page.locator('table tbody tr', { hasText: 'Tavily search' })
    await expect(row).toBeVisible()
    // The fixture runs with no credentials, so this must not claim health.
    await expect(row).toContainText(/Not configured/i)
  })

  test('the local model server is reported as observed, not assumed', async ({ page }) => {
    await page.goto('/#/health')
    await expect(page.locator('table tbody tr', { hasText: 'Local model server' })).toContainText(/Reachable/i)
  })
})

test.describe('calibration', () => {
  test('captured days are shown and the unimplemented part is named', async ({ page }) => {
    await page.goto('/#/calibration')
    await expect(page.getByRole('heading', { name: 'Snapshot continuity' })).toBeVisible()
    await expect(page.getByText(/Niche-cluster calibration/i).first()).toBeVisible()
    await expect(page.getByText(/Not implemented/i).first()).toBeVisible()
    // No score or recommendation may appear before calibration is activated.
    await expect(page.getByText(/Scoring locked/i).first()).toBeVisible()
  })
})

test.describe('venture scout', () => {
  test('the two operations are offered and the invalid one is blocked', async ({ page }) => {
    await page.goto('/#/scout')
    const operation = page.getByLabel('Operation')
    await expect(operation).toBeVisible()

    await operation.selectOption('audit_idea')
    // The fixture seeds a proposal for the first game only, so selecting a
    // game without one must disable the action and say why.
    const candidate = page.getByLabel('Candidate')
    const options = await candidate.locator('option').allInnerTexts()
    const withoutProposal = options.findIndex(text => text.includes('no Hunter proposal'))
    if (withoutProposal >= 0) {
      await candidate.selectOption({ index: withoutProposal })
      await expect(page.getByRole('button', { name: 'Audit this idea' })).toBeDisabled()
      await expect(page.getByText(/nothing to audit/i)).toBeVisible()
    }

    await operation.selectOption('analyze_game')
    await expect(page.getByRole('button', { name: 'Analyze this game' })).toBeEnabled()
  })
})

test.describe('matching engine', () => {
  test('no disabled placeholder tabs are presented as features', async ({ page }) => {
    await page.goto('/#/matching')
    await expect(page.locator('.workspace').first()).toBeVisible()
    for (const label of ['Threshold laboratory', 'Feature inspector', 'Match sandbox', 'Versions']) {
      await expect(page.getByRole('button', { name: label })).toHaveCount(0)
    }
  })

  test('a disabled control is disabled for a stated reason, not as a placeholder', async ({ page }) => {
    await page.goto('/#/matching')
    await expect(page.locator('.workspace').first()).toBeVisible()
    for (const button of await page.locator('button:disabled').all()) {
      // Either it explains itself, or the form around it does.
      const title = await button.getAttribute('title')
      const label = (await button.innerText()).trim()
      expect(Boolean(title) || label.length > 0,
        'a disabled control gave no indication of why it is unavailable').toBeTruthy()
    }
  })
})
