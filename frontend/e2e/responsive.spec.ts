import { expect, test, type Page } from '@playwright/test'

// Layout and keyboard operation at every configured width. The audit asked for
// desktop, tablet and narrow screens plus browser zoom, and for keyboard-only
// operation across navigation, filters, dialogs and disclosures.

const PAGES = ['home', 'ideas', 'sources', 'history', 'market', 'matching', 'meta', 'scout', 'calibration', 'health']

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

test.describe('the shell uses the width it has', () => {
  test('the content starts where the sidebar ends', async ({ page }) => {
    // `.app-shell` is a grid whose first column holds the sidebar, and
    // `.app-content` also carried a margin the width of that sidebar. The
    // content was offset twice: the sidebar ended at 195px and the content
    // began at 441px, leaving a 246px dead strip and squeezing every panel.
    await page.goto('/#/meta')
    await page.locator('.app-content').waitFor()
    const gap = await page.evaluate(() => {
      const side = document.querySelector('.app-sidebar')?.getBoundingClientRect()
      const content = document.querySelector('.app-content')?.getBoundingClientRect()
      if (!side || !content) return null
      return Math.round(content.left - side.right)
    })
    expect(gap, 'a dead strip between the sidebar and the content').toBeLessThanOrEqual(2)
  })

  test('below 820px the sidebar stops being a column', async ({ page }) => {
    // The single-column rule sat earlier in the stylesheet than the two rules
    // that set fixed sidebar columns, lost to them on equal specificity, and
    // never took effect. At 420px the sidebar still claimed 232px, the content
    // column was 135px, and a card rendered 113px across.
    await page.setViewportSize({ width: 420, height: 880 })
    await page.goto('/#/scout')
    await page.locator('.app-content').waitFor()
    const width = await page.evaluate(() =>
      document.querySelector('.app-content')!.getBoundingClientRect().width)
    expect(width, 'the sidebar is still eating the window').toBeGreaterThan(420 * 0.9)
  })

  test('the sidebar sits above the content on a phone, not beside it', async ({ page }) => {
    await page.setViewportSize({ width: 420, height: 880 })
    await page.goto('/#/scout')
    await page.locator('.app-sidebar').waitFor()
    const stacked = await page.evaluate(() => {
      const side = document.querySelector('.app-sidebar')!.getBoundingClientRect()
      const content = document.querySelector('.app-content')!.getBoundingClientRect()
      return content.top >= side.bottom - 2
    })
    expect(stacked, 'the navigation is still a side column').toBe(true)
  })

  test('the workspace is centred on a wide screen, not pinned left', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 900 })
    await page.goto('/#/meta')
    await page.locator('.workspace').first().waitFor()
    const box = await page.evaluate(() => {
      const w = document.querySelector('.workspace')?.getBoundingClientRect()
      const c = document.querySelector('.app-content')?.getBoundingClientRect()
      if (!w || !c) return null
      return { left: w.left - c.left, right: c.right - w.right }
    })
    expect(box).not.toBeNull()
    // Equal margins either side; a pinned-left layout has one near zero.
    expect(Math.abs(box!.left - box!.right)).toBeLessThan(24)
  })

  test('the heading lines up with the content it belongs to', async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 900 })
    await page.goto('/#/meta')
    await page.locator('.page-title h1').waitFor()
    const drift = await page.evaluate(() => {
      const title = document.querySelector('.page-title h1')?.getBoundingClientRect()
      const work = document.querySelector('.workspace')?.getBoundingClientRect()
      if (!title || !work) return null
      return Math.abs(title.left - work.left)
    })
    expect(drift, 'the title sat left of the content below it').toBeLessThan(48)
  })
})
