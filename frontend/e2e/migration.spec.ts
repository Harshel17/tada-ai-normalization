import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

// One-click examples, the result summary and the UKG Ready migration preview, against the REAL stack.

const RAW_SECRETS = ['123-45-6789', '987-65-4321', '900-44-3333', '912-34-6789', '000123456789', '998877665544', '5550001111', '4400112233', '000999888777']
const DRY = 'DRY RUN — No data has been sent to the destination provider.'
const view = (page: Page) => page.locator('[data-view]:not([hidden])')

async function noSecrets(page: Page) {
  const text = await page.locator('body').innerText()
  for (const secret of RAW_SECRETS) expect(text).not.toContain(secret)
}

test.describe.configure({ mode: 'serial' })

for (const [id, label] of [['prismhr', 'PrismHR'], ['isolved', 'isolved'], ['ukg-ready', 'UKG Ready']] as const) {
  test(`${label}: one click gives normalized results and a summary computed from them`, async ({ page }) => {
    await page.goto('/#/normalize')
    await expect(view(page).getByRole('heading', { name: 'Three payroll providers. One consistent data model.' })).toBeVisible()
    await view(page).getByTestId(`quick-${id}`).click()
    const results = view(page).getByTestId('results')
    await expect(results).toBeVisible()
    const overview = results.getByTestId('overview')
    await expect(overview).toBeVisible()
    for (const stat of ['Records produced', 'Mapped fields', 'Preserved, not mapped', 'Relationships', 'Integrity checks', 'Protected values']) await expect(overview).toContainText(stat)
    await expect(overview.locator('p.text-2xl').first()).not.toHaveText('0')
    await expect(results.getByTestId('migration')).toBeVisible()
    await expect(results.getByTestId('dry-run').first()).toHaveText(DRY)
    await noSecrets(page)
  })
}

test('PrismHR: the relationships view lists real references from the paychecks', async ({ page }) => {
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-prismhr').click()
  const rel = view(page).getByTestId('relationships')
  await expect(rel).toBeVisible()
  await rel.getByRole('button', { name: /how these records connect/ }).click()
  const list = rel.getByRole('list', { name: 'Record relationships' })
  await expect(list.getByRole('listitem').first()).toContainText('→')
  await expect(list).toContainText('found')
})

test('migration preview from the results: BLOCKED without a username, then READY or NEEDS_REVIEW once one is given', async ({ page }) => {
  const migrate: string[] = []
  const foreign: string[] = []
  page.on('request', (r) => {
    const u = new URL(r.url())
    if (!['127.0.0.1', 'localhost'].includes(u.hostname)) foreign.push(r.url())
    if (/migrate/.test(u.pathname)) migrate.push(`${r.method()} ${u.pathname}`)
  })
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-prismhr').click()
  const panel = view(page).getByTestId('migration')
  await expect(panel).toBeVisible()
  await expect(panel.getByTestId('dry-run')).toHaveText(DRY)
  const button = panel.getByRole('button', { name: 'Preview migration to UKG Ready' })
  await expect(button).toBeDisabled()
  await expect(panel.getByTestId('mig-problem')).toContainText('destination company ID')

  await panel.getByLabel(/Destination company ID/).fill('4410')
  await button.click()
  const readiness = panel.getByTestId('mig-readiness')
  await expect(readiness).toHaveAttribute('data-readiness', 'BLOCKED')
  await expect(panel.getByTestId('mig-requirements')).toContainText('username')
  await expect(panel.getByTestId('mig-issues')).toContainText('username is required')

  await panel.getByLabel(/Username in UKG Ready/).fill('e2e.reviewer')
  await expect(panel.getByTestId('mig-result')).toHaveCount(0) // an edit clears the stale preview
  await button.click()
  await expect(readiness).not.toHaveAttribute('data-readiness', 'BLOCKED')
  await expect(readiness).toContainText('does not mean UKG Ready will accept')
  await expect(readiness).toContainText('POST /v2/companies/4410/employees (not sent)')
  await expect(panel.getByTestId('pane-source')).toContainText('"id"')
  await expect(panel.getByTestId('pane-canonical')).toContainText('first_name')
  await expect(panel.getByTestId('pane-destination')).toContainText('"username": "e2e.reviewer"')
  await expect(panel.getByTestId('mig-not-migrated')).toContainText('ssn_last4')
  await expect(panel.getByTestId('mig-not-migrated')).toContainText('compensation')
  await expect(panel.getByTestId('mig-fields')).toContainText('first_name')
  await noSecrets(page)
  expect(migrate).toEqual(['POST /api/migrate/preview/ukg-ready', 'POST /api/migrate/preview/ukg-ready'])
  expect(foreign).toEqual([]) // the browser never talks to any other host
})

test('migration preview: source ids go only to their documented fields and only after being ticked', async ({ page }) => {
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-isolved').click()
  const panel = view(page).getByTestId('migration')
  await expect(panel).toBeVisible()
  await panel.getByLabel(/Destination company ID/).fill('88')
  await panel.getByLabel(/Username in UKG Ready/).fill('someone')
  await panel.getByRole('button', { name: 'Preview migration to UKG Ready' }).click()
  const body = panel.getByTestId('pane-destination')
  await expect(body).toBeVisible()
  await expect(body).not.toContainText('employee_id')
  await expect(body).not.toContainText('external_id')
  await panel.getByRole('checkbox', { name: /source record id/ }).check()
  await panel.getByRole('button', { name: 'Preview migration to UKG Ready' }).click()
  await expect(panel.getByTestId('pane-destination')).toContainText('"external_id"')
  await expect(panel.getByTestId('pane-destination')).not.toContainText('"id":') // UKG assigns its own id
})

test('UKG Ready source: the source username is only a suggestion until confirmed', async ({ page }) => {
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-ukg-ready').click()
  const panel = view(page).getByTestId('migration')
  await expect(panel).toBeVisible()
  await panel.getByLabel(/Destination company ID/).fill('12')
  const suggestion = panel.getByRole('button', { name: /Suggestion: the source username “mdiaz”/ })
  await expect(suggestion).toBeVisible()
  await suggestion.click()
  const button = panel.getByRole('button', { name: 'Preview migration to UKG Ready' })
  await expect(button).toBeDisabled()
  await expect(panel.getByTestId('mig-problem')).toContainText('source system')
  await panel.getByRole('checkbox', { name: /I confirm/ }).check()
  await expect(button).toBeEnabled()
  await button.click()
  await expect(panel.getByTestId('pane-destination')).toContainText('"username": "mdiaz"')
  await noSecrets(page)
})

test('a keyboard user can reach and operate the preview form', async ({ page }) => {
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-prismhr').click()
  const panel = view(page).getByTestId('migration')
  await expect(panel).toBeVisible()
  await panel.getByLabel('Employee', { exact: true }).focus()
  await page.keyboard.press('Tab')
  await expect(panel.getByLabel(/Destination company ID/)).toBeFocused()
  await page.keyboard.type('4410')
  await page.keyboard.press('Tab')
  await page.keyboard.type('kb.user')
  await panel.getByRole('button', { name: 'Preview migration to UKG Ready' }).focus()
  await page.keyboard.press('Enter')
  await expect(panel.getByTestId('mig-readiness')).toBeVisible()
})

test('the address-country action reveals and focuses the field; the reviewer supplies the value; readiness rules are unchanged', async ({ page }) => {
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-prismhr').click()
  const panel = view(page).getByTestId('migration')
  await panel.getByLabel(/Destination company ID/).fill('4410')
  await panel.getByLabel(/Username in UKG Ready/).fill('country.test')
  const button = panel.getByRole('button', { name: 'Preview migration to UKG Ready' })
  await button.click()
  await expect(panel.getByTestId('mig-issues')).toContainText('requires a country')
  await expect(panel.getByTestId('mig-readiness')).toHaveAttribute('data-readiness', 'NEEDS_REVIEW')
  await expect(panel.getByTestId('pane-destination')).not.toContainText('"address"')
  await expect(panel.getByLabel(/Address country/)).toBeHidden() // collapsed
  await panel.getByRole('button', { name: 'Enter the address country' }).first().click()
  await expect(panel.getByLabel(/Address country/)).toBeVisible()
  await expect(panel.getByLabel(/Address country/)).toBeFocused()
  await expect(panel.getByLabel(/Address country/)).toHaveValue('') // never invented
  await page.keyboard.type('USA')
  await button.click()
  await expect(panel.getByTestId('pane-destination')).toContainText('"country": "USA"')
  await expect(panel.getByTestId('mig-fields')).toContainText('you supplied')
  await expect(panel.getByTestId('dry-run')).toHaveCount(1) // one banner for the whole panel
})

test('relationships use readable labels and never show deposit identifiers', async ({ page }) => {
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-prismhr').click()
  const rel = view(page).getByTestId('relationships')
  await rel.getByRole('button', { name: /how these records connect/ }).click()
  const list = rel.getByRole('list', { name: 'Record relationships' })
  await expect(list).toContainText('Direct deposit #1')
  await expect(list).toContainText('Employee Jordan Rivera')
  await expect(list).not.toContainText('111000025')
  await rel.getByRole('checkbox', { name: /exact record ids/ }).check()
  await expect(list).toContainText('id withheld')
  await expect(list).toContainText('E1001')
  await expect(list).not.toContainText('111000025')
  await expect(view(page).getByTestId('overview')).not.toContainText('companie,')
})

test('isolved example: the documented owner is used, shown as documented and as not verified; embedded deposits keep their owner', async ({ page }) => {
  await page.goto('/#/normalize')
  await view(page).getByTestId('quick-isolved').click()
  const note = view(page).getByTestId('ownership-notice')
  await expect(note).toBeVisible()
  await expect(note).toContainText('used by this example set')
  await expect(note).toContainText('assigned to employee 90001')
  await expect(note).toContainText('not something the files or the integrity checks can verify')
  // the overlap is described as source occurrences of one logical record, not a double upload
  const integrity = view(page).getByTestId('integrity-panel')
  await expect(integrity).toContainText('logical record')
  await expect(integrity).toContainText('does not mean a file was uploaded twice')
  await expect(integrity).not.toContainText('probably added twice')
})

test('isolved example set loaded by hand: the owner needs an explicit confirmation and a different employee is only an acknowledgment', async ({ page }) => {
  await page.goto('/#/normalize')
  const flow = view(page).getByTestId('flow')
  await flow.getByRole('radio', { name: /isolved/ }).click()
  await flow.getByRole('button', { name: 'Try example data' }).click()
  const field = flow.getByTestId('owner-field')
  await expect(field).toBeVisible()
  await expect(field.getByTestId('owner-explanation')).toContainText('does not identify the employee who owns these direct deposits')
  await expect(field.getByTestId('owner-candidates')).toContainText('2 employees appear in your files')
  await expect(field.getByTestId('owner-documented')).toContainText('90001')
  await expect(field.getByLabel(/Employee ID/)).toHaveValue('')
  const box = flow.getByTestId('context-box')
  await box.getByRole('button', { name: /Use 4501/ }).first().click()
  await field.getByRole('button', { name: /Use 90002/ }).click()
  await expect(flow.getByRole('button', { name: 'Normalize Data' })).toBeDisabled()
  await field.getByRole('checkbox').check()
  await flow.getByRole('button', { name: 'Normalize Data' }).click()
  const note = flow.getByTestId('ownership-notice')
  await expect(note).toContainText('you confirmed')
  await expect(note).toContainText('assigned to employee 90002')
  // the embedded deposit still belongs to 90001
  await flow.getByTestId('relationships').getByRole('button', { name: /how these records connect/ }).click()
  await flow.getByTestId('relationships').getByRole('checkbox', { name: /exact record ids/ }).check()
  const list = flow.getByTestId('relationships').getByRole('list', { name: 'Record relationships' })
  await expect(list).toContainText('→ 90002') // the bare deposits took the acknowledged owner
  await expect(list).toContainText('→ 90001') // the embedded deposit kept its own
})

test('no API response the browser receives ever carries account_number_hash or a hash-shaped value', async ({ page }) => {
  const bodies: string[] = []
  page.on('response', async (r) => {
    if (new URL(r.url()).pathname.startsWith('/api/')) bodies.push(await r.text().catch(() => ''))
  })
  for (const id of ['prismhr', 'isolved', 'ukg-ready']) {
    await page.goto('/#/normalize')
    await view(page).getByTestId(`quick-${id}`).click()
    const panel = view(page).getByTestId('migration')
    await expect(panel).toBeVisible()
    await panel.getByLabel(/Destination company ID/).fill('4410')
    await panel.getByLabel(/Username in UKG Ready/).fill('hash.check')
    await panel.getByRole('button', { name: 'Preview migration to UKG Ready' }).click()
    await expect(panel.getByTestId('mig-readiness')).toBeVisible()
  }
  expect(bodies.length).toBeGreaterThanOrEqual(9) // detect + convert + preview, three providers
  const all = bodies.join('\n')
  expect(all).not.toContain('account_number_hash')
  expect(all).not.toMatch(/(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])/)
  expect(await page.locator('body').innerText()).not.toMatch(/[0-9a-f]{40,}/)
})

for (const [id, label] of [['prismhr', 'PrismHR'], ['isolved', 'isolved'], ['ukg-ready', 'UKG Ready']] as const) {
  test(`${label}: every converted group is informative while collapsed, shows no account digits, and opens to the full details`, async ({ page }) => {
    await page.goto('/#/normalize')
    await view(page).getByTestId(`quick-${id}`).click()
    const cards = view(page).locator('li[data-testid^="unit-"]')
    await expect(cards.first()).toBeVisible()
    const n = await cards.count()
    expect(n).toBeGreaterThan(0)
    for (let i = 0; i < n; i++) {
      const card = cards.nth(i)
      await expect(card.getByLabel('Summary')).toBeVisible() // statistics, before anything is opened
      await expect(card).toContainText('Source:')
      await expect(card.locator('summary').filter({ hasText: 'What we found' })).toBeVisible()
      const collapsed = ((await card.getByLabel('Summary').innerText()) + (await card.getByTestId('unit-preview').allInnerTexts()).join(' '))
      if ((await card.getAttribute('data-testid'))!.includes('direct_deposit')) expect(collapsed).not.toMatch(/\*{2,}|\d{5,}/)
    }
    const first = view(page).locator('[data-testid^="found-"]').nth(1)
    await first.locator('summary').click()
    await expect(first).toHaveAttribute('open', '')
    await expect(first.getByTestId('readable-summary')).toBeVisible()
    await expect(view(page).getByTestId('integrity-panel').getByRole('heading', { name: 'Data quality & integrity' })).toBeVisible()
  })
}
