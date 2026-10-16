import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

// Synthetic values that appear in the fixtures. None of them may ever be shown in a result view.
const RAW_SSN = '123-45-6789'
const RAW_ACCOUNT = '000123456789'

const tenant = (label: string) => `e2e-${label}-${Math.random().toString(36).slice(2, 8)}`

async function open(page: Page, section: 'overview' | 'normalize' | 'explorer' | 'runs') {
  await page.goto(`/#/${section}`)
}

/** The technical tool lives behind "Advanced mode"; guided mode is what a first-time visitor sees. */
async function openAdvanced(page: Page) {
  await open(page, 'normalize')
  await ui(page).getByRole('radio', { name: 'Advanced mode' }).click()
  await expect(ui(page).getByLabel('Entity type')).toBeVisible()
}

async function pickFixture(page: Page, provider: string, entity: string, fixture: string, ctx: Record<string, string>) {
  await ui(page).getByRole('radio', { name: provider }).click()
  await ui(page).getByLabel('Entity type').selectOption(entity)
  await ui(page).getByLabel('Synthetic fixture').selectOption({ label: fixture })
  await expect(ui(page).getByText('valid JSON')).toBeVisible()
  for (const [label, value] of Object.entries(ctx)) await ui(page).getByLabel(label, { exact: false }).first().fill(value)
}

/** Replaces the whole content of the (editable) source editor. Monaco ignores fill(), so type via the keyboard. */
async function setSource(page: Page, text: string) {
  const edit = ui(page).getByRole('button', { name: 'Edit input' })
  if (await edit.count()) await edit.click() // already editing: the button then reads "Done editing"
  const editor = ui(page).getByTestId('editor-source-json').locator('.monaco-editor').first()
  await editor.click()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.press('Backspace')
  await page.keyboard.insertText(text)
}

/** The section currently shown (all visited sections stay mounted, the others are hidden). */
const ui = (page: Page) => page.locator('[data-view]:not([hidden])')

const editorText = (page: Page, id: string) => ui(page).getByTestId(id).locator('.view-lines').innerText()

test.describe.configure({ mode: 'serial' })

test('overview reports real connectivity, providers, entity types and an empty database', async ({ page }) => {
  await open(page, 'overview')
  await expect(ui(page).getByTestId('api-status')).toContainText('API online')
  await expect(ui(page).getByTestId('db-status')).toContainText('database up')
  for (const p of ['PrismHR', 'isolved', 'UKG Ready']) await expect(ui(page).getByRole('heading', { name: p })).toBeVisible()
  for (const e of ['Company', 'Employee', 'Direct deposit', 'Deduction setup', 'Payroll']) await expect(ui(page).getByText(e, { exact: true }).first()).toBeVisible()
  await expect(ui(page).getByText('The database is empty')).toBeVisible() // fresh e2e database: no invented numbers
})

test('provider and entity selection filter the available fixtures', async ({ page }) => {
  await openAdvanced(page)
  const fixtures = ui(page).getByLabel('Synthetic fixture')
  await expect(fixtures.locator('option', { hasText: 'client_master.json' })).toHaveCount(1)
  await ui(page).getByRole('radio', { name: 'isolved' }).click()
  await expect(fixtures.locator('option', { hasText: 'legals.json' })).toHaveCount(1)
  await expect(fixtures.locator('option', { hasText: 'client_master.json' })).toHaveCount(0)
  await ui(page).getByRole('radio', { name: 'UKG Ready' }).click()
  await expect(fixtures.locator('option', { hasText: 'employee_bundle.json' })).toHaveCount(1)
  await ui(page).getByLabel('Entity type').selectOption('payroll')
  await expect(ui(page).getByLabel('Entity type')).toHaveValue('payroll')
})

test('loading a fixture masks sensitive values in the view but sends the original text', async ({ page }) => {
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': tenant('mask') })
  const requestPromise = page.waitForRequest((r) => r.url().includes('/api/normalize/') && r.method() === 'POST')
  await ui(page).getByRole('button', { name: 'Normalize (preview)' }).click()
  const body = (await requestPromise).postData() ?? ''
  expect(body).toContain(RAW_SSN) // the API receives the real (synthetic) input ...
  expect(body).toContain('"standardHours": 80.00') // ... exactly as written, decimal scale included
  await expect(ui(page).getByTestId('summary')).toBeVisible()
  expect(await page.locator('body').innerText()).not.toContain(RAW_SSN) // ... but the page never shows it
})

test('JSON editing: invalid input is flagged and blocks the buttons; fixing it re-enables them', async ({ page }) => {
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': tenant('edit') })
  await setSource(page, '{\n  "employee": [\n    { "id": oops }\n  ]\n}')
  await expect(ui(page).getByTestId('json-error')).toContainText('line 3')
  await expect(ui(page).getByTestId('json-error')).not.toContainText('oops')
  await expect(ui(page).getByText('invalid JSON')).toBeVisible()
  await expect(ui(page).getByRole('button', { name: 'Normalize (preview)' })).toBeDisabled()
  await expect(ui(page).getByRole('button', { name: 'Save to PostgreSQL' })).toBeDisabled()
  await setSource(page, '{"employee": [{"id": "E9", "firstName": "Ada"}]}')
  await expect(ui(page).getByTestId('json-error')).toHaveCount(0)
  await expect(ui(page).getByRole('button', { name: 'Normalize (preview)' })).toBeEnabled()
})

test('editing mode shows raw synthetic values; masking returns when editing is done', async ({ page }) => {
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': tenant('mask2') })
  await setSource(page, `{"ssn": "${RAW_SSN}", "accountNum": "${RAW_ACCOUNT}", "name": "x"}`)
  await expect(ui(page).getByText('Editing the raw input')).toBeVisible()
  expect(await editorText(page, 'editor-source-json')).toContain(RAW_SSN)
  await ui(page).getByRole('button', { name: 'Done editing' }).click()
  await expect.poll(() => editorText(page, 'editor-source-json')).toContain('••••6789') // Monaco updates asynchronously
  const masked = await editorText(page, 'editor-source-json')
  expect(masked).not.toContain(RAW_SSN)
  expect(masked).not.toContain(RAW_ACCOUNT)
  // Regression: the masked VIEW must never overwrite the real input. The request still carries the originals.
  await ui(page).getByLabel('Entity type').selectOption('company')
  const sent = page.waitForRequest((r) => r.url().includes('/api/normalize/') && r.method() === 'POST')
  await ui(page).getByRole('button', { name: 'Normalize (preview)' }).click()
  const body = (await sent).postData() ?? ''
  expect(body).toContain(RAW_SSN)
  expect(body).toContain(RAW_ACCOUNT)
  expect(body).not.toContain('••••')
})

test('uploading a JSON file loads it', async ({ page }) => {
  await openAdvanced(page)
  await ui(page).getByLabel('Synthetic fixture').selectOption('')
  await ui(page).getByTestId('upload').setInputFiles({ name: 'mine.json', mimeType: 'application/json', buffer: Buffer.from('{"clientMaster": {"id": "UPLD", "legalName": "Uploaded Inc"}}') })
  await expect(ui(page).getByText('valid JSON')).toBeVisible()
  await ui(page).getByLabel('Entity type').selectOption('company')
  await ui(page).getByLabel('Tenant id').first().fill(tenant('upload'))
  await ui(page).getByRole('button', { name: 'Normalize (preview)' }).click()
  await expect(ui(page).getByTestId('summary')).toContainText('1 company')
})

test('normalizing shows the summary, canonical records, provenance, unmapped fields and a side-by-side view', async ({ page }) => {
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': tenant('norm') })
  await ui(page).getByRole('button', { name: 'Normalize (preview)' }).click()
  const summary = ui(page).getByTestId('summary')
  await expect(summary).toContainText('normalized without errors')
  await expect(summary).toContainText('2 employee')
  await expect(summary).toContainText('Withheld')

  await ui(page).getByRole('tab', { name: /Records/ }).click()
  const card = ui(page).getByTestId('record-employee').first()
  await expect(card).toContainText('E1001')
  await expect(card).toContainText('Jordan')
  await expect(card).toContainText('$.employee[0]') // provenance: where in the source it came from
  await expect(card).toContainText('$.employee[0].compensation.ssn') // withheld, listed by path
  await expect(card).toContainText('personChecksum') // unmapped source fields are kept, not dropped
  expect(await page.locator('body').innerText()).not.toContain(RAW_SSN)

  await ui(page).getByRole('tab', { name: /Source/ }).click()
  await expect(ui(page).getByTestId('compare')).toBeVisible()
  await expect(ui(page).getByTestId('editor-compare-source').locator('.monaco-editor').first()).toBeVisible()
  await expect(ui(page).getByTestId('editor-compare-canonical').locator('.monaco-editor').first()).toBeVisible()
  await expect.poll(() => editorText(page, 'editor-compare-canonical')).toContain('prismhr')

  await ui(page).getByRole('tab', { name: /Issues/ }).click()
  await expect(ui(page).getByTestId('issues-table')).toContainText('unmapped_code')
  await ui(page).getByRole('tab', { name: 'Raw response' }).click()
  await expect(ui(page).getByText('"entity_type": "employee"')).toBeVisible()
})

test('validation errors: a record without an id is reported, and nothing is saved', async ({ page }) => {
  const t = tenant('bad')
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': t })
  await setSource(page, '{"employee": [{"firstName": "No id"}]}')
  await ui(page).getByRole('button', { name: 'Normalize (preview)' }).click()
  await expect(ui(page).getByTestId('summary')).toContainText('1 error: this payload cannot be saved')
  await expect(ui(page).getByTestId('summary')).toContainText('employee.id is missing')
  await ui(page).getByRole('button', { name: 'Save to PostgreSQL' }).click()
  const result = ui(page).getByTestId('save-result')
  await expect(result).toContainText('not saved')
  await expect(result).toContainText('Nothing was written')
})

test('missing required context blocks the request and names the field', async ({ page }) => {
  await openAdvanced(page)
  await pickFixture(page, 'UKG Ready', 'direct_deposit', 'direct_deposits.json', { 'Tenant id': tenant('ctx') })
  await expect(ui(page).getByLabel('Employee id')).toHaveValue('7770001') // the scenario's context was pre-filled
  await ui(page).getByLabel('Employee id').fill('')
  await expect(ui(page).getByText(/Required context missing: employee_id/)).toBeVisible()
  await expect(ui(page).getByRole('button', { name: 'Normalize (preview)' })).toBeDisabled()
  await ui(page).getByLabel('Employee id').fill('7770001')
  await expect(ui(page).getByRole('button', { name: 'Normalize (preview)' })).toBeEnabled()
})

const ingestTenantA = tenant('ingest')

test('saving to PostgreSQL inserts, and saving the same input again reports unchanged', async ({ page }) => {
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': ingestTenantA })
  await ui(page).getByRole('button', { name: 'Save to PostgreSQL' }).click()
  const outcome = ui(page).getByTestId('stored-outcome')
  await expect(outcome).toContainText('2 inserted')
  await expect(outcome).toContainText('0 unchanged')

  await ui(page).getByRole('button', { name: 'Save to PostgreSQL' }).click()
  await expect(outcome).toContainText('0 inserted')
  await expect(outcome).toContainText('2 unchanged')
  await expect(outcome).toContainText('nothing written')
})

test('a changed record is reported as updated, not duplicated', async ({ page }) => {
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': ingestTenantA })
  await setSource(page, '{"employee": [{"id": "E1001", "firstName": "Renamed"}]}')
  await ui(page).getByRole('button', { name: 'Save to PostgreSQL' }).click()
  await expect(ui(page).getByTestId('stored-outcome')).toContainText('1 updated')
})

test('tenant and provider isolation in the Data Explorer, and read-back of stored data', async ({ page }) => {
  const tenantB = tenant('other')
  // tenant B stores ONE employee with the same ids as tenant A's, under the same provider
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': tenantB })
  await setSource(page, '{"employee": [{"id": "E1001", "firstName": "OnlyInB"}]}')
  await ui(page).getByRole('button', { name: 'Save to PostgreSQL' }).click()
  await expect(ui(page).getByTestId('stored-outcome')).toContainText('1 inserted')
  // and an isolved record under yet another tenant
  const isoTenant = tenant('iso')
  await pickFixture(page, 'isolved', 'employee', 'employees.json', { 'Tenant id': isoTenant, 'Company id': '4501' })
  await ui(page).getByRole('button', { name: 'Save to PostgreSQL' }).click()
  await expect(ui(page).getByTestId('stored-outcome')).toContainText('2 inserted')

  await open(page, 'explorer')
  await ui(page).getByRole('button', { name: 'Refresh' }).click()
  await ui(page).getByLabel('Provider', { exact: true }).selectOption({ label: 'PrismHR' })

  const tenants = ui(page).getByLabel('Tenant', { exact: true })
  await expect(tenants.locator('option', { hasText: ingestTenantA })).toHaveCount(1)
  await expect(tenants.locator('option', { hasText: isoTenant })).toHaveCount(0) // other provider's tenant is invisible here

  await tenants.selectOption(ingestTenantA)
  const table = ui(page).getByTestId('employees-table')
  await expect(table).toContainText('Renamed') // tenant A: the updated name from the previous test
  await expect(table).toContainText('Casey')
  await expect(table).not.toContainText('OnlyInB')

  await tenants.selectOption(tenantB)
  await expect(ui(page).getByTestId('employees-table')).toContainText('OnlyInB')
  await expect(ui(page).getByTestId('employees-table')).not.toContainText('Renamed')
  await expect(ui(page).getByTestId('employees-table').getByRole('row')).toHaveCount(2) // header + 1 employee

  await ui(page).getByLabel('Provider', { exact: true }).selectOption({ label: 'isolved' })
  await expect(ui(page).getByLabel('Tenant', { exact: true }).locator('option', { hasText: ingestTenantA })).toHaveCount(0)
  await expect(ui(page).getByTestId('employees-table')).toContainText('Alex')
})

test('Data Explorer shows direct deposits, deductions, payroll and masks sensitive data', async ({ page }) => {
  const t = tenant('explore')
  await open(page, 'runs')
  await ui(page).getByRole('button', { name: 'None' }).click()
  for (const title of ['PrismHR · client master', 'PrismHR · getEmployee', 'PrismHR · ACH vouchers', 'PrismHR · scheduled deductions', 'PrismHR · payroll vouchers']) await ui(page).getByLabel(`Select ${title}`, { exact: false }).check()
  await ui(page).getByLabel(/Tenant for this run/).fill(t)
  await ui(page).getByRole('button', { name: /Run 5 scenarios/ }).click()
  await expect(ui(page).getByTestId('run-summary')).toContainText('5 of 5 completed scenarios passed', { timeout: 60_000 })

  await open(page, 'explorer')
  await ui(page).getByRole('button', { name: 'Refresh' }).click()
  await ui(page).getByLabel('Provider', { exact: true }).selectOption({ label: 'PrismHR' })
  await ui(page).getByLabel('Tenant', { exact: true }).selectOption(t)
  await ui(page).getByTestId('employees-table').getByText('E1001').click()
  const detail = ui(page).getByTestId('employee-detail')
  await expect(detail).toContainText('Jordan')
  await expect(detail).toContainText('6789') // ssn_last4 only

  await detail.getByRole('tab', { name: /Direct deposits/ }).click()
  const deposits = ui(page).getByTestId('deposits-table')
  await expect(deposits.getByRole('row')).toHaveCount(4) // header + 3 allocations
  await expect(deposits).toContainText('****6789')
  await expect(deposits).toContainText('fixed_amount')
  await expect(deposits).toContainText('remaining_balance')
  await detail.getByRole('tab', { name: /Deductions/ }).click()
  await expect(ui(page).getByTestId('deductions-table')).toContainText('Parking')

  await ui(page).getByRole('tab', { name: /Payroll/ }).click()
  await ui(page).getByTestId('runs-table').getByText('B-2025-02').click()
  await ui(page).getByTestId('paychecks-table').getByText('V-5001').click()
  const paycheck = ui(page).getByTestId('paycheck-detail')
  await expect(paycheck).toContainText('2410.37')
  await expect(paycheck).toContainText('"code": "FIT"')
  await expect(paycheck).toContainText('****6789') // masked deposit account on the paycheck

  const everything = await page.locator('body').innerText()
  for (const secret of [RAW_SSN, RAW_ACCOUNT, '998877665544', '123456789']) expect(everything).not.toContain(secret)
})

test('Test Runs: one scenario per provider run end to end through the public API', async ({ page }) => {
  await open(page, 'runs')
  await ui(page).getByRole('button', { name: 'None' }).click()
  for (const title of ['PrismHR · getEmployee', 'isolved · employees', 'UKG Ready · employee bundle']) await ui(page).getByLabel(`Select ${title}`, { exact: false }).check()
  await ui(page).getByRole('button', { name: /Run 3 scenarios/ }).click()
  await expect(ui(page).getByTestId('run-summary')).toContainText('3 of 3 completed scenarios passed', { timeout: 60_000 })
  for (const id of ['prismhr-employee', 'isolved-employee', 'ukg-employee']) {
    await expect(ui(page).getByTestId(`scenario-${id}`)).toContainText('all steps passed')
  }
  await ui(page).getByTestId('scenario-ukg-employee').getByRole('button', { name: /UKG Ready · employee bundle/ }).click()
  for (const step of ['normalize', 'ingest1', 'ingest2', 'readback', 'tenant', 'provider']) {
    await expect(ui(page).getByTestId(`step-ukg-employee-${step}`)).toContainText('pass')
  }
  await expect(ui(page).getByTestId('step-ukg-employee-ingest2')).toContainText('0 written')
})

test('overview counts reflect what the tests really stored', async ({ page }) => {
  await open(page, 'overview')
  const totals = ui(page).getByTestId('totals')
  await expect(totals).toBeVisible()
  await expect(ui(page).getByTestId('scope-table')).toContainText(ingestTenantA)
  await expect(totals).toContainText('Employees')
})

test('privacy: no request leaves localhost and nothing is written to browser storage', async ({ page, context }) => {
  const hosts = new Set<string>()
  page.on('request', (r) => {
    const u = new URL(r.url())
    if (!['data:', 'blob:'].includes(u.protocol)) hosts.add(u.host)
  })
  await open(page, 'overview')
  await openAdvanced(page)
  await pickFixture(page, 'PrismHR', 'employee', 'employee_get_employee.json', { 'Tenant id': tenant('privacy') })
  await ui(page).getByRole('button', { name: 'Normalize (preview)' }).click()
  await expect(ui(page).getByTestId('summary')).toBeVisible()
  await ui(page).getByRole('tab', { name: /Source/ }).click() // loads the Monaco editors
  await expect(ui(page).getByTestId('editor-compare-canonical').locator('.monaco-editor').first()).toBeVisible()
  await ui(page).getByRole('button', { name: 'Save to PostgreSQL' }).click()
  await expect(ui(page).getByTestId('stored-outcome')).toBeVisible()
  await open(page, 'explorer')

  expect([...hosts]).toEqual([new URL(page.url()).host]) // only the console itself (API calls go through its proxy)
  const stored = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length, cookie: document.cookie }))
  expect(stored).toEqual({ local: 0, session: 0, cookie: '' })
  expect(await context.cookies()).toEqual([])
  expect(await page.evaluate(async () => (await indexedDB.databases?.())?.length ?? 0)).toBe(0)
})
