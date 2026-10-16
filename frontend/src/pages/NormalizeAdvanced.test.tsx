import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mockFetch, normalizeResponse, record, renderWithClient } from '../test/utils'
import type { Call } from '../test/utils'
import { NormalizeAdvanced } from './NormalizeAdvanced'

// Monaco needs a real browser; the e2e tests cover it. Here the editor is a plain textarea.
vi.mock('../components/JsonEditor', () => ({
  JsonEditor: ({ value, onChange, readOnly, ariaLabel }: { value: string; onChange?: (v: string) => void; readOnly?: boolean; ariaLabel: string }) => (
    <textarea aria-label={ariaLabel} value={value} readOnly={readOnly} onChange={(e) => onChange?.(e.target.value)} />
  ),
}))

const FIXTURE_TEXT = '{"employee":[{"id":"E1","compensation":{"ssn":"123-45-6789","annualPay":"100000.00","salary":3846.150}}]}'
const FIXTURES = { fixtures: [
  { dir: 'prismhr', name: 'employee_get_employee.json' }, { dir: 'prismhr', name: 'client_master.json' },
  { dir: 'isolved', name: 'employees.json' }, { dir: 'ukg_ready', name: 'employee_bundle.json' },
] }

const base = () => [
  { match: /__fixtures\/index/, reply: { body: FIXTURES } },
  { match: /__fixtures\/file\?dir=prismhr&name=employee_get_employee/, reply: { text: FIXTURE_TEXT } },
]

const source = () => screen.getByLabelText('Source JSON') as HTMLTextAreaElement
const user = () => userEvent.setup()
const posts = (calls: Call[]) => calls.filter((c) => c.method === 'POST')

async function loadFixture(name = 'employee_get_employee.json') {
  await waitFor(() => expect(screen.getByRole('option', { name })).toBeInTheDocument())
  await user().selectOptions(screen.getByLabelText('Synthetic fixture'), name)
  await waitFor(() => expect(source().value).not.toBe(''))
}

afterEach(() => vi.unstubAllGlobals())
beforeEach(() => vi.unstubAllGlobals())

describe('provider and entity selection', () => {
  it('lets the user pick each provider and entity, and lists only that provider’s fixtures', async () => {
    mockFetch(base())
    renderWithClient(<NormalizeAdvanced />)
    const radios = screen.getAllByRole('radio')
    expect(radios.map((r) => r.textContent)).toEqual(['PrismHR', 'isolved', 'UKG Ready'])
    expect(screen.getByRole('radio', { name: 'PrismHR' })).toBeChecked()

    await waitFor(() => expect(screen.getByRole('option', { name: 'client_master.json' })).toBeInTheDocument())
    expect(screen.queryByRole('option', { name: 'employees.json' })).not.toBeInTheDocument()

    await user().click(screen.getByRole('radio', { name: 'isolved' }))
    expect(screen.getByRole('radio', { name: 'isolved' })).toBeChecked()
    expect(screen.getByRole('option', { name: 'employees.json' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'client_master.json' })).not.toBeInTheDocument()

    await user().click(screen.getByRole('radio', { name: 'UKG Ready' }))
    expect(screen.getByRole('option', { name: 'employee_bundle.json' })).toBeInTheDocument()

    const entity = screen.getByLabelText('Entity type')
    expect(within(entity).getAllByRole('option').map((o) => o.textContent)).toEqual(['Company', 'Employee', 'Direct deposit', 'Deduction setup', 'Payroll'])
    await user().selectOptions(entity, 'payroll')
    expect((entity as HTMLSelectElement).value).toBe('payroll')
  })

  it('marks the context each provider/entity needs as required', async () => {
    mockFetch(base())
    renderWithClient(<NormalizeAdvanced />)
    expect(screen.getByLabelText(/Company id/)).toBeInTheDocument()
    await user().click(screen.getByRole('radio', { name: 'UKG Ready' }))
    await user().selectOptions(screen.getByLabelText('Entity type'), 'direct_deposit')
    const required = screen.getAllByLabelText('required').length
    expect(required).toBe(3) // tenant, company, employee
  })
})

describe('fixtures and JSON input', () => {
  it('loads a fixture into the editor, with sensitive values masked in the view', async () => {
    mockFetch(base())
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    expect(source().value).toContain('"annualPay":"100000.00"')
    expect(source().value).toContain('••••6789')
    expect(source().value).not.toContain('123-45-6789')
    expect(source()).toHaveAttribute('readonly')
    expect(screen.getByText('valid JSON')).toBeInTheDocument()
  })

  it('shows the raw text with a warning only after “Edit input”, and validates edits live', async () => {
    mockFetch(base())
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Edit input' }))
    expect(source().value).toContain('123-45-6789')
    expect(screen.getByText(/Editing the raw input/)).toBeInTheDocument()

    const u = user()
    await u.clear(source())
    await u.click(source())
    await u.paste('{"a": oops}')
    expect(screen.getByTestId('json-error')).toHaveTextContent(/JSON error: .*line 1/)
    expect(screen.getByTestId('json-error')).not.toHaveTextContent('oops')
    expect(screen.getByRole('button', { name: 'Normalize (preview)' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save to PostgreSQL' })).toBeDisabled()
    expect(screen.getByText('invalid JSON')).toBeInTheDocument()

    await u.clear(source())
    await u.click(source())
    await u.paste('{"employee": []}')
    expect(screen.queryByTestId('json-error')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Normalize (preview)' })).toBeEnabled()
  })

  it('reads an uploaded JSON file in the browser', async () => {
    mockFetch(base())
    renderWithClient(<NormalizeAdvanced />)
    const file = new File(['{"clientMaster": {"id": "ACME"}}'], 'mine.json', { type: 'application/json' })
    await user().upload(screen.getByTestId('upload'), file)
    await waitFor(() => expect(source().value).toBe('{"clientMaster": {"id": "ACME"}}'))
    expect(screen.getByText('valid JSON')).toBeInTheDocument()
  })

  it('never writes input JSON to browser storage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem')
    mockFetch(base())
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    expect(setItem).not.toHaveBeenCalled()
    expect(localStorage.length + sessionStorage.length).toBe(0)
    setItem.mockRestore()
  })

  it('disables running when required context is missing and says which', async () => {
    mockFetch(base())
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().clear(screen.getByLabelText(/Company id/))
    expect(screen.getByRole('button', { name: 'Normalize (preview)' })).toBeDisabled()
    expect(screen.getByText(/Required context missing: company_id/)).toBeInTheDocument()
  })

  it('reports a fixture-loading failure and still allows pasting', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html/>', { status: 200, headers: { 'content-type': 'text/html' } })))
    renderWithClient(<NormalizeAdvanced />)
    await waitFor(() => expect(screen.getByText(/only available while/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Edit input' })).toBeEnabled()
  })
})

describe('normalize', () => {
  it('sends the original text with the context, then shows figures computed from the response', async () => {
    const response = normalizeResponse({
      records: [record({ extras: { x: 1, y: 2 }, provenance: { source_endpoint: 'GET /e', source_path: '$.employee[0]', redacted_paths: ['$.ssn'], derived_fields: [] } })],
      issues: [{ severity: 'info', code: 'unmapped_code', message: 'gender has a code without a documented meaning', path: '$.employee[0]', source_id: 'E1' }],
    })
    const { calls } = mockFetch([...base(), { match: /\/api\/normalize\/prismhr\/employee/, reply: { body: response } }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Normalize (preview)' }))

    const summary = await screen.findByTestId('summary')
    const sent = posts(calls)[0]!
    expect(sent.url).toBe('/api/normalize/prismhr/employee')
    expect(sent.body).toContain(FIXTURE_TEXT) // original, unmasked, byte-for-byte
    expect(JSON.parse(sent.body!).context).toEqual({ tenant_id: 'console-demo', company_id: 'ACME' })
    expect(within(summary).getByText('normalized without errors')).toBeInTheDocument()
    expect(within(summary).getByText('Mapped fields')).toBeInTheDocument()
    expect(within(summary).getByText('1 employee')).toBeInTheDocument()
    expect(within(summary).getByText('0/0/1')).toBeInTheDocument()
    expect(within(summary).getByText('gender has a code without a documented meaning')).toBeInTheDocument()
    expect(posts(calls)).toHaveLength(1) // preview never saves
  })

  it('shows records with provenance and unmapped fields', async () => {
    mockFetch([...base(), { match: /normalize/, reply: { body: normalizeResponse({ records: [record({ extras: { person: { smoker: 'N' } }, provenance: { source_endpoint: 'GET /e', source_path: '$.employee[0]', redacted_paths: ['$.employee[0].compensation.ssn'], derived_fields: ['gross_pay'] } })] }) } }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Normalize (preview)' }))
    await user().click(await screen.findByRole('tab', { name: /Records/ }))
    const card = screen.getByTestId('record-employee')
    expect(within(card).getByText('Jordan')).toBeInTheDocument()
    expect(within(card).getByText('$.employee[0]')).toBeInTheDocument()
    expect(within(card).getByText('$.employee[0].compensation.ssn')).toBeInTheDocument()
    expect(within(card).getByText('gross_pay')).toBeInTheDocument()
    expect(within(card).getByText(/"smoker": "N"/)).toBeInTheDocument()
  })

  it('shows validation errors from a 422 response and says the payload cannot be saved', async () => {
    const bad = normalizeResponse({ ok: false, records: [], counts: {}, issues: [{ severity: 'error', code: 'missing_id', message: 'employee.id is missing', path: '$.employee[0]', source_id: null }] })
    mockFetch([...base(), { match: /normalize/, reply: { status: 422, body: bad } }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Normalize (preview)' }))
    const summary = await screen.findByTestId('summary')
    expect(within(summary).getByText(/1 error: this payload cannot be saved/)).toBeInTheDocument()
    expect(within(summary).getByText('employee.id is missing')).toBeInTheDocument()
    expect(within(summary).getByText('HTTP 422')).toBeInTheDocument()
  })

  it('shows a request failure without echoing the payload', async () => {
    mockFetch([...base(), { match: /normalize/, reply: new TypeError('Failed to fetch') }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Normalize (preview)' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Cannot reach the API')
    expect(document.body.textContent).not.toContain('123-45-6789')
  })

  it('shows a server-side rejection such as an unknown provider (404)', async () => {
    mockFetch([...base(), { match: /normalize/, reply: { status: 404, body: { detail: "unknown provider 'x'" } } }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Normalize (preview)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("unknown provider 'x'")
  })
})

describe('save to PostgreSQL (ingest)', () => {
  const ingestBody = (inserted: number, unchanged: number) => ({ ok: true, provider: 'prismhr', entity_type: 'employee', counts: { employee: 2 }, issues: [], stored: { employee: { inserted, updated: 0, unchanged } } })

  it('reports inserted on the first save and unchanged on the repeat', async () => {
    let n = 0
    const { calls } = mockFetch([...base(), { match: /ingest/, reply: () => ({ body: ingestBody(n++ === 0 ? 2 : 0, n === 1 ? 0 : 2) }) }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Save to PostgreSQL' }))
    let out = await screen.findByTestId('stored-outcome')
    expect(within(out).getByText('2 inserted')).toBeInTheDocument()
    expect(within(out).getByText('0 unchanged')).toBeInTheDocument()
    expect(within(out).getByText('new rows written')).toBeInTheDocument()

    await user().click(screen.getByRole('button', { name: 'Save to PostgreSQL' }))
    await waitFor(() => expect(within(screen.getByTestId('stored-outcome')).getByText('2 unchanged')).toBeInTheDocument())
    out = screen.getByTestId('stored-outcome')
    expect(within(out).getByText('0 inserted')).toBeInTheDocument()
    expect(within(out).getByText(/nothing written/)).toBeInTheDocument()
    expect(posts(calls).map((c) => c.url)).toEqual(['/api/ingest/prismhr/employee', '/api/ingest/prismhr/employee'])
  })

  it('shows “updated” when stored rows were replaced', async () => {
    mockFetch([...base(), { match: /ingest/, reply: { body: { ...ingestBody(0, 1), stored: { employee: { inserted: 0, updated: 1, unchanged: 1 } } } } }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Save to PostgreSQL' }))
    const out = await screen.findByTestId('stored-outcome')
    expect(within(out).getByText('1 updated')).toBeInTheDocument()
    expect(within(out).getByText(/existing rows replaced/)).toBeInTheDocument()
  })

  it('says nothing was written when the backend rejects the payload', async () => {
    mockFetch([...base(), { match: /ingest/, reply: { status: 422, body: { ok: false, provider: 'prismhr', entity_type: 'employee', counts: {}, stored: {}, issues: [{ severity: 'error', code: 'missing_id', message: 'employee.id is missing', path: null, source_id: null }] } } }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Save to PostgreSQL' }))
    const box = await screen.findByTestId('save-result')
    expect(within(box).getByText('not saved')).toBeInTheDocument()
    expect(within(box).getByText('Nothing was written')).toBeInTheDocument()
    expect(within(box).getByText('employee.id is missing')).toBeInTheDocument()
  })

  it('surfaces a database outage (503)', async () => {
    mockFetch([...base(), { match: /ingest/, reply: { status: 503, body: { detail: 'database unavailable' } } }])
    renderWithClient(<NormalizeAdvanced />)
    await loadFixture()
    await user().click(screen.getByRole('button', { name: 'Save to PostgreSQL' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('database unavailable')
  })
})
