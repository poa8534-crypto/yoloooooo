import { defineConfig, devices } from '@playwright/test'

// Browser tests run against an isolated ledger, never the operator's. The
// fixture server creates a temporary database and artifact directory, points
// the model endpoint at a local stub, and starts with no API credentials, so
// actions that change data are safe to exercise and nothing spends quota.
const PORT = 8799
const CHANNEL = process.env.PLAYWRIGHT_CHANNEL || 'chrome'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
  },
  // Uses the Chrome already installed on the machine rather than downloading a
  // separate browser build. Override with PLAYWRIGHT_CHANNEL if Chrome is not
  // present and Playwright's own Chromium has been installed instead.
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], channel: CHANNEL, viewport: { width: 1440, height: 900 } } },
    { name: 'tablet', use: { ...devices['Desktop Chrome'], channel: CHANNEL, viewport: { width: 900, height: 1000 } } },
    { name: 'narrow', use: { ...devices['Desktop Chrome'], channel: CHANNEL, viewport: { width: 420, height: 880 } } },
  ],
  webServer: {
    // cwd is the repository root, so the interpreter path resolves from there.
    command: `.venv\\Scripts\\python.exe -m scripts.e2e_fixture --port ${PORT}`,
    cwd: '..',
    // The liveness probe, which never needs a credential.
    url: `http://127.0.0.1:${PORT}/api/health/live`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'pipe',
  },
})
