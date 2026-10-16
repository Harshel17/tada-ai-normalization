import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ConvertResponse, MigrationPreview, UnitResult } from '../../api/types'
import { mockFetch, record, renderWithClient } from '../../test/utils'
import { DRY_RUN, MigrationPanel } from './MigrationPanel'

afterEach(() => vi.unstubAllGlobals())
const user = () => userEvent.setup()

const emp = record({ source_id: 'N-3001', first_name: 'Priya', last_name: 'Nair', username: 'srcuser', employee_number: '3001', ssn_last4: '4417', provenance: { source_endpoint: 'GET /employee', source_path: '$.employee[0]', redacted_paths: [], derived_fields: [] } })
const unit: UnitResult = { file: 'employees.json', entity: 'employee', label: 'x', status: 'converted', missing: [], counts: { employee: 2 }, issues: [], records: [emp, record({ source_id: 'N-3002', first_name: 'Tomas', last_name: 'Okafor' })], traces: [] }
const conversion: ConvertResponse = { provider: 'prismhr', files: [], results: [unit], suggestions: [] }
const SOURCE = JSON.stringify({ employee: [{ id: 'N-3001', firstName: 'Priya', compensation: { ssn: '907-31-4417' } }] })

const preview = (over: Partial<MigrationPreview> = {}): MigrationPreview => ({
  dry_run: true, notice: DRY_RUN, destination_provider: 'ukg-ready', destination_company_id: '4410',
  source: { provider: 'prismhr', tenant_id: 't', company_id: 'ACME', source_id: 'N-3001' },
  contract: { provider: 'ukg-ready', method: 'POST', endpoint: '/v2/companies/4410/employees', schema_file: 'f', schema_anchor: 'a', caveat: 'not a complete endpoint contract' },
  readiness: 'READY', readiness_meaning: 'READY means the draft passed the documented local checks only. It does not mean UKG Ready will accept the request.',
  request_body: { username: 'pnair', first_name: 'Priya', last_name: 'Nair' },
  fields: [{ destination: 'username', source: null, status: 'operator_supplied', value: 'pnair', note: '' }, { destination: 'first_name', source: 'first_name', status: 'mapped', value: 'Priya', note: '' }],
  requirements: [{ field: 'username', satisfied: true, detail: 'supplied' }, { field: 'first_name', satisfied: true, detail: 'supplied' }],
  issues: [], not_migrated: [{ source: 'ssn_last4', reason: 'sensitive', note: 'never sent' }, { source: 'gender', reason: 'separate_endpoint', note: 'separate call' }], ...over,
})

function mount(reply: unknown = preview(), status = 200) {
  const m = mockFetch([{ match: /\/migrate\/preview\/ukg-ready/, reply: { status, body: reply } }])
  renderWithClient(<MigrationPanel conversion={conversion} fileTexts={{ 'employees.json': SOURCE }} />)
  return m
}
const bodyOf = (calls: { body?: string }[]) => JSON.parse(calls[0]!.body!)

describe('Migration preview panel', () => {
  it('shows the DRY RUN notice before anything is requested, and offers the employees from the result', () => {
    mount()
    expect(screen.getByTestId('dry-run')).toHaveTextContent(DRY_RUN)
    const select = screen.getByLabelText('Employee')
    expect(within(select).getAllByRole('option')).toHaveLength(2)
    expect(screen.queryByTestId('mig-result')).not.toBeInTheDocument()
  })

  it('cannot be requested without a destination company ID, and says why', async () => {
    const m = mount()
    expect(screen.getByRole('button', { name: 'Preview migration to UKG Ready' })).toBeDisabled()
    expect(screen.getByTestId('mig-problem')).toHaveTextContent(/destination company ID/i)
    await user().type(screen.getByLabelText(/Destination company ID/), 'bad id')
    expect(screen.getByTestId('mig-problem')).toHaveTextContent(/letters, digits/)
    expect(m.calls).toHaveLength(0)
  })

  it('suggests the source username but will not send it until confirmed', async () => {
    const m = mount()
    const u = user()
    await u.type(screen.getByLabelText(/Destination company ID/), '4410')
    await u.click(screen.getByRole('button', { name: /Suggestion: the source username “srcuser”/ }))
    expect(screen.getByLabelText(/Username in UKG Ready/)).toHaveValue('srcuser')
    expect(screen.getByRole('button', { name: 'Preview migration to UKG Ready' })).toBeDisabled()
    expect(screen.getByTestId('mig-problem')).toHaveTextContent(/source system/)
    await u.click(screen.getByRole('checkbox', { name: /I confirm “srcuser”/ }))
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    await screen.findByTestId('mig-result')
    expect(bodyOf(m.calls).operator).toEqual({ username: 'srcuser' })
  })

  it('sends source ids only when each box is ticked, never as the destination id', async () => {
    const m = mount()
    const u = user()
    await u.type(screen.getByLabelText(/Destination company ID/), '4410')
    await u.type(screen.getByLabelText(/Username in UKG Ready/), 'pnair')
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    await screen.findByTestId('mig-result')
    expect(bodyOf(m.calls).operator).toEqual({ username: 'pnair' })
    await u.click(screen.getByRole('checkbox', { name: /source employee number \(3001\)/ }))
    await u.click(screen.getByRole('checkbox', { name: /source record id \(N-3001\)/ }))
    expect(screen.queryByTestId('mig-result')).not.toBeInTheDocument() // an edit clears the stale preview
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    await screen.findByTestId('mig-result')
    expect(bodyOf([m.calls[1]!]).operator).toEqual({ username: 'pnair', use_source_employee_number: true, use_source_id_as_external_id: true })
    expect(m.calls[1]!.url).toMatch(/\/migrate\/preview\/ukg-ready$/)
    expect(m.calls.every((c) => c.method === 'POST' && /migrate\/preview/.test(c.url))).toBe(true) // the only call there is
  })

  it('renders source, canonical and destination, readiness, requirements, fields and what is not migrated', async () => {
    mount()
    const u = user()
    await u.type(screen.getByLabelText(/Destination company ID/), '4410')
    await u.type(screen.getByLabelText(/Username in UKG Ready/), 'pnair')
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    const result = await screen.findByTestId('mig-result')
    expect(within(result).queryAllByTestId('dry-run')).toHaveLength(0) // one banner for the whole panel, kept in view while scrolling
    expect(screen.getAllByTestId('dry-run')).toHaveLength(1)
    expect(screen.getByTestId('dry-run').className).toMatch(/sticky/)
    expect(screen.getByTestId('mig-readiness')).toHaveAttribute('data-readiness', 'READY')
    expect(screen.getByTestId('mig-readiness')).toHaveTextContent(/does not mean UKG Ready will accept/)
    expect(screen.getByTestId('mig-readiness')).toHaveTextContent(/POST \/v2\/companies\/4410\/employees \(not sent\)/)
    expect(screen.getByTestId('pane-source')).toHaveTextContent('"firstName": "Priya"')
    expect(screen.getByTestId('pane-canonical')).toHaveTextContent('Nair')
    expect(screen.getByTestId('pane-destination')).toHaveTextContent('"username": "pnair"')
    expect(screen.getByTestId('mig-requirements')).toHaveTextContent('username')
    expect(screen.getByTestId('mig-fields')).toHaveTextContent('you supplied')
    expect(screen.getByTestId('mig-not-migrated')).toHaveTextContent(/ssn_last4/)
    expect(screen.getByTestId('mig-not-migrated')).toHaveTextContent(/Needs a separate UKG call/)
  })

  it('never shows an unmasked SSN from the source, and the canonical pane has none', async () => {
    mount()
    const u = user()
    await u.type(screen.getByLabelText(/Destination company ID/), '4410')
    await u.type(screen.getByLabelText(/Username in UKG Ready/), 'pnair')
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    await screen.findByTestId('mig-result')
    expect(document.body.textContent).not.toContain('907-31-4417')
    expect(document.body.textContent).not.toContain('907314417')
  })

  it('offers a direct action that reveals and focuses the address country, without filling it in', async () => {
    mount(preview({ readiness: 'NEEDS_REVIEW', issues: [{ severity: 'review', code: 'address_country_required', field: 'address', message: 'address was not sent: the documented address requires a country (ISO alpha-3). Supply it as operator input.' }] }))
    const u = user()
    expect(screen.getByLabelText(/Address country/)).not.toBeVisible() // collapsed until asked for
    await u.type(screen.getByLabelText(/Destination company ID/), '4410')
    await u.type(screen.getByLabelText(/Username in UKG Ready/), 'pnair')
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    await screen.findByTestId('mig-result')
    await u.click(screen.getByRole('button', { name: 'Enter the address country' }))
    await waitFor(() => expect(screen.getByLabelText(/Address country/)).toBeVisible())
    await waitFor(() => expect(screen.getByLabelText(/Address country/)).toHaveFocus())
    expect(screen.getByLabelText(/Address country/)).toHaveValue('') // never invented
  })

  it('says plainly when the source text is not available', async () => {
    mockFetch([{ match: /\/migrate\/preview/, reply: { body: preview() } }])
    renderWithClient(<MigrationPanel conversion={conversion} fileTexts={{}} />)
    const u = user()
    await u.type(screen.getByLabelText(/Destination company ID/), '4410')
    await u.type(screen.getByLabelText(/Username in UKG Ready/), 'pnair')
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    expect(await screen.findByTestId('pane-source')).toHaveTextContent(/not available/)
  })

  it('presents BLOCKED and NEEDS_REVIEW with their reasons', async () => {
    mount(preview({ readiness: 'BLOCKED', request_body: {}, requirements: [{ field: 'username', satisfied: false, detail: 'missing' }],
      issues: [{ severity: 'blocking', code: 'missing_username', field: 'username', message: 'username is required by the documented schema and was not supplied.' }] }))
    const u = user()
    await u.type(screen.getByLabelText(/Destination company ID/), '4410')
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    await screen.findByTestId('mig-result')
    expect(screen.getByTestId('mig-readiness')).toHaveAttribute('data-readiness', 'BLOCKED')
    expect(screen.getByTestId('mig-issues')).toHaveTextContent(/username is required/)
    expect(screen.getByTestId('mig-requirements')).toHaveTextContent('missing')
  })

  it('shows an API error without a result', async () => {
    mount({ detail: 'destination.company_id: String should match pattern' }, 422)
    const u = user()
    await u.type(screen.getByLabelText(/Destination company ID/), '4410')
    await u.type(screen.getByLabelText(/Username in UKG Ready/), 'pnair')
    await u.click(screen.getByRole('button', { name: 'Preview migration to UKG Ready' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/company_id/)
    expect(screen.queryByTestId('mig-result')).not.toBeInTheDocument()
  })

  it('switching employee resets confirmations', async () => {
    mount()
    const u = user()
    await u.type(screen.getByLabelText(/Username in UKG Ready/), 'x')
    await u.selectOptions(screen.getByLabelText('Employee'), screen.getAllByRole('option')[1]!)
    await waitFor(() => expect(screen.getByLabelText(/Username in UKG Ready/)).toHaveValue(''))
  })
})
