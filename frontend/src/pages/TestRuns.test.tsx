import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockFetch, renderWithClient } from '../test/utils'
import { TestRuns } from './TestRuns'

afterEach(() => vi.unstubAllGlobals())

const ing = (inserted: number, unchanged: number, type = 'company') => ({ ok: true, counts: { [type]: 1 }, stored: { [type]: { inserted, updated: 0, unchanged } }, issues: [] })

async function runOnly(title: RegExp) {
  const u = userEvent.setup()
  await u.click(screen.getByRole('button', { name: 'None' }))
  await u.click(screen.getByLabelText(title))
  await u.click(screen.getByRole('button', { name: /Run 1 scenario/ }))
}

describe('Test Runs', () => {
  it('starts with nothing marked as passed', () => {
    mockFetch([])
    renderWithClient(<TestRuns />)
    expect(screen.getByText('No run yet')).toBeInTheDocument()
    expect(screen.queryByTestId('run-summary')).not.toBeInTheDocument()
    expect(screen.queryByText('all steps passed')).not.toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(15)
  })

  it('runs a scenario against the API and reports each step from the real responses', async () => {
    let saves = 0
    const { calls } = mockFetch([
      { match: /__fixtures\/file/, reply: { text: '{"clientMaster":{"id":"ACME"}}' } },
      { match: /normalize/, reply: { body: { ok: true, counts: { company: 1 }, issues: [], records: [] } } },
      { match: /ingest/, reply: () => ({ body: saves++ === 0 ? ing(1, 0) : ing(0, 1) }) },
      { match: /-other\//, reply: { status: 404, body: { detail: 'company not found' } } },
      { match: /\/isolved\//, reply: { status: 404, body: { detail: 'company not found' } } },
      { match: /companies\/ACME$/, reply: { body: { source_id: 'ACME' } } },
    ])
    renderWithClient(<TestRuns />)
    await runOnly(/PrismHR · client master/)
    await waitFor(() => expect(screen.getByTestId('run-summary')).toHaveTextContent(/1 of 1 completed scenarios passed/))
    const sc = screen.getByTestId('scenario-prismhr-company')
    expect(within(sc).getByText('all steps passed')).toBeInTheDocument()
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(3) // normalize + two saves, really sent
    expect(calls.map((c) => c.url)).toContain('/api/tenants/' + calls.find((c) => c.url.includes('/companies/ACME'))!.url.split('/')[3] + '/prismhr/companies/ACME')
  })

  it('shows failures and skipped steps when the backend misbehaves', async () => {
    mockFetch([
      { match: /__fixtures\/file/, reply: { text: '{}' } },
      { match: /normalize/, reply: { status: 422, body: { ok: false, counts: {}, issues: [{ severity: 'error', code: 'missing_id', message: 'x', path: null, source_id: null }], records: [] } } },
    ])
    renderWithClient(<TestRuns />)
    await runOnly(/PrismHR · client master/)
    await waitFor(() => expect(screen.getByTestId('run-summary')).toHaveTextContent(/0 of 1/))
    const sc = screen.getByTestId('scenario-prismhr-company')
    expect(within(sc).getAllByText('fail').length).toBeGreaterThanOrEqual(1)
    expect(within(screen.getByTestId('step-prismhr-company-normalize')).getByText('fail')).toBeInTheDocument()
    expect(within(screen.getByTestId('step-prismhr-company-ingest1')).getByText('skipped')).toBeInTheDocument()
    expect(screen.queryByText('all steps passed')).not.toBeInTheDocument()
  })

  it('requires a tenant and at least one scenario before it will run', async () => {
    mockFetch([])
    renderWithClient(<TestRuns />)
    const u = userEvent.setup()
    await u.click(screen.getByRole('button', { name: 'None' }))
    expect(screen.getByRole('button', { name: /Run 0 scenarios/ })).toBeDisabled()
    await u.click(screen.getByLabelText(/PrismHR · client master/))
    await u.clear(screen.getByLabelText(/Tenant for this run/))
    expect(screen.getByRole('button', { name: /Run 1 scenario/ })).toBeDisabled()
  })
})
