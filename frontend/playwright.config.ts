import { defineConfig } from '@playwright/test'

// End-to-end tests run against the REAL stack: FastAPI + PostgreSQL + the Vite dev server.
// They use their own ports so they never collide with a server you already have running.
const API_PORT = Number(process.env.E2E_API_PORT ?? 8010)
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 5180)
// A separate, freshly migrated database (the name must end in _e2e). Your development data is never touched.
const DB_URL = process.env.E2E_DATABASE_URL ?? 'postgresql+psycopg://tada:tada_dev_password@localhost:54329/tada_normalize_e2e'

export default defineConfig({
  testDir: './e2e',
  testIgnore: /production\.spec\.ts/, // that file runs against the production build: `npm run e2e:prod`
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: 'retain-on-failure' },
  webServer: [
    {
      command: `uv run python frontend/e2e/prepare_db.py && uv run uvicorn normalization.api.app:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: '..',
      env: { DATABASE_URL: DB_URL, E2E_DATABASE_URL: DB_URL },
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'npx vite',
      env: { API_URL: `http://127.0.0.1:${API_PORT}`, CONSOLE_PORT: String(WEB_PORT) },
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
