import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockFetch, record, renderWithClient } from '../test/utils'
import { Explorer } from './Explorer'

afterEach(() => vi.unstubAllGlobals())

const scope = (over = {}) => ({ provider: 'prismhr', tenant_id: 'tA', company_id: 'ACME', companies: 1, employees: 2, direct_deposits: 3, deductions: 2, payroll_runs: 1, paychecks: 2, ...over })
const stats = (scopes: unknown[]) => ({ providers: ['prismhr', 'isolved', 'ukg-ready'], entity_types: [], scopes })
const emp = (id: string, extra = {}) => record({ source_id: id, first_name: 'Jordan', last_name: 'Rivera', employment_status: 'active', hire_date: '2021-03-01', ...extra })

const routes = [
  { match: /\/api\/stats/, reply: { body: stats([scope(), scope({ tenant_id: 'tB', employees: 0 }), scope({ provider: 'isolved', tenant_id: 'iso', company_id: '4501' })]) } },
  { match: /\/companies\/ACME\/employees\?/, reply: { body: [emp('E1001'), emp('E1002', { first_name: 'Casey' })] } },
  { match: /\/employees\/E1001\/direct-deposits/, reply: { body: [record({ record_type: 'direct_deposit', source_id: 'd1', account_number_masked: '****6789', account_number_hash: 'deadbeefdeadbeef', allocation_type: 'fixed_amount', amount: '500.00', status: 'active', active: true, sequence: null, source_position: 0 })] } },
  { match: /\/employees\/E1001\/deductions/, reply: { body: [record({ record_type: 'deduction', source_id: 'PARK', deduction_code: 'PARK', name: 'Parking', setup_kind: 'scheduled', amount: '25.50' })] } },
  { match: /\/employees\/E1001\/paychecks/, reply: { body: [] } },
  { match: /\/employees\/E1001$/, reply: { body: emp('E1001', { ssn_last4: '6789', birth_date: '1988-04-17' }) } },
  { match: /\/payroll-runs\/B1\/paychecks/, reply: { body: [record({ record_type: 'paycheck', source_id: 'V-1', employee_id: 'E1001', pay_date: '2025-01-17', gross_pay: '3846.15', net_pay: '2410.37' })] } },
  { match: /\/payroll-runs\?/, reply: { body: [record({ record_type: 'payroll_run', source_id: 'B1', pay_date: '2025-01-17', period_start: '2025-01-04', period_end: '2025-01-17' })] } },
  { match: /\/paychecks\/V-1/, reply: { body: record({ record_type: 'paycheck', source_id: 'V-1', employee_id: 'E1001', gross_pay: '3846.15', net_pay: '2410.37', earnings: [{ line_no: 1, code: 'REG', amount: '3846.15', extras: {} }], taxes: [], deductions: [], benefits: [], payments: [{ line_no: 1, amount: '500.00', account_masked: '****6789' }] }) } },
]

describe('Data Explorer', () => {
  it('offers only providers, tenants and companies that actually exist in the database', async () => {
    mockFetch(routes)
    renderWithClient(<Explorer />)
    await screen.findByLabelText('Provider')
    const options = (label: string) => within(screen.getByLabelText(label)).getAllByRole('option').map((o) => o.textContent)
    expect(options('Provider')).toEqual(['PrismHR', 'isolved'])
    expect(options('Tenant')).toEqual(['tA', 'tB'])
    expect(options('Company')).toEqual(['ACME'])
    await userEvent.setup().selectOptions(screen.getByLabelText('Provider'), 'isolved')
    await waitFor(() => expect(options('Tenant')).toEqual(['iso']))
    expect(options('Company')).toEqual(['4501'])
  })

  it('reads employees from the API, scoped to the chosen provider/tenant/company', async () => {
    const { calls } = mockFetch(routes)
    renderWithClient(<Explorer />)
    const table = await screen.findByTestId('employees-table')
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    expect(calls.some((c) => c.url.startsWith('/api/tenants/tA/prismhr/companies/ACME/employees?limit=25&offset=0'))).toBe(true)
    expect(within(table).getByText('Casey Rivera')).toBeInTheDocument()
  })

  it('writes the stored counts with correct singular and plural words', async () => {
    mockFetch([{ match: /\/api\/stats/, reply: { body: stats([scope({ direct_deposits: 1, deductions: 1, paychecks: 1 })]) } }, ...routes.slice(1)])
    renderWithClient(<Explorer />)
    expect(await screen.findByText(/1 direct deposit · 1 deduction · 1 paycheck\./)).toBeInTheDocument()
  })

  it('shows an employee’s details with masked bank data and no raw SSN or hash', async () => {
    mockFetch(routes)
    renderWithClient(<Explorer />)
    const u = userEvent.setup()
    await u.click(await screen.findByText('E1001'))
    const detail = await screen.findByTestId('employee-detail')
    await waitFor(() => expect(within(detail).getByText('6789')).toBeInTheDocument()) // ssn_last4 only
    await u.click(within(detail).getByRole('tab', { name: /Direct deposits/ }))
    const deposits = await within(detail).findByTestId('deposits-table')
    expect(within(deposits).getByText('****6789')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('deadbeef') // the account hash is never displayed
    await u.click(within(detail).getByRole('tab', { name: /Deductions/ }))
    expect(await within(detail).findByText('Parking')).toBeInTheDocument()
    await u.click(within(detail).getByRole('tab', { name: /Paychecks/ }))
    expect(await within(detail).findByText('No paychecks stored for this employee')).toBeInTheDocument()
  })

  it('browses payroll runs, their paychecks and the line items of one paycheck', async () => {
    mockFetch(routes)
    renderWithClient(<Explorer />)
    const u = userEvent.setup()
    await screen.findByTestId('employees-table')
    await u.click(screen.getByRole('tab', { name: /Payroll/ }))
    await u.click(await screen.findByText('B1'))
    await u.click(await screen.findByText('V-1'))
    const detail = await screen.findByTestId('paycheck-detail')
    expect(await within(detail).findByText('2410.37')).toBeInTheDocument()
    expect(within(detail).getByText(/earnings/i)).toBeInTheDocument()
    expect(within(detail).getByText(/"REG"/)).toBeInTheDocument()
    expect(within(detail).getByText(/"account_masked": "\*\*\*\*6789"/)).toBeInTheDocument()
  })

  it('masks a sensitive value even if the API ever returned one', async () => {
    mockFetch([
      routes[0]!,
      { match: /\/employees\?/, reply: { body: [] } },
      { match: /\/payroll-runs\?/, reply: { body: [record({ record_type: 'payroll_run', source_id: 'B1' })] } },
      { match: /B1\/paychecks/, reply: { body: [record({ record_type: 'paycheck', source_id: 'V-1', employee_id: 'E' })] } },
      { match: /\/paychecks\/V-1/, reply: { body: record({ record_type: 'paycheck', source_id: 'V-1', earnings: [], taxes: [], deductions: [], benefits: [], payments: [{ line_no: 1, account: '123456789012' }] }) } },
    ])
    renderWithClient(<Explorer />)
    const u = userEvent.setup()
    await screen.findByLabelText('Provider')
    await u.click(screen.getByRole('tab', { name: /Payroll/ }))
    await u.click(await screen.findByText('B1'))
    await u.click(await screen.findByText('V-1'))
    await screen.findByTestId('paycheck-detail')
    await waitFor(() => expect(screen.getByText(/••••9012/)).toBeInTheDocument())
    expect(document.body.textContent).not.toContain('123456789012')
  })

  it('shows an empty state with a way forward when nothing is stored', async () => {
    mockFetch([{ match: /stats/, reply: { body: stats([]) } }])
    renderWithClient(<Explorer />)
    expect(await screen.findByText('Nothing stored yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Normalize' })).toHaveAttribute('href', '#/normalize')
  })

  it('shows an error when the API is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('x') }))
    renderWithClient(<Explorer />)
    expect(await screen.findByText('Could not reach the API')).toBeInTheDocument()
  })

  it('pages with Previous / Next using limit and offset', async () => {
    const page = Array.from({ length: 25 }, (_, i) => emp(`E${i}`))
    const { calls } = mockFetch([routes[0]!, { match: /employees\?limit=25&offset=0/, reply: { body: page } }, { match: /employees\?limit=25&offset=25/, reply: { body: [emp('LAST')] } }])
    renderWithClient(<Explorer />)
    const u = userEvent.setup()
    await screen.findByTestId('employees-table')
    await u.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('LAST')).toBeInTheDocument()
    expect(calls.some((c) => c.url.includes('offset=25'))).toBe(true)
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled() // fewer than a full page: no more rows
  })
})
