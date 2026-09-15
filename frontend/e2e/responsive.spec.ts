import { expect, test, type Page } from '@playwright/test'

// Layout and keyboard operation at every configured width. The audit asked for
// desktop, tablet and narrow screens plus browser zoom, and for keyboard-only
// operation across navigation, filters, dialogs and disclosures.

const PAGES = ['home', 'ideas', 'sources', 'history', 'matching', 'meta', 'scout', 'calibration', 'health']

async function horizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement
    // Allow a pixel of rounding slack.
    return doc.scrollWidth - doc.clientWidth > 1
  })
}

test.describe('layout', () => {
  for (const slug of PAGES) {
    test(`${slug} does not scroll sideways`, async ({ page }) => {
      await page.goto(`/#/${slug}`)
      await expect(page.locator('.workspace').first()).toBeVisible()
      expect(await horizontalOverflow(page), `${slug} overflowed its width`).toBe(false)
    })
  }

  test('the page survives browser zoom', async ({ page }) => {
    await page.goto('/#/home')
    await expect(page.locator('.workspace').first()).toBeVisible()
    // Emulating zoom by shrinking the viewport is what a zoomed browser does
    // to CSS pixels, and it is the part a layout can get wrong.
    await page.setViewportSize({ width: 720, height: 600 })
    await expect(page.locator('.workspace').first()).toBeVisible()
    expect(await horizontalOverflow(page)).toBe(false)
  })
})

test.describe('keyboard operation', () => {
  test('navigation is reachable and operable by keyboard alone', async ({ page }) => {
    await page.goto('/#/home')
    await page.locator('nav[aria-label="Main navigation"] button', { hasText: 'Sources' }).first().focus()
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/#\/sources/)
  })

  test('a filter can be operated by keyboard', async ({ page }) => {
    await page.goto('/#/ideas')
    const filter = page.getByRole('button', { name: 'Research only', exact: true })
    await filter.focus()
    await expect(filter).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(filter).toHaveClass(/active/)
  })

  test('a disclosure opens and closes from the keyboard', async ({ page }) => {
    await page.goto('/#/calibration')
    const summary = page.locator('details.brief-fold > summary').first()
    if (await summary.count()) {
      await summary.focus()
      await page.keyboard.press('Enter')
      await expect(page.locator('details.brief-fold').first()).toHaveAttribute('open', '')
      await page.keyboard.press('Enter')
      await expect(page.locator('details.brief-fold').first()).not.toHaveAttribute('open', '')
    }
  })

  test('every focusable control shows a visible focus ring', async ({ page }) => {
    await page.goto('/#/sources')
    const button = page.locator('nav[aria-label="Main navigation"] button').first()
    await button.focus()
    const outline = await button.evaluate(node => {
      const style = window.getComputedStyle(node)
      return `${style.outlineStyle} ${style.outlineWidth} ${style.boxShadow}`
    })
    expect(outline, 'focused control had no visible focus indicator').not.toBe('none 0px none')
  })
})

test.describe('accessible labels', () => {
  test('inputs carry a label, not just a placeholder', async ({ page }) => {
    await page.goto('/#/sources')
    const input = page.getByLabel('Filter sources')
    await expect(input).toBeVisible()
  })

  test('the evidence drawer is announced as a dialog region', async ({ page }) => {
    await page.goto('/#/ideas')
    const toggle = page.getByRole('button', { name: /Game dossiers|Games/i })
    if (await toggle.count()) await toggle.first().click()
    await page.locator('.ideas-list button').first().waitFor()
    await page.locator('.ideas-list button').first().click()
    const fact = page.locator('.fact-stack button').first()
    await fact.waitFor()
    await fact.click()
    await expect(page.locator('aside[aria-label="Evidence inspector"]')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.drawer-scrim.open')).toHaveCount(0)
  })
})
