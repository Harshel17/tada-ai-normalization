import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockFetch, renderWithClient } from '../test/utils'
import { Overview } from './Overview'

afterEach(() => vi.unstubAllGlobals())

const scope = (over = {}) => ({ provider: 'prismhr', tenant_id: 'acme', company_id: 'ACME', companies: 1, employees: 2, direct_deposits: 3, deductions: 2, payroll_runs: 1, paychecks: 2, ...over })
const stats = (scopes: unknown[]) => ({ providers: ['prismhr', 'isolved', 'ukg-ready'], entity_types: ['company', 'employee', 'direct_deposit', 'deduction', 'payroll'], scopes })

describe('Overview', () => {
  it('explains what the tool is for and links to the Normalize flow', () => {
    mockFetch([{ match: /health/, reply: { body: { status: 'ok', database: 'up' } } }, { match: /stats/, reply: { body: stats([]) } }])
    renderWithClient(<Overview />)
    const box = screen.getByTestId('what-is-this')
    expect(box).toHaveTextContent('one common format')
    expect(within(box).getByRole('link', { name: /Try it with example data/ })).toHaveAttribute('href', '#/normalize')
  })

  it('shows connectivity and the providers and entity types the backend reports', async () => {
    mockFetch([{ match: /health/, reply: { body: { status: 'ok', database: 'up' } } }, { match: /stats/, reply: { body: stats([]) } }])
    renderWithClient(<Overview />)
    await waitFor(() => expect(within(screen.getByTestId('api-status')).getByText('API online')).toBeInTheDocument())
    expect(within(screen.getByTestId('db-status')).getByText('database up')).toBeInTheDocument()
    for (const p of ['prismhr', 'isolved', 'ukg-ready']) expect(within(screen.getByTestId(`provider-${p}`)).getByText('supported by API')).toBeInTheDocument()
    for (const e of ['company', 'employee', 'direct_deposit', 'deduction', 'payroll']) expect(screen.getByTestId(`entity-${e}`)).toBeInTheDocument()
  })

  it('shows an empty state instead of invented numbers when the database holds nothing', async () => {
    mockFetch([{ match: /health/, reply: { body: { status: 'ok', database: 'up' } } }, { match: /stats/, reply: { body: stats([]) } }])
    renderWithClient(<Overview />)
    expect(await screen.findByText('The database is empty')).toBeInTheDocument()
    expect(screen.queryByTestId('totals')).not.toBeInTheDocument()
  })

  it('shows real totals summed from the per-scope counts', async () => {
    mockFetch([{ match: /health/, reply: { body: { status: 'ok', database: 'up' } } }, { match: /stats/, reply: { body: stats([scope(), scope({ tenant_id: 'b', employees: 5 })]) } }])
    renderWithClient(<Overview />)
    const totals = await screen.findByTestId('totals')
    expect(within(totals).getByText('Employees').nextSibling).toHaveTextContent('7')
    expect(within(totals).getByText('Direct deposits').nextSibling).toHaveTextContent('6')
    expect(screen.getAllByRole('row')).toHaveLength(3) // header + 2 scopes
  })

  it('reports an unreachable API honestly', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('down') }))
    renderWithClient(<Overview />)
    expect(await screen.findByText('unreachable')).toBeInTheDocument()
    expect(within(screen.getByTestId('db-status')).getByText(/unknown/)).toBeInTheDocument()
    expect(await screen.findByText('Could not load stored counts')).toBeInTheDocument()
  })

  it('reports a database outage reported by /health', async () => {
    mockFetch([{ match: /health/, reply: { body: { status: 'ok', database: 'down' } } }, { match: /stats/, reply: { status: 503, body: { detail: 'database unavailable' } } }])
    renderWithClient(<Overview />)
    expect(await screen.findByText('database down')).toBeInTheDocument()
    expect(await screen.findByText('database unavailable')).toBeInTheDocument()
  })
})
