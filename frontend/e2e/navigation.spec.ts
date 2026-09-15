import { expect, test } from '@playwright/test'

// Navigation, entity identity and browser history. The audit found that Home's
// candidate "Inspect" opened the general list rather than the clicked game, and
// that nothing survived a refresh, so these pin the entity as well as the page.

// Labels as the sidebar actually renders them.
const PAGES = [
  ['Home', 'home'],
  ['Ideas and game dossiers', 'ideas'],
  ['Sources', 'sources'],
  ['Agent History', 'history'],
  ['Matching Engine', 'matching'],
  ['Meta Hunter', 'meta'],
  ['Venture Scout', 'scout'],
  ['Collection & Calibration', 'calibration'],
  ['System Health', 'health'],
] as const

const title = (page: import('@playwright/test').Page) => page.locator('.page-title h1')

test.describe('navigation', () => {
  for (const [label, slug] of PAGES) {
    test(`${label} opens and is addressable`, async ({ page }) => {
      await page.goto('/')
      await page.locator('nav[aria-label="Main navigation"] button', { hasText: label }).first().click()
      await expect(page).toHaveURL(new RegExp(`#/${slug}`))
      await expect(title(page)).toHaveText(label)
      // Every page renders its own workspace, not a blank shell.
      await expect(page.locator('.workspace').first()).toBeVisible()
    })
  }

  test('a page survives a reload', async ({ page }) => {
    await page.goto('/#/history')
    await expect(title(page)).toHaveText('Agent History')
    await page.reload()
    await expect(page).toHaveURL(/#\/history/)
    await expect(title(page)).toHaveText('Agent History')
  })

  test('Back and Forward move between pages', async ({ page }) => {
    await page.goto('/#/sources')
    await page.locator('nav[aria-label="Main navigation"] button', { hasText: 'System Health' }).first().click()
    await expect(page).toHaveURL(/#\/health/)
    await page.goBack()
    await expect(page).toHaveURL(/#\/sources/)
    await page.goForward()
    await expect(page).toHaveURL(/#\/health/)
  })

  test('opening a game keeps that exact game through a reload', async ({ page }) => {
    await page.goto('/#/ideas')
    const card = page.locator('.ideas-list button').first()
    const name = (await card.locator('h3').innerText()).trim()
    await card.click()

    await expect(page).toHaveURL(/candidate=/)
    const url = page.url()
    await expect(page.locator('.brief-hero')).toContainText(name)

    await page.reload()
    expect(page.url()).toBe(url)
    await expect(page.locator('.brief-hero')).toContainText(name)
  })

  test('an unknown route falls back to Home rather than a blank page', async ({ page }) => {
    await page.goto('/#/not-a-page')
    await expect(page.locator('.workspace').first()).toBeVisible()
    await expect(title(page)).toHaveText('Home')
  })

  test('the current page is marked for assistive technology', async ({ page }) => {
    await page.goto('/#/sources')
    const current = page.locator('nav[aria-label="Main navigation"] button[aria-current="page"]')
    await expect(current).toHaveCount(1)
    await expect(current).toHaveText(/Sources/)
  })
})
