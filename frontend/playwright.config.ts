import { defineConfig, devices } from '@playwright/test'

// End-to-end tests against the real backend and the real KataGo engine.
// Prerequisites: frontend built (npm run build), KataGo + a network installed
// (Mac: ./scripts/setup_mac.sh; Linux/CI: ./scripts/fetch_ci_engine.sh).
const PORT = Number(process.env.E2E_PORT || 8765)

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 60_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    // iPhone-sized viewport with touch; Chromium engine (WebKit/Safari must be checked on a device).
    { name: 'iphone-13', use: { ...devices['iPhone 13'], browserName: 'chromium' } },
    { name: 'iphone-se', use: { ...devices['iPhone SE'], browserName: 'chromium' } },
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: `uv run gobot serve --port ${PORT}`,
    cwd: '..',
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { GOBOT_MODEL: process.env.GOBOT_MODEL || 'b18c384-conv' },
  },
})
