import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

// The Normalize flow and the Pipeline Explorer, against the REAL stack (FastAPI + PostgreSQL).

const RAW_SECRETS = ['123-45-6789', '987-65-4321', '900-44-3333', '000123456789', '998877665544', '5550001111', '4400112233', '000999888777']
const ui = (page: Page) => page.locator('[data-view]:not([hidden])')
const flow = (page: Page) => ui(page).getByTestId('flow')
const workspace = (label: string) => `e2e-flow-${label}-${Math.random().toString(36).slice(2, 7)}`

async function start(page: Page) {
  await page.goto('/#/normalize')
  await expect(flow(page)).toBeVisible()
}

async function loadSet(page: Page, provider: RegExp, name: string, files: number) {
  await flow(page).getByRole('radio', { name: provider }).click()
  await flow(page).getByRole('button', { name: 'Try example data' }).click()
  await expect(flow(page).getByTestId('file-list').getByRole('listitem')).toHaveCount(files)
  await expect(flow(page).getByTestId('context-box')).toBeVisible()
}

/** Confirm the suggested ids by clicking them: nothing is filled in on the user's behalf. */
async function confirmIds(page: Page, ids: string[]) {
  const box = flow(page).getByTestId('context-box')
  for (const id of ids) await box.getByRole('button', { name: new RegExp(`Use ${id}\\b`) }).first().click()
  // an employee id the files do not assign is an operator decision: acknowledge it explicitly
  const owner = flow(page).getByTestId('owner-field')
  if (await owner.count()) await owner.getByRole('checkbox').check()
}

async function expectNoSecrets(page: Page) {
  const text = await page.locator('body').innerText()
  for (const secret of RAW_SECRETS) expect(text).not.toContain(secret)
}

test.describe.configure({ mode: 'serial' })

test('opening the app lands on Normalize: one heading, three providers, no dashboard', async ({ page }) => {
  await page.goto('/')
  await expect(flow(page)).toBeVisible()
  await expect(flow(page).getByRole('heading', { name: 'Three payroll providers. One consistent data model.' })).toBeVisible()
  await expect(flow(page).getByRole('radio')).toHaveCount(3)
  const nav = page.getByRole('navigation', { name: 'Sections' })
  await expect(nav.getByRole('link')).toHaveText(['Normalize', 'Pipeline Explorer', 'Data Explorer', 'Test Runs'])
  const visible = await page.locator('body').innerText()
  for (const clutter of [/PostgreSQL/i, /API online/i, /database/i, /tenant/i, /fixture/i, /entity type/i, /canonical/i, /provenance/i]) expect(visible).not.toMatch(clutter)
  await expect(flow(page).getByRole('button', { name: 'Upload JSON' })).toHaveCount(0) // appears after choosing a provider
  await expect(flow(page).getByRole('button', { name: 'Normalize Data' })).toHaveCount(0)
  await flow(page).getByRole('radio', { name: /PrismHR/ }).click()
  await expect(flow(page).getByRole('button', { name: 'Upload JSON' })).toBeVisible()
  await expect(flow(page).getByRole('button', { name: 'Try example data' })).toBeVisible()
})

test('system status is still reachable, just not in the main navigation', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'System status' }).click()
  await expect(ui(page).getByRole('heading', { name: 'Overview' })).toBeVisible()
  await expect(ui(page).getByTestId('api-status')).toContainText('API online')
})

test('the Pipeline Explorer says what to do first when nothing has been converted', async ({ page }) => {
  await page.goto('/#/pipeline')
  await expect(ui(page).getByText('Nothing to explore yet')).toBeVisible()
  await ui(page).getByRole('link', { name: 'Go to Normalize' }).click()
  await expect(flow(page)).toBeVisible()
})

const PROVIDERS = [
  { re: /PrismHR/, name: 'PrismHR', files: 4, ids: ['ACME', 'E1001'], full: '$.employee[0].firstName', employeeFile: 'employee_get_employee.json', detect: ['2 employees', '3 direct deposits', 'inside the employee records'], paycheck: /2 paychecks/ },
  { re: /isolved/, name: 'isolved', files: 5, ids: ['4501', '90001'], full: '$.results[0].nameAddress.firstName', employeeFile: 'employees.json', detect: ['Legal-company fields', '2 employees'], paycheck: /1 paycheck/ },
  { re: /UKG Ready/, name: 'UKG Ready', files: 5, ids: ['CID1', '7770001'], full: '$.employee.first_name', employeeFile: 'employee_bundle.json', detect: ['Tada bundle'], paycheck: /2 paychecks/ },
]

for (const p of PROVIDERS) {
  test(`${p.name}: example set → detect → confirm ids → normalize → explore the pipeline`, async ({ page }) => {
    await start(page)
    await loadSet(page, p.re, p.name, p.files)

    // every file is recognised without the user naming it; detection explains itself
    for (const text of p.detect) await expect(flow(page).getByTestId('step-3')).toContainText(text)
    await expect(flow(page).getByTestId('step-3').getByText('Not recognised')).toHaveCount(0)
    await expect(flow(page).getByTestId('step-3').getByText('Recognised').first()).toBeVisible()

    // ids are suggested, never used until confirmed
    await expect(flow(page).getByRole('button', { name: 'Normalize Data' })).toBeDisabled()
    await expect(flow(page).getByTestId('blocked')).toContainText('Confirm:')
    await confirmIds(page, p.ids)
    await flow(page).getByLabel('Workspace name').fill(workspace(p.name.replace(/\W/g, '')))
    await flow(page).getByRole('button', { name: 'Normalize Data' }).click()

    const results = flow(page).getByTestId('results')
    await expect(results.getByTestId('headline')).toContainText(/^Converted /)
    await expect(results).not.toContainText('needs an identifier')
    await expect(results).not.toContainText('has errors')
    await expect(results).toContainText('Employee records')
    await expect(results).toContainText('Payroll run and paychecks')
    await expect(results.getByTestId('headline')).toContainText(p.paycheck)

    // data checks: four statuses, nothing failed on the example data, unsupported money rules not evaluated
    const panel = results.getByTestId('integrity-panel')
    await expect(panel).toBeVisible()
    let total = 0
    for (const status of ['passed', 'warning', 'failed', 'not_evaluated']) total += Number(await panel.getByTestId(`integrity-count-${status}`).innerText())
    expect(total).toBe(11)
    await expect(panel.getByTestId('integrity-count-failed')).toHaveText('0')
    await expect(panel.getByTestId('check-status-gross_to_net')).toHaveText('Not evaluated')
    const findings: Record<string, [string, string]> = {
      PrismHR: ['Passed', 'Passed'], isolved: ['Warning', 'Passed'], 'UKG Ready': ['Passed', 'Warning'],
    }  // [duplicate ids, employee links]: isolved's example files overlap; a UKG pay statement names an unconverted employee
    await expect(panel.getByTestId('check-status-duplicate_ids')).toHaveText(findings[p.name]![0])
    await expect(panel.getByTestId('check-status-employee_links')).toHaveText(findings[p.name]![1])
    await expect(panel.getByTestId('check-status-paycheck_run_link')).toHaveText('Passed')

    // readable results: every kind of record is described in plain words, and the first section opens by itself
    const cards = results.locator('[data-testid^="found-"]')
    expect(await cards.count()).toBeGreaterThanOrEqual(4)
    await expect(cards.first()).toHaveAttribute('open', '')
    const SECTION: Record<string, string> = {
      company: 'The employer', employee: 'Who this is', direct_deposit: 'How pay is split', deduction: 'The deduction', payroll: 'The pay run',
    }
    const seen = new Set<string>()
    for (let i = 0; i < (await cards.count()); i++) {
      const card = cards.nth(i)
      const kind = ((await card.getAttribute('data-testid')) ?? '').split('-').pop() ?? ''
      if ((await card.getAttribute('open')) === null) await card.locator('summary').first().click() // closed: open it
      await expect(card.getByTestId('record-heading')).not.toBeEmpty()
      await expect(card.getByRole('region', { name: SECTION[kind]! }).first()).toBeVisible()
      await expect(card.getByTestId('data-story')).toContainText('Filled in the common format')
      seen.add(kind)
    }
    expect([...seen].sort()).toEqual(['company', 'deduction', 'direct_deposit', 'employee', 'payroll'])
    await expectNoSecrets(page)

    // pipeline explorer: real source path → transformation → standard field
    await results.getByRole('link', { name: 'View Transformation Pipeline' }).click()
    const pipe = ui(page).getByTestId('pipeline')
    await ui(page).getByLabel('Choose converted data').selectOption({ label: `Employee records · ${p.employeeFile}` })
    const first = pipe.getByTestId('pipeline-row').filter({ hasText: 'first_name' }).first()
    await expect(first.getByTestId('origin').locator(`[title="${p.full}"]`)).toBeVisible() // the complete source path
    await expect(first).toContainText('Copied as text')
    const hire = pipe.getByTestId('pipeline-row').filter({ hasText: 'hire_date' }).first()
    await expect(hire).toContainText('Read as a date')
    const ssn = pipe.getByTestId('pipeline-row').filter({ hasText: 'ssn_last4' }).first()
    await expect(ssn.getByTestId('origin')).toContainText('[withheld]')
    await expect(pipe.getByTestId('unmapped')).toContainText('Source mapping not available')
    await expect(pipe.getByTestId('withheld')).toBeVisible()
    await expectNoSecrets(page)
  })
}

test('PrismHR pipeline: enum conversion shows the table, the raw code and the standard value', async ({ page }) => {
  await start(page)
  await loadSet(page, /PrismHR/, 'PrismHR', 4)
  await confirmIds(page, ['ACME', 'E1001'])
  await flow(page).getByRole('button', { name: 'Normalize Data' }).click()
  await flow(page).getByTestId('results').getByRole('link', { name: 'View Transformation Pipeline' }).click()
  const pipe = ui(page).getByTestId('pipeline')
  await ui(page).getByLabel('Choose converted data').selectOption({ label: 'Employee records · employee_get_employee.json' })
  const row = pipe.getByTestId('pipeline-row').filter({ hasText: 'employment_status' }).first()
  await expect(row).toContainText('statusClass')
  await expect(row).toContainText('EMPLOYMENT_STATUS')
  await row.getByRole('button').click() // expand for the full explanation
  await expect(row.getByTestId('row-details')).toContainText('Looked up in the EMPLOYMENT_STATUS table: “A” → “active”')
  // the search box and operation filter work on real rows
  await pipe.getByRole('searchbox').fill('hire')
  await expect(pipe.getByTestId('pipeline-row').first()).toContainText('hire')
  await pipe.getByRole('searchbox').fill('')
  await pipe.getByRole('group', { name: 'Filter by what was done' }).getByRole('button', { name: /Translated to a standard value/ }).click()
  await expect(pipe.getByTestId('pipeline-row').first()).toContainText('Translated to a standard value')
  // second employee
  await pipe.getByRole('group', { name: 'Filter by what was done' }).getByRole('button', { name: /^All \(/ }).click()
  await pipe.getByRole('tab', { name: /Casey/ }).click()
  await expect(pipe.getByTestId('pipeline-row').filter({ hasText: 'first_name' }).first()).toContainText('Casey')
})

test('saving is separate: normalizing stores nothing, saving says what happened and is idempotent', async ({ page }) => {
  const ws = workspace('save')
  await start(page)
  await loadSet(page, /isolved/, 'isolved', 5)
  await confirmIds(page, ['4501', '90001'])
  await flow(page).getByLabel('Workspace name').fill(ws)
  await flow(page).getByRole('button', { name: 'Normalize Data' }).click()
  const results = flow(page).getByTestId('results')
  await expect(results.getByTestId('headline')).toContainText('Converted')
  await expect(results.getByTestId('save-explainer')).toContainText('Normalizing does not save anything.')

  const before = await (await page.request.get('/api/stats')).json()
  expect(before.scopes.some((s: { tenant_id: string }) => s.tenant_id === ws)).toBe(false)

  await results.getByRole('button', { name: 'Save to Database' }).click()
  await expect(results.getByTestId('save-outcome')).toContainText('new employee record')
  await results.getByRole('button', { name: 'Save to Database' }).click()
  await expect(results.getByTestId('save-outcome')).toContainText('already saved, so nothing changed')
  const after = await (await page.request.get('/api/stats')).json()
  expect(after.scopes.find((s: { tenant_id: string; employees: number }) => s.tenant_id === ws)?.employees).toBeGreaterThan(0)

  await results.getByRole('link', { name: /Browse saved data/ }).click()
  await ui(page).getByRole('button', { name: 'Refresh' }).click()
  await ui(page).getByLabel('Provider', { exact: true }).selectOption({ label: 'isolved' })
  await ui(page).getByLabel('Tenant', { exact: true }).selectOption(ws)
  await expect(ui(page).getByTestId('employees-table')).toContainText('Alex')
})

test('missing ids block Normalize and nothing is guessed', async ({ page }) => {
  await start(page)
  await loadSet(page, /UKG Ready/, 'UKG Ready', 5)
  await expect(flow(page).getByLabel(/Employer ID/)).toHaveValue('')
  await expect(flow(page).getByLabel(/Employee ID/)).toHaveValue('')
  await expect(flow(page).getByRole('button', { name: 'Normalize Data' })).toBeDisabled()
  await flow(page).getByLabel(/Employer ID/).fill('CID1')
  await expect(flow(page).getByRole('button', { name: 'Normalize Data' })).toBeDisabled() // employee id still needed
  await flow(page).getByLabel(/Employee ID/).fill('7770001')
  await expect(flow(page).getByRole('button', { name: 'Normalize Data' })).toBeDisabled() // typing an owner is not confirming it
  await expect(flow(page).getByTestId('blocked')).toContainText('Confirm which employee owns')
  await flow(page).getByTestId('owner-field').getByRole('checkbox').check()
  await expect(flow(page).getByRole('button', { name: 'Normalize Data' })).toBeEnabled()
})

test('an ambiguous file asks for confirmation instead of guessing', async ({ page }) => {
  await start(page)
  await flow(page).getByRole('radio', { name: /PrismHR/ }).click()
  await flow(page).getByTestId('upload').setInputFiles({ name: 'mystery.json', mimeType: 'application/json', buffer: Buffer.from('[{"firstName": "Ann"}]') })
  const card = flow(page).getByTestId('detect-mystery.json')
  await expect(card).toContainText('Needs your confirmation')
  await card.getByRole('radio', { name: /Employee records/ }).click()
  await card.getByRole('button', { name: /Confirm this is/ }).click()
  await expect(card).toContainText('confirmed by you')
  await expect(card).toContainText('1 employee')
})

test('a file for another provider is flagged, not silently reinterpreted', async ({ page }) => {
  await start(page)
  await flow(page).getByRole('radio', { name: /PrismHR/ }).click()
  await flow(page).getByTestId('upload').setInputFiles({ name: 'eins.json', mimeType: 'application/json', buffer: Buffer.from('{"items":[{"id":1,"ein_tax_id":"12-3456789","ein_name":"X"}]}') })
  const card = flow(page).getByTestId('detect-eins.json')
  await expect(card).toContainText('Not recognised')
  await expect(card).toContainText('looks like UKG Ready')
  await expect(flow(page).getByRole('radio', { name: /PrismHR/ })).toBeChecked()
})

test('invalid and empty files are explained', async ({ page }) => {
  await start(page)
  await flow(page).getByRole('radio', { name: /isolved/ }).click()
  await flow(page).getByTestId('upload').setInputFiles([
    { name: 'broken.json', mimeType: 'application/json', buffer: Buffer.from('{not json') },
    { name: 'empty.json', mimeType: 'application/json', buffer: Buffer.from('[]') },
  ])
  await expect(flow(page).getByText('broken.json is not valid JSON')).toBeVisible()
  await expect(flow(page).getByTestId('detect-empty.json')).toContainText('Empty')
  await expect(flow(page).getByRole('button', { name: 'Normalize Data' })).toHaveCount(0)
})

test('Advanced mode stays available and the flow keeps its work when switching', async ({ page }) => {
  await start(page)
  await loadSet(page, /PrismHR/, 'PrismHR', 4)
  await ui(page).getByRole('radio', { name: 'Advanced mode' }).click()
  await expect(ui(page).getByLabel('Entity type')).toBeVisible()
  await ui(page).getByRole('radio', { name: 'Normalize', exact: true }).click()
  await expect(flow(page).getByTestId('file-list').getByRole('listitem')).toHaveCount(4)
})

test('start a new conversion resets everything', async ({ page }) => {
  await start(page)
  await loadSet(page, /PrismHR/, 'PrismHR', 4)
  await confirmIds(page, ['ACME', 'E1001'])
  await flow(page).getByRole('button', { name: 'Normalize Data' }).click()
  await flow(page).getByTestId('results').getByRole('button', { name: 'Start New Conversion' }).click()
  await expect(flow(page).getByTestId('results')).toHaveCount(0)
  await expect(flow(page).getByTestId('file-list')).toHaveCount(0)
})

test('Pipeline Explorer: compact header, grouped rows, real coverage, collapsed untraced section', async ({ page }) => {
  await start(page)
  await loadSet(page, /PrismHR/, 'PrismHR', 4)
  await confirmIds(page, ['ACME', 'E1001'])
  await flow(page).getByRole('button', { name: 'Normalize Data' }).click()
  await flow(page).getByTestId('results').getByRole('link', { name: 'View Transformation Pipeline' }).click()
  const pipe = ui(page).getByTestId('pipeline')
  // opens on an employee with mapping evidence, without any selection
  await expect(pipe.getByTestId('pipeline-header')).toContainText('PrismHR')
  await expect(pipe.getByTestId('pipeline-header')).toContainText('Employee records')
  await expect(pipe.getByTestId('selected-record')).toContainText('Jordan')
  await expect(pipe.getByTestId('coverage')).toHaveText(/^\d+ of \d+ fields \(\d+%\)$/)
  for (const g of ['Identity', 'Employment', 'Dates', 'Job', 'Compensation', 'Privacy']) await expect(pipe.getByTestId(`group-${g}`)).toBeVisible()
  await expect(pipe.getByTestId('group-Dates')).toContainText('hire_date')
  await expect(pipe.getByTestId('group-Compensation')).toContainText('compensation.annual_salary')
  await expect(pipe.getByTestId('group-Privacy')).toContainText('[withheld]')
  // untraced fields: separate and collapsed
  await expect(pipe.getByTestId('unmapped')).not.toHaveAttribute('open', '')
  await expect(pipe.getByTestId('unmapped').locator('summary')).toContainText('Source mapping not available')
  // meaningful conversions only
  await pipe.getByRole('checkbox', { name: 'Only real conversions' }).check()
  await expect(pipe.getByTestId('pipeline-row').filter({ hasText: 'first_name' })).toHaveCount(0)
  await expect(pipe.getByTestId('pipeline-row').filter({ hasText: 'employment_status' }).first()).toBeVisible()
  // keyboard: a row expands with the keyboard and exposes its state
  const row = pipe.getByTestId('pipeline-row').filter({ hasText: 'hire_date' }).first()
  await row.getByRole('button').focus()
  await page.keyboard.press('Enter')
  await expect(row.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  await expectNoSecrets(page)
})

for (const p of PROVIDERS) {
  test(`${p.name}: every record type has a readable mapping journey in the Pipeline Explorer`, async ({ page }) => {
    await start(page)
    await loadSet(page, p.re, p.name, p.files)
    await confirmIds(page, p.ids)
    await flow(page).getByRole('button', { name: 'Normalize Data' }).click()
    await flow(page).getByTestId('results').getByRole('link', { name: 'View Transformation Pipeline' }).click()
    const pipe = ui(page).getByTestId('pipeline')
    const choose = async (title: string) => {
      const select = ui(page).getByLabel('Choose converted data')
      const value = await select.locator('option').filter({ hasText: title }).first().getAttribute('value')
      await select.selectOption(value!)
    }
    // employer
    await choose('Employer')
    await expect(pipe.getByTestId('group-Identity')).toBeVisible()
    await expect(pipe.getByTestId('coverage')).toHaveText(/^\d+ of \d+ fields/)
    // bank deposits: account, split rule, and the account withheld
    await choose('Bank deposit set-up')
    await expect(pipe.getByTestId('group-Account')).toBeVisible()
    await expect(pipe.getByTestId('group-How pay is split')).toBeVisible()
    await expect(pipe.getByTestId('group-Privacy').getByTestId('origin')).toContainText('[withheld]')
    await expect(pipe.getByTestId('group-Account')).toContainText('routing_number')
    // deductions: what it is, and a value set by the normalizer has no invented source
    await choose('Paycheck deductions')
    await expect(pipe.getByTestId('group-What it is')).toBeVisible()
    const assigned = pipe.getByTestId('pipeline-row').filter({ hasText: 'setup_kind' }).first()
    await expect(assigned.getByTestId('no-source')).toHaveText('No source field')
    // payroll: the run (derived or copied) and a paycheck header; line items are named as not traced yet
    await choose('Payroll run and paychecks')
    await expect(pipe.getByTestId('group-Dates')).toBeVisible()
    await pipe.getByRole('tab', { name: /Paycheck/ }).first().click()
    await expect(pipe.getByTestId('group-Totals')).toBeVisible()
    await expect(pipe.getByTestId('untraced-why')).toContainText('Line items')
    await expectNoSecrets(page)
  })
}

