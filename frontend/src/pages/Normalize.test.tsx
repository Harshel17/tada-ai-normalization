import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConvertResponse, Detection, DetectedUnit, DetectResponse, IntegrityReport, UnitResult } from '../api/types'
import { ConversionProvider } from '../state/conversion'
import { countsOf, mockFetch, record, renderWithClient, repoFixture, repoSnapshot } from '../test/utils'
import type { Call } from '../test/utils'
import { Normalize } from './Normalize'

vi.mock('../components/JsonEditor', () => ({
  JsonEditor: ({ value, ariaLabel }: { value: string; ariaLabel: string }) => <textarea aria-label={ariaLabel} value={value} readOnly />,
}))

const user = () => userEvent.setup()
afterEach(() => vi.unstubAllGlobals())
beforeEach(() => vi.unstubAllGlobals())
const posts = (calls: Call[], re: RegExp) => calls.filter((c) => c.method === 'POST' && re.test(c.url))

const PRISM_FILES = ['client_master.json', 'deductions_get_deductions.json', 'employee_get_employee.json', 'payroll_vouchers.json']

const unit = (entity: DetectedUnit['entity'], over: Partial<DetectedUnit> = {}): DetectedUnit => ({
  entity, shape: 'x', label: entity, keys: null, counts: { [entity]: 1 }, derived_counts: {}, evidence: ['Found wrapper key “x”'], requires: [],
  optional: [], nested_in: null, convention: false, strength: 'strong', notes: [], ...over,
})
const ok = (...units: DetectedUnit[]): Detection => ({ status: 'ok', units, candidates: [], warnings: [], message: null, provider_hint: null })

function prismDetect(): DetectResponse {
  return {
    provider: 'prismhr',
    files: [
      { name: 'client_master.json', detection: ok(unit('company', { counts: { company: 1 } })) },
      { name: 'deductions_get_deductions.json', detection: ok(unit('deduction', { counts: { deduction: 3 }, requires: ['company_id', 'employee_id'] })) },
      { name: 'employee_get_employee.json', detection: ok(
        unit('employee', { counts: { employee: 2 }, requires: ['company_id'] }),
        unit('direct_deposit', { counts: { direct_deposit: 3 }, nested_in: 'employee', requires: ['company_id'] }),
        unit('deduction', { counts: { deduction: 2 }, nested_in: 'employee', requires: ['company_id'] }),
      ) },
      { name: 'payroll_vouchers.json', detection: ok(unit('payroll', { counts: { paycheck: 2 }, derived_counts: { payroll_run: 1 }, requires: ['company_id'] })) },
    ],
    suggestions: [
      { field: 'company_id', value: 'ACME', source_file: 'client_master.json', why: 'clientMaster.id' },
      { field: 'employee_id', value: 'E1001', source_file: 'employee_get_employee.json', why: 'employee[].id' },
      { field: 'employee_id', value: 'E1002', source_file: 'employee_get_employee.json', why: 'employee[].id' },
    ],
  }
}

const result = (u: Partial<UnitResult>): UnitResult => ({ file: 'f.json', entity: 'employee', label: 'x', status: 'converted', missing: [], counts: { employee: 1 }, issues: [], records: [record()], traces: [[]], ...u })

function routes(over: { detect?: (c: Call) => unknown; convert?: (c: Call) => unknown; ingest?: (c: Call) => { status?: number; body: unknown } } = {}) {
  return [
    { match: /__fixtures\/index/, reply: { body: { fixtures: PRISM_FILES.map((name) => ({ dir: 'prismhr', name })).concat([{ dir: 'isolved', name: 'legals.json' }]) } } },
    { match: /__fixtures\/file\?dir=/, reply: (call: Call) => { const u = new URL(call.url, 'http://x'); return { text: repoFixture(u.searchParams.get('dir')!, u.searchParams.get('name')!) } } },
    { match: /\/detect\//, reply: (c: Call) => ({ body: over.detect ? over.detect(c) : prismDetect() }) },
    { match: /\/convert\//, reply: (c: Call) => ({ body: over.convert ? over.convert(c) : { provider: 'prismhr', files: [], results: [], suggestions: [] } }) },
    { match: /\/ingest\//, reply: (c: Call) => over.ingest?.(c) ?? { body: { provider: 'prismhr', entity_type: 'employee', ok: true, counts: { employee: 1 }, stored: { employee: { inserted: 1, updated: 0, unchanged: 0 } }, issues: [] } } },
    { match: /\/stats/, reply: { body: { providers: [], entity_types: [], scopes: [] } } },
  ]
}

const mount = () => renderWithClient(<ConversionProvider><Normalize /></ConversionProvider>)
async function loadSet(provider = 'PrismHR') {
  await user().click(screen.getByRole('radio', { name: new RegExp(provider) }))
  await user().click(screen.getByRole('button', { name: 'Try example data' }))
  await screen.findByTestId('detect-client_master.json')
}

describe('the first screen', () => {
  it('shows one heading and the provider choice; later steps appear only when reached', () => {
    mockFetch(routes())
    mount()
    expect(screen.getByRole('heading', { name: 'Three payroll providers. One consistent data model.' })).toBeInTheDocument()
    expect(screen.getAllByRole('radio', { name: /PrismHR|isolved|UKG Ready/ })).toHaveLength(3)
    expect(screen.queryByRole('button', { name: 'Upload JSON' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try example data' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Normalize Data' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('stepper')).not.toBeInTheDocument()
  })

  it('shows Upload JSON and Try example data once a provider is chosen', async () => {
    mockFetch(routes())
    mount()
    await user().click(screen.getByRole('radio', { name: /PrismHR/ }))
    expect(screen.getByRole('button', { name: 'Upload JSON' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try example data' })).toBeInTheDocument()
  })

  it('uses plain language: no developer vocabulary on the main screen', () => {
    mockFetch(routes())
    mount()
    const text = document.body.textContent ?? ''
    for (const jargon of [/tenant/i, /fixture/i, /entity type/i, /canonical/i, /provenance/i, /PostgreSQL/i, /endpoint/i]) expect(text).not.toMatch(jargon)
  })

  it('never asks the user to pick an entity type up front', () => {
    mockFetch(routes())
    mount()
    expect(screen.queryByText(/What kind of information/)).not.toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /Direct deposit/ })).not.toBeInTheDocument()
  })
})

describe('adding data and detecting contents', () => {
  it('loads the provider’s complete real example set (not merged) and detects each file', async () => {
    const { calls } = mockFetch(routes())
    mount()
    await loadSet()
    const list = screen.getByTestId('file-list')
    expect(within(list).getAllByRole('listitem')).toHaveLength(4)
    expect(posts(calls, /\/detect\/prismhr/)).toHaveLength(1)
    const sent = JSON.parse(posts(calls, /\/detect\//)[0]!.body!) as { files: { name: string; content: unknown }[] }
    expect(sent.files.map((f) => f.name).sort()).toEqual(PRISM_FILES)
    expect(calls.some((c) => /dir=isolved/.test(c.url))).toBe(false)
  })

  it('embeds each file’s text as written, so 100000.00 keeps its scale', async () => {
    const { calls } = mockFetch(routes())
    mount()
    await loadSet()
    await waitFor(() => expect(posts(calls, /\/detect\//)).toHaveLength(1))
    expect(posts(calls, /\/detect\//)[0]!.body).toContain('100000.00')
  })

  it('shows what each file contains, counts, nested records, and why', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    const emp = await screen.findByTestId('detect-employee_get_employee.json')
    expect(emp).toHaveTextContent('2 employees')
    expect(emp).toHaveTextContent('3 direct deposits')
    expect(emp).toHaveTextContent('(inside the employee records)')
    expect(within(emp).getAllByText('Why we think so')).toHaveLength(3)
    expect(screen.getByTestId('detect-payroll_vouchers.json')).toHaveTextContent('also creates 1 payroll run')
  })

  it('asks for confirmation when a file is ambiguous, then re-detects with the user’s choice', async () => {
    const ambiguous: Detection = { status: 'ambiguous', units: [], candidates: [{ entity: 'employee', reason: 'Only name fields found' }, { entity: 'payroll', reason: 'amount' }], warnings: [], message: 'Please confirm what it is.', provider_hint: null }
    const { calls } = mockFetch(routes({
      detect: (c) => {
        const body = JSON.parse(c.body!) as { files: { name: string; entity?: string }[] }
        return { provider: 'prismhr', suggestions: [], files: body.files.map((f) => ({ name: f.name, detection: f.entity ? ok(unit(f.entity as 'employee', { requires: ['company_id'] })) : ambiguous })) }
      },
    }))
    mount()
    await user().click(screen.getByRole('radio', { name: /PrismHR/ }))
    await user().upload(screen.getByTestId('upload'), new File(['[{"firstName":"Ann"}]'], 'mystery.json', { type: 'application/json' }))
    const card = await screen.findByTestId('detect-mystery.json')
    expect(card).toHaveTextContent('Needs your confirmation')
    await user().click(within(card).getByRole('radio', { name: /Employee records/ }))
    await user().click(within(card).getByRole('button', { name: /Confirm this is/ }))
    await waitFor(() => expect(screen.getByTestId('detect-mystery.json')).toHaveTextContent('confirmed by you'))
    expect(JSON.parse(posts(calls, /\/detect\//).at(-1)!.body!).files[0].entity).toBe('employee')
  })

  it('explains an unsupported file and hints at another provider without switching', async () => {
    const bad: Detection = { status: 'unsupported', units: [], candidates: [], warnings: [], message: 'This does not match any PrismHR response shape.', provider_hint: 'This looks like UKG Ready data (EINs), but PrismHR is selected.' }
    mockFetch(routes({ detect: () => ({ provider: 'prismhr', suggestions: [], files: [{ name: 'eins.json', detection: bad }] }) }))
    mount()
    await user().click(screen.getByRole('radio', { name: /PrismHR/ }))
    await user().upload(screen.getByTestId('upload'), new File(['{"items":[{"ein_tax_id":"1"}]}'], 'eins.json'))
    const card = await screen.findByTestId('detect-eins.json')
    expect(card).toHaveTextContent('Not recognised')
    expect(card).toHaveTextContent('looks like UKG Ready')
    expect(screen.getByRole('radio', { name: /PrismHR/ })).toBeChecked()
  })

  it('flags a file that is not valid JSON and leaves it out of the request', async () => {
    const { calls } = mockFetch(routes({ detect: (c) => ({ provider: 'prismhr', suggestions: [], files: (JSON.parse(c.body!) as { files: { name: string }[] }).files.map((f) => ({ name: f.name, detection: ok(unit('company')) })) }) }))
    mount()
    await user().click(screen.getByRole('radio', { name: /PrismHR/ }))
    await user().upload(screen.getByTestId('upload'), [new File(['{not json'], 'broken.json'), new File(['{"clientMaster":{"id":"A"}}'], 'good.json')])
    await screen.findByTestId('detect-good.json')
    expect(screen.getByText('broken.json is not valid JSON')).toBeInTheDocument()
    const sent = JSON.parse(posts(calls, /\/detect\//).at(-1)!.body!) as { files: { name: string }[] }
    expect(sent.files.map((f) => f.name)).toEqual(['good.json'])
  })

  it('switching provider clears files and results', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    await user().click(screen.getByRole('radio', { name: /isolved/ }))
    expect(screen.queryByTestId('file-list')).not.toBeInTheDocument()
  })
})

describe('identifiers: suggested, never silently used', () => {
  it('lists the ids the data needs, offers sibling-file suggestions, and blocks Normalize until confirmed', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    const box = await screen.findByTestId('context-box')
    expect(within(box).getByLabelText(/Employer ID/)).toHaveValue('')
    expect(within(box).getByLabelText(/Employee ID/)).toHaveValue('')
    expect(within(box).getByRole('button', { name: /Use ACME/ })).toHaveTextContent('from client_master.json')
    expect(screen.getByRole('button', { name: 'Normalize Data' })).toBeDisabled()
    expect(screen.getByTestId('blocked')).toHaveTextContent('Confirm: Employer ID, Employee ID')
    await user().click(within(box).getByRole('button', { name: /Use ACME/ }))
    await user().click(within(box).getByRole('button', { name: /Use E1001/ }))
    expect(within(box).getByLabelText(/Employer ID/)).toHaveValue('ACME')
    // choosing an employee is not enough: the owner of the deductions must be acknowledged
    expect(screen.getByRole('button', { name: 'Normalize Data' })).toBeDisabled()
    expect(screen.getByTestId('blocked')).toHaveTextContent(/Confirm which employee owns the deductions/)
    await user().click(screen.getByRole('checkbox', { name: /I confirm that employee E1001 owns the deductions/ }))
    expect(screen.getByRole('button', { name: 'Normalize Data' })).toBeEnabled()
  })

  it('never fills an id by itself, even when examples are loaded', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    const box = await screen.findByTestId('context-box')
    expect(within(box).getAllByRole('button', { name: /Use ACME/ })).toHaveLength(1) // not duplicated
    expect(within(box).getAllByRole('button', { name: /Use E1002/ })).toHaveLength(1)
    expect(within(box).getByLabelText(/Employer ID/)).toHaveValue('')
  })
})

async function readyToConvert() {
  await loadSet()
  const box = await screen.findByTestId('context-box')
  await user().click(within(box).getAllByRole('button', { name: /Use ACME/ })[0]!)
  await user().click(within(box).getAllByRole('button', { name: /Use E1001/ })[0]!)
  await user().click(screen.getByRole('checkbox', { name: /I confirm that employee E1001 owns/ }))
}

const converted = (): ConvertResponse => ({
  provider: 'prismhr', files: [], suggestions: [],
  results: [
    result({ file: 'employee_get_employee.json', entity: 'employee', counts: { employee: 2 }, records: [record(), record({ source_id: 'E2' })], traces: [[], []] }),
    result({ file: 'employee_get_employee.json', entity: 'direct_deposit', counts: { direct_deposit: 3 } }),
    result({ file: 'payroll_vouchers.json', entity: 'payroll', status: 'needs_context', missing: ['company_id'], counts: {}, records: [], traces: [] }),
  ],
})

describe('normalize and results', () => {
  it('converts through /convert with the confirmed context and asks for the trace; saves nothing', async () => {
    const { calls } = mockFetch(routes({ convert: converted }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    const results = await screen.findByTestId('results')
    const body = JSON.parse(posts(calls, /\/convert\//)[0]!.body!) as { context: Record<string, string>; trace: boolean }
    expect(body.context).toMatchObject({ company_id: 'ACME', employee_id: 'E1001', tenant_id: 'normalize-demo' })
    expect(body.trace).toBe(true)
    expect(posts(calls, /\/ingest\//)).toHaveLength(0)
    expect(within(results).getByTestId('headline')).toHaveTextContent('Converted 2 employees, 3 direct deposits.')
    expect(within(results).getByTestId('headline')).toHaveTextContent('waiting for an identifier')
  })

  it('shows each section’s outcome, including one that is waiting for an identifier', async () => {
    mockFetch(routes({ convert: converted }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    await screen.findByTestId('results')
    expect(screen.getByTestId('unit-payroll_vouchers.json-payroll')).toHaveTextContent('Needs: Employer ID. Nothing was guessed.')
    expect(screen.getByTestId('unit-employee_get_employee.json-employee')).toHaveTextContent('2 employees')
  })

  it('offers the four actions; the pipeline link goes to the explorer; JSON view hides secrets', async () => {
    const withSecret = converted()
    withSecret.results[0]!.records = [record({ ssn_last4: '6789', account_number: '000123456789' })]
    mockFetch(routes({ convert: () => withSecret }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    const results = await screen.findByTestId('results')
    expect(within(results).getByRole('link', { name: 'View Transformation Pipeline' })).toHaveAttribute('href', '#/pipeline')
    await user().click(within(results).getByRole('button', { name: 'View Normalized JSON' }))
    const json = screen.getByTestId('normalized-json')
    expect(json).toHaveTextContent('6789')
    expect(json.textContent).not.toContain('000123456789')
    expect(within(results).getByRole('button', { name: 'Save to Database' })).toBeEnabled()
    expect(within(results).getByRole('button', { name: 'Start New Conversion' })).toBeInTheDocument()
  })

  it('reports an API failure without showing a result', async () => {
    mockFetch([...routes({}).filter((r) => !/convert/.test(String(r.match))), { match: /\/convert\//, reply: new Error('offline') }])
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    expect(await screen.findByText('The conversion could not run')).toBeInTheDocument()
    expect(screen.queryByTestId('results')).not.toBeInTheDocument()
  })
})

describe('saving is a separate, explicit step', () => {
  it('saves each converted section through /ingest with the original file text, and says what happened', async () => {
    const { calls } = mockFetch(routes({ convert: converted }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    await screen.findByTestId('results')
    expect(posts(calls, /\/ingest\//)).toHaveLength(0)
    await user().click(screen.getByRole('button', { name: 'Save to Database' }))
    const outcome = await screen.findByTestId('save-outcome')
    const ingests = posts(calls, /\/ingest\//)
    expect(ingests.map((c) => c.url)).toEqual(['/api/ingest/prismhr/employee', '/api/ingest/prismhr/direct_deposit'])
    expect(ingests[0]!.body).toContain('"payload":')
    expect(ingests[0]!.body).toContain('100000.00')
    expect(outcome).toHaveTextContent('saved')
    expect(outcome).toHaveTextContent('1 new employee record saved')
  })

  it('reports nothing saved when the backend rejects a section', async () => {
    mockFetch(routes({ convert: converted, ingest: () => ({ status: 422, body: { provider: 'prismhr', entity_type: 'employee', ok: false, counts: {}, stored: {}, issues: [{ severity: 'error', code: 'x', message: 'bad record', path: null, source_id: null }] } }) }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    await screen.findByTestId('results')
    await user().click(screen.getByRole('button', { name: 'Save to Database' }))
    expect(await screen.findAllByText('not saved')).not.toHaveLength(0)
  })

  it('disables saving when a section has errors', async () => {
    const bad = converted()
    bad.results[0]!.issues = [{ severity: 'error', code: 'x', message: 'bad', path: null, source_id: null }]
    mockFetch(routes({ convert: () => bad }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    await screen.findByTestId('results')
    expect(screen.getByRole('button', { name: 'Save to Database' })).toBeDisabled()
  })
})

describe('safeguards and modes', () => {
  it('Start New Conversion returns to the beginning', async () => {
    mockFetch(routes({ convert: converted }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    await screen.findByTestId('results')
    await user().click(within(screen.getByTestId('results')).getByRole('button', { name: 'Start New Conversion' }))
    expect(screen.queryByTestId('results')).not.toBeInTheDocument()
    expect(screen.queryByTestId('file-list')).not.toBeInTheDocument()
  })

  it('Advanced mode is one click away and the flow keeps its state', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    await user().click(screen.getByRole('radio', { name: 'Advanced mode' }))
    expect(screen.getByTestId('advanced')).toBeVisible()
    await user().click(screen.getByRole('radio', { name: 'Normalize' }))
    expect(screen.getByTestId('file-list')).toBeVisible()
  })

  it('never writes to browser storage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    mockFetch(routes({ convert: converted }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    await screen.findByTestId('results')
    expect(setItem).not.toHaveBeenCalled()
    setItem.mockRestore()
  })
})

describe('a valid response with zero records', () => {
  it('says so instead of showing an empty “Converted .”', async () => {
    const empty: ConvertResponse = { provider: 'prismhr', files: [], suggestions: [], results: [result({ file: 'd.json', entity: 'deduction', counts: {}, records: [], traces: [], issues: [{ severity: 'warning', code: 'no_records', message: 'payload held no records', path: null, source_id: null }] })] }
    mockFetch(routes({ convert: () => empty }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    const head = await screen.findByTestId('headline')
    expect(head).toHaveTextContent('Recognised, but the file contains no records to convert.')
    expect(head).not.toHaveTextContent('Converted .')
  })
})

describe('readable results (“What we found”)', () => {
  const real = (name: string, over: Partial<UnitResult> = {}): UnitResult => {
    const snap = repoSnapshot(name)
    return result({ entity: snap.entity_type as UnitResult['entity'], counts: countsOf(snap.records), issues: snap.issues, records: snap.records, traces: [], ...over })
  }
  const realResponse = (): ConvertResponse => ({
    provider: 'prismhr', files: [], suggestions: [],
    results: [
      real('prismhr-employee', { file: 'employee_get_employee.json' }),
      real('prismhr-direct-deposit', { file: 'employee_get_employee.json' }),
      real('prismhr-payroll', { file: 'payroll_vouchers.json' }),
      result({ file: 'later.json', entity: 'deduction', status: 'needs_context', missing: ['employee_id'], counts: {}, records: [], traces: [] }),
    ],
  })
  async function normalizeWith(response: ConvertResponse) {
    mockFetch(routes({ convert: () => response }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    return screen.findByTestId('results')
  }

  it('opens a plain-language summary for the first converted section and keeps the others closed', async () => {
    const results = await normalizeWith(realResponse())
    const first = within(results).getByTestId('found-employee_get_employee.json-employee')
    expect(first).toHaveAttribute('open')
    expect(within(first).getByTestId('record-heading')).toHaveTextContent('Jordan Q Rivera')
    expect(within(first).getByRole('region', { name: 'Who this is' })).toHaveTextContent('Jordan Q Rivera')
    expect(within(first).getByRole('region', { name: 'Employment' })).toHaveTextContent('2021-03-01')
    expect(within(results).getByTestId('found-employee_get_employee.json-direct_deposit')).not.toHaveAttribute('open')
    expect(within(results).getByTestId('found-payroll_vouchers.json-payroll')).not.toHaveAttribute('open')
  })

  it('shows what happened to the data: counts and plain-English notes', async () => {
    const results = await normalizeWith(realResponse())
    const first = within(results).getByTestId('found-employee_get_employee.json-employee')
    expect(within(first).getByTestId('story-converted')).toHaveTextContent('across 2 records')
    expect(within(first).getByTestId('story-withheld')).toBeInTheDocument()
    expect(within(first).getByTestId('story-notUnderstood')).toHaveTextContent('documentation does not explain') // Casey’s gender code X
  })

  it('describes every kind of converted record when its section is opened', async () => {
    const results = await normalizeWith(realResponse())
    const dep = within(results).getByTestId('found-employee_get_employee.json-direct_deposit')
    await user().click(within(dep).getByText(/What we found/))
    expect(within(dep).getByRole('region', { name: 'Where the money goes' })).toHaveTextContent('****')
    const pay = within(results).getByTestId('found-payroll_vouchers.json-payroll')
    await user().click(within(pay).getByText(/What we found/))
    expect(within(pay).getAllByRole('tab')).toHaveLength(3)
    expect(within(pay).getByRole('region', { name: 'The pay run' })).toBeInTheDocument()
    await user().click(within(pay).getAllByRole('tab', { name: /Paycheck/ })[0]!)
    expect(within(pay).getByRole('region', { name: 'The paycheck' })).toHaveTextContent('Gross pay')
  })

  it('offers no summary for a section that is waiting for an identifier or has no records', async () => {
    const response = realResponse()
    response.results.push(result({ file: 'empty.json', entity: 'deduction', counts: {}, records: [], traces: [] }))
    const results = await normalizeWith(response)
    expect(within(results).queryByTestId('found-later.json-deduction')).not.toBeInTheDocument()
    expect(within(results).queryByTestId('found-empty.json-deduction')).not.toBeInTheDocument()
    expect(within(results).getByTestId('unit-later.json-deduction')).toHaveTextContent('Needs: Employee ID')
  })

  it('never shows a full SSN, account number or password anywhere in the readable results', async () => {
    const response = realResponse()
    response.results[0]!.records = [record({ first_name: 'Ann', last_name: 'Lee', ssn: '123-45-6789', account_number: '000123456789', password: 'hunter2hunter2' }), ...response.results[0]!.records]
    const results = await normalizeWith(response)
    for (const el of within(results).getAllByText(/What we found/)) await user().click(el)
    const text = document.body.textContent ?? ''
    for (const secret of ['123-45-6789', '000123456789', 'hunter2hunter2', '987-65-4321']) expect(text).not.toContain(secret)
    expect(text).toContain('Ann Lee')
  })
})

describe('data checks (integrity) in the results', () => {
  const report = (): IntegrityReport => ({
    summary: { passed: 1, warning: 1, failed: 0, not_evaluated: 1 },
    checks: [
      { id: 'duplicate_ids', group: 'Identity', title: 'No record id appears twice', status: 'passed', reason: 'All 14 record ids are unique.', evaluated: 14, flagged: 0, examples: [] },
      { id: 'employee_links', group: 'Relationships', title: 'Deposits, deductions and paychecks point to a converted employee', status: 'warning', reason: '1 employee id is referenced but not converted here.', evaluated: 7, flagged: 2, examples: [{ record: 'employee 7770002', detail: 'referenced by 2 records; not among the converted employees' }] },
      { id: 'gross_to_net', group: 'Money', title: 'Gross pay minus taxes and deductions equals net pay', status: 'not_evaluated', reason: 'Not checked on purpose.', evaluated: 0, flagged: 0, examples: [] },
    ],
  })

  it('asks for the checks together with the trace, and shows them with all statuses', async () => {
    const { calls } = mockFetch(routes({ convert: () => ({ ...converted(), integrity: report() }) }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    const panel = await screen.findByTestId('integrity-panel')
    const body = JSON.parse(posts(calls, /\/convert\//)[0]!.body!) as Record<string, unknown>
    expect(body.integrity).toBe(true)
    expect(body.trace).toBe(true)
    expect(within(panel).getByTestId('check-employee_links')).toHaveTextContent('employee 7770002')
    expect(within(panel).getByTestId('check-status-gross_to_net')).toHaveTextContent('Not evaluated')
    expect(within(panel).getByTestId('integrity-count-passed')).toHaveTextContent('1')
  })

  it('shows no panel when the response carries no report, and still converts and saves as before', async () => {
    mockFetch(routes({ convert: converted }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    await screen.findByTestId('results')
    expect(screen.queryByTestId('integrity-panel')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save to Database' })).toBeEnabled()
  })

  it('does not block saving: the checks inform, they do not decide', async () => {
    const failing = report()
    failing.summary = { passed: 0, warning: 0, failed: 1, not_evaluated: 0 }
    failing.checks = [{ id: 'duplicate_ids', group: 'Identity', title: 'No record id appears twice', status: 'failed', reason: 'A record id is used twice.', evaluated: 2, flagged: 1, examples: [] }]
    mockFetch(routes({ convert: () => ({ ...converted(), integrity: failing }) }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    await screen.findByTestId('integrity-panel')
    expect(screen.getByRole('button', { name: 'Save to Database' })).toBeEnabled()
  })
})


describe('one-click examples, summary and migration preview', () => {
  it('shows the headline and an example button for each provider on the first screen', () => {
    mockFetch(routes())
    mount()
    expect(screen.getByRole('heading', { name: 'Three payroll providers. One consistent data model.' })).toBeInTheDocument()
    const group = screen.getByRole('group', { name: 'Try an example' })
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['PrismHR example', 'isolved example', 'UKG Ready example'])
  })

  it('one click loads the examples, uses the ids they were written with, normalizes and shows the summary', async () => {
    const { calls } = mockFetch(routes({ convert: converted }))
    mount()
    await user().click(screen.getByTestId('quick-prismhr'))
    await screen.findByTestId('results')
    const convertCall = posts(calls, /\/convert\//)[0]!
    const sent = JSON.parse(convertCall.body!)
    expect(sent.context).toMatchObject({ tenant_id: 'normalize-demo', company_id: 'ACME', employee_id: 'E1001' })
    expect(sent.files).toHaveLength(4)
    expect(sent.integrity).toBe(true)
    const overview = screen.getByTestId('overview')
    expect(overview).toHaveTextContent('Records produced')
    expect(overview).toHaveTextContent('Preserved, not mapped')
    expect(overview).toHaveTextContent('Protected values')
    expect(posts(calls, /\/ingest\//)).toHaveLength(0) // an example never saves anything
  })

  it('the migration preview is offered for the employees of that result and is the only extra call', async () => {
    const { calls } = mockFetch(routes({ convert: converted }))
    mount()
    await user().click(screen.getByTestId('quick-prismhr'))
    const panel = await screen.findByTestId('migration')
    expect(within(panel).getByTestId('dry-run')).toHaveTextContent('DRY RUN — No data has been sent to the destination provider.')
    expect(calls.filter((c) => /migrate/.test(c.url))).toHaveLength(0) // nothing is requested until the reviewer asks
  })

  it('reports a failed example load without leaving a half-built screen', async () => {
    mockFetch([{ match: /__fixtures\/index/, reply: { status: 404, body: null } }, { match: /\/stats/, reply: { body: { providers: [], entity_types: [], scopes: [] } } }])
    mount()
    await user().click(screen.getByTestId('quick-isolved'))
    expect(await screen.findByRole('alert')).toHaveTextContent(/only available while/)
    expect(screen.queryByTestId('results')).not.toBeInTheDocument()
  })
})

describe('employee ownership (the Employee ID decides records that name no owner)', () => {
  it('explains what the field does, in plain words, before anything is chosen', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    const field = await screen.findByTestId('owner-field')
    expect(within(field).getByTestId('owner-explanation')).toHaveTextContent(
      'This file does not identify the employee who owns these deductions. The employee ID you select will be assigned to records that do not provide their own owner. Confirm the correct employee before normalizing.',
    )
    expect(within(field).getByLabelText(/Employee ID/)).toHaveValue('')
    expect(within(field).getByRole('checkbox')).toBeDisabled() // nothing to confirm yet
  })

  it('several employees are candidates only: none is chosen, and the text says so', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    const field = await screen.findByTestId('owner-field')
    expect(within(field).getByTestId('owner-candidates')).toHaveTextContent(/2 employees appear in your files\. They are only candidates/)
    expect(within(field).getByTestId('owner-candidates')).toHaveTextContent(/none is chosen for you/)
    expect(within(field).getByLabelText(/Employee ID/)).toHaveValue('')
  })

  it('shows the documented owner of a bundled example file, as information, without filling it in', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    const documented = await screen.findByTestId('owner-documented')
    expect(documented).toHaveTextContent('Example set’s convention for the bundled file deductions_get_deductions.json: E1001. The file itself does not name its owner')
    expect(screen.getByLabelText(/Employee ID/)).toHaveValue('')
  })

  it('a wrong but existing owner can be chosen and confirmed: the UI makes the confirmation explicit and claims no verification', async () => {
    mockFetch(routes({ convert: converted }))
    mount()
    await loadSet()
    const box = await screen.findByTestId('context-box')
    await user().click(within(box).getAllByRole('button', { name: /Use ACME/ })[0]!)
    await user().click(within(box).getAllByRole('button', { name: /Use E1002/ })[0]!) // not the documented owner
    const label = screen.getByText(/I confirm that employee E1002 owns the deductions/)
    expect(label).toHaveTextContent(/neither the files nor the checks can verify it/)
    await user().click(screen.getByRole('checkbox', { name: /I confirm that employee E1002/ }))
    expect(screen.getByRole('button', { name: 'Normalize Data' })).toBeEnabled()
  })

  it('changing the employee withdraws the confirmation', async () => {
    mockFetch(routes())
    mount()
    await loadSet()
    const box = await screen.findByTestId('context-box')
    await user().click(within(box).getAllByRole('button', { name: /Use ACME/ })[0]!)
    await user().click(within(box).getAllByRole('button', { name: /Use E1001/ })[0]!)
    await user().click(screen.getByRole('checkbox', { name: /I confirm that employee E1001/ }))
    expect(screen.getByRole('button', { name: 'Normalize Data' })).toBeEnabled()
    await user().click(within(box).getAllByRole('button', { name: /Use E1002/ })[0]!)
    expect(screen.getByRole('checkbox', { name: /I confirm that employee E1002/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Normalize Data' })).toBeDisabled()
  })

  it('without the confirmation nothing is sent', async () => {
    const { calls } = mockFetch(routes({ convert: converted }))
    mount()
    await loadSet()
    const box = await screen.findByTestId('context-box')
    await user().click(within(box).getAllByRole('button', { name: /Use ACME/ })[0]!)
    await user().click(within(box).getAllByRole('button', { name: /Use E1001/ })[0]!)
    expect(screen.getByRole('button', { name: 'Normalize Data' })).toBeDisabled() // a disabled button cannot send anything
    expect(posts(calls, /\/convert\//)).toHaveLength(0)
  })

  it('after normalizing it states which records took their owner from the Employee ID, and that this is not verified', async () => {
    const detect = prismDetect()
    const conv: ConvertResponse = {
      provider: 'prismhr', suggestions: [],
      files: detect.files,
      results: [result({ file: 'deductions_get_deductions.json', entity: 'deduction', counts: { deduction: 3 }, records: [record({ record_type: 'deduction', source_id: 'D1' }), record({ record_type: 'deduction', source_id: 'D2' }), record({ record_type: 'deduction', source_id: 'D3' })] })],
    }
    mockFetch(routes({ convert: () => conv }))
    mount()
    await readyToConvert()
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    const note = await screen.findByTestId('ownership-notice')
    expect(note).toHaveTextContent('Owner assigned from the Employee ID you confirmed')
    expect(note).toHaveTextContent('3 deductions in deductions_get_deductions.json were assigned to employee E1001')
    expect(note).toHaveTextContent(/acknowledgment, not something the files or the integrity checks can verify/)
    expect(note).toHaveTextContent(/keep it/)
    expect(note.textContent).not.toMatch(/verified owner|ownership is verified|confirmed correct/i)
  })

  it('ownership messages carry no sensitive values', async () => {
    mockFetch(routes({ convert: converted }))
    mount()
    await loadSet()
    const text = (await screen.findByTestId('owner-field')).textContent ?? ''
    expect(text).not.toMatch(/\d{3}-\d{2}-\d{4}|\d{9,}/)
  })
})
