import { defineConfig } from '@playwright/test'

// Browser tests against the PRODUCTION build (`vite build` + `vite preview`), not the dev server: FastAPI + PostgreSQL + the built app.
// Ports and database are the same separate ones the other browser tests use, so nothing you have running is touched.
const API_PORT = Number(process.env.E2E_API_PORT ?? 8010)
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 5180)
const DB_URL = process.env.E2E_DATABASE_URL ?? 'postgresql+psycopg://tada:tada_dev_password@localhost:54329/tada_normalize_e2e'

export default defineConfig({
  testDir: './e2e',
  testMatch: /production\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
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
      command: `npm run build && npx vite preview --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      env: { API_URL: `http://127.0.0.1:${API_PORT}` },
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
})
