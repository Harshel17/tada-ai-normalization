import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

// Run with `npm run e2e:prod`: the PRODUCTION build served by `vite preview`, with no dev server and no dev-only middleware.

const DRY = 'DRY RUN — No data has been sent to the destination provider.'
const RAW_SECRETS = ['123-45-6789', '987-65-4321', '900-44-3333', '912-34-6789', '000123456789', '998877665544', '5550001111', '4400112233', '000999888777']
const view = (page: Page) => page.locator('[data-view]:not([hidden])')

/** Records everything the page requests so a test can prove what it did NOT touch. */
function watch(page: Page) {
  const urls: string[] = []
  page.on('request', (r) => urls.push(r.url()))
  return urls
}

test.describe.configure({ mode: 'serial' })

test('the app is the production bundle: no dev-server client, no source modules, no fixture middleware', async ({ page }) => {
  const urls = watch(page)
  await page.goto('/#/normalize')
  await expect(view(page).getByRole('heading', { name: 'Three payroll providers. One consistent data model.' })).toBeVisible()
  const scripts = await page.locator('script[src]').evaluateAll((els) => els.map((e) => (e as HTMLScriptElement).src))
  expect(scripts.length).toBeGreaterThan(0)
  for (const src of scripts) expect(new URL(src).pathname).toMatch(/^\/assets\/.+\.js$/)
  for (const u of urls) expect(u).not.toMatch(/@vite|\/src\/|__fixtures|\/@fs\//)
})

for (const [id, label] of [['prismhr', 'PrismHR'], ['isolved', 'isolved'], ['ukg-ready', 'UKG Ready']] as const) {
  test(`${label}: the one-click example works in the production build`, async ({ page }) => {
    const urls = watch(page)
    await page.goto('/#/normalize')
    await view(page).getByTestId(`quick-${id}`).click()
    const results = view(page).getByTestId('results')
    await expect(results).toBeVisible()
    await expect(results.getByTestId('overview')).toContainText('Records produced')
    await expect(results.getByTestId('overview').locator('p.text-2xl').first()).not.toHaveText('0')
    await expect(view(page).getByTestId('file-list').getByRole('listitem').first()).toBeVisible()
    const text = await page.locator('body').innerText()
    for (const secret of RAW_SECRETS) expect(text).not.toContain(secret)
    // the example files came from the bundle (static chunks), never from a dev middleware or an API endpoint
    expect(urls.some((u) => /__fixtures/.test(u))).toBe(false)
    expect(urls.filter((u) => /\/api\//.test(u)).every((u) => /\/api\/(detect|convert)\//.test(u))).toBe(true)
    expect(urls.every((u) => new URL(u).hostname === '127.0.0.1')).toBe(true)
  })
}

test('the migration preview stays usable from a one-click example in the production build', async ({ page }) => {
  const migrate: string[] = []
  page.on('request', (r) => { if (/migrate/.test(r.url())) migrate.push(`${r.method()} ${new URL(r.url()).pathname}`) })
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-ukg-ready').click()
  const panel = view(page).getByTestId('migration')
  await expect(panel).toBeVisible()
  await expect(panel.getByTestId('dry-run')).toHaveText(DRY)
  const button = panel.getByRole('button', { name: 'Preview migration to UKG Ready' })
  await expect(button).toBeDisabled()
  await panel.getByLabel(/Destination company ID/).fill('4410')
  await panel.getByRole('button', { name: /Suggestion: the source username “mdiaz”/ }).click()
  await expect(button).toBeDisabled() // the source username must be confirmed first
  await panel.getByRole('checkbox', { name: /I confirm/ }).check()
  await button.click()
  const readiness = panel.getByTestId('mig-readiness')
  await expect(readiness).toBeVisible()
  await expect(readiness).toContainText('does not mean UKG Ready will accept')
  await expect(panel.getByTestId('pane-source')).toContainText('mdiaz')
  await expect(panel.getByTestId('pane-destination')).toContainText('"username": "mdiaz"')
  await expect(panel.getByTestId('mig-not-migrated')).toContainText('ssn_last4')
  const text = await page.locator('body').innerText()
  for (const secret of RAW_SECRETS) expect(text).not.toContain(secret)
  expect(migrate).toEqual(['POST /api/migrate/preview/ukg-ready'])
})

test('the production build shows readable relationship labels and the address-country action', async ({ page }) => {
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-prismhr').click()
  const rel = view(page).getByTestId('relationships')
  await rel.getByRole('button', { name: /how these records connect/ }).click()
  await expect(rel.getByRole('list', { name: 'Record relationships' })).toContainText('Direct deposit #1')
  await expect(view(page).getByTestId('overview')).toContainText('1 company')
  const panel = view(page).getByTestId('migration')
  await panel.getByLabel(/Destination company ID/).fill('4410')
  await panel.getByLabel(/Username in UKG Ready/).fill('prod.test')
  await panel.getByRole('button', { name: 'Preview migration to UKG Ready' }).click()
  await panel.getByRole('button', { name: 'Enter the address country' }).first().click()
  await expect(panel.getByLabel(/Address country/)).toBeFocused()
})

test('the built files carry only the synthetic examples: no live-shape data, no credentials, no .env content', async () => {
  const dist = path.resolve(process.cwd(), 'dist') // playwright runs from frontend/
  const files: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      if (e.isDirectory()) walk(path.join(dir, e.name))
      else files.push(path.join(dir, e.name))
    }
  }
  walk(dist)
  expect(files.length).toBeGreaterThan(0)
  const all = files.filter((f) => /\.(js|css|html|json)$/.test(f)).map((f) => readFileSync(f, 'utf8')).join('\n')
  for (const forbidden of ['live_shapes', 'DEMO_SIGNIN', 'ACCOUNT_HASH_SALT', 'sessionId', 'Bearer ']) expect(all).not.toContain(forbidden)
  expect(files.some((f) => /live|\.env|\.pem|\.key$/i.test(path.basename(f)))).toBe(false)
  // the examples ARE bundled: one of them is found by its synthetic company id
  expect(all).toContain('ACME')
})
