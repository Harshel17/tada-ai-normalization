import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ConvertResponse, Detection, DetectedUnit, DetectResponse, UnitResult } from '../api/types'
import { ConversionProvider } from '../state/conversion'
import { mockFetch, record, renderWithClient, repoFixture } from '../test/utils'
import type { Call } from '../test/utils'
import { Normalize } from './Normalize'

vi.mock('../components/JsonEditor', () => ({
  JsonEditor: ({ value, ariaLabel }: { value: string; ariaLabel: string }) => <textarea aria-label={ariaLabel} value={value} readOnly />,
}))
afterEach(() => vi.unstubAllGlobals())
const user = () => userEvent.setup()

const ISOLVED = ['direct_deposits.json', 'employees.json']
const unit = (entity: DetectedUnit['entity'], over: Partial<DetectedUnit> = {}): DetectedUnit => ({
  entity, shape: 'x', label: entity, keys: null, counts: { [entity]: 1 }, derived_counts: {}, evidence: ['e'], requires: [], optional: [], nested_in: null, convention: false, strength: 'strong', notes: [], ...over,
})
const ok = (...units: DetectedUnit[]): Detection => ({ status: 'ok', units, candidates: [], warnings: [], message: null, provider_hint: null })

/** The isolved example set as the real detection reports it: the deposit list has no owner, the employees file does. */
function isolvedDetect(order: string[] = ['90001', '90002']): DetectResponse {
  return {
    provider: 'isolved',
    files: [
      { name: 'direct_deposits.json', detection: ok(unit('direct_deposit', { counts: { direct_deposit: 3 }, requires: ['company_id', 'employee_id'], notes: ['A bare deposit list does not say which employee it belongs to.'] })) },
      { name: 'employees.json', detection: ok(unit('employee', { counts: { employee: 2 }, requires: ['company_id'] }), unit('direct_deposit', { counts: { direct_deposit: 1 }, nested_in: 'employee', requires: ['company_id'] })) },
    ],
    suggestions: [{ field: 'company_id', value: '4501', source_file: 'employees.json', why: 'legal' }, ...order.map((v) => ({ field: 'employee_id', value: v, source_file: 'employees.json', why: 'employee[].id' }))],
  }
}
const result = (over: Partial<UnitResult>): UnitResult => ({ file: 'f.json', entity: 'employee', label: 'x', status: 'converted', missing: [], counts: {}, issues: [], records: [], traces: [], ...over })
const convertedIsolved = (owner: string): ConvertResponse => ({
  provider: 'isolved', suggestions: [], files: isolvedDetect().files,
  results: [
    result({ file: 'direct_deposits.json', entity: 'direct_deposit', counts: { direct_deposit: 3 }, records: ['301', '302', '303'].map((id) => record({ record_type: 'direct_deposit', source_id: id, employee_id: owner })) }),
    result({ file: 'employees.json', entity: 'employee', counts: { employee: 2 }, records: [record({ source_id: '90001' }), record({ source_id: '90002' })] }),
    result({ file: 'employees.json', entity: 'direct_deposit', counts: { direct_deposit: 1 }, records: [record({ record_type: 'direct_deposit', source_id: '301', employee_id: '90001' })] }),
  ],
})

function routes(detect: DetectResponse = isolvedDetect(), convert: (c: Call) => unknown = () => convertedIsolved('90001')) {
  return [
    { match: /__fixtures\/index/, reply: { body: { fixtures: ISOLVED.map((name) => ({ dir: 'isolved', name })) } } },
    { match: /__fixtures\/file\?dir=/, reply: (call: Call) => { const u = new URL(call.url, 'http://x'); return { text: repoFixture(u.searchParams.get('dir')!, u.searchParams.get('name')!) } } },
    { match: /\/detect\//, reply: { body: detect } },
    { match: /\/convert\//, reply: (c: Call) => ({ body: convert(c) }) },
    { match: /\/stats/, reply: { body: { providers: [], entity_types: [], scopes: [] } } },
  ]
}
const mount = () => renderWithClient(<ConversionProvider><Normalize /></ConversionProvider>)
const converts = (calls: Call[]) => calls.filter((c) => c.method === 'POST' && /\/convert\//.test(c.url))

describe('the one-click isolved example', () => {
  it('uses the DOCUMENTED owner (90001), shows it as documented, and states it is not verified', async () => {
    const { calls } = mockFetch(routes())
    mount()
    await user().click(screen.getByTestId('quick-isolved'))
    const note = await screen.findByTestId('ownership-notice')
    const sent = JSON.parse(converts(calls)[0]!.body!)
    expect(sent.context).toMatchObject({ company_id: '4501', employee_id: '90001' })
    expect(note).toHaveTextContent('Owner assigned from the Employee ID used by this example set')
    expect(note).toHaveTextContent('3 direct deposits in direct_deposits.json were assigned to employee 90001')
    expect(note).toHaveTextContent(/not something the files or the integrity checks can verify/)
    expect(note).not.toHaveTextContent(/employees\.json were assigned/) // the embedded deposit keeps its own owner
  })

  it('does not take "the first employee found": the order of the suggestions changes nothing', async () => {
    const { calls } = mockFetch(routes(isolvedDetect(['90002', '90001'])))
    mount()
    await user().click(screen.getByTestId('quick-isolved'))
    await screen.findByTestId('results')
    expect(JSON.parse(converts(calls)[0]!.body!).context.employee_id).toBe('90001')
  })

  it('a reviewer can still pick the other existing employee: it is accepted, flagged as a confirmation, and the embedded owner is untouched', async () => {
    mockFetch(routes(isolvedDetect(), (c) => convertedIsolved(JSON.parse(c.body!).context.employee_id)))
    mount()
    await user().click(screen.getByRole('radio', { name: /isolved/ }))
    await user().click(screen.getByRole('button', { name: 'Try example data' }))
    const field = await screen.findByTestId('owner-field')
    expect(within(field).getByTestId('owner-documented')).toHaveTextContent('Example set’s convention for the bundled file direct_deposits.json: 90001')
    await user().click(within(screen.getByTestId('context-box')).getAllByRole('button', { name: /Use 4501/ })[0]!)
    await user().click(within(field).getByRole('button', { name: /Use 90002/ }))
    await user().click(within(field).getByRole('checkbox', { name: /I confirm that employee 90002 owns the direct deposits/ }))
    await user().click(screen.getByRole('button', { name: 'Normalize Data' }))
    const note = await screen.findByTestId('ownership-notice')
    expect(note).toHaveTextContent('Owner assigned from the Employee ID you confirmed')
    expect(note).toHaveTextContent('assigned to employee 90002')
    expect(note).toHaveTextContent(/This is your confirmation\. It is an acknowledgment/)
  })
})

describe('custom uploads', () => {
  const upload = async (name: string) => {
    await user().click(screen.getByRole('radio', { name: /isolved/ }))
    const input = screen.getByTestId('upload') as HTMLInputElement
    await user().upload(input, new File([repoFixture('isolved', 'direct_deposits.json')], name, { type: 'application/json' }))
    await screen.findByTestId('owner-field')
  }
  const nothingFound: DetectResponse = { ...isolvedDetect(), files: [isolvedDetect().files[0]!], suggestions: [{ field: 'company_id', value: '4501', source_file: 'x', why: 'legal' }].slice(0, 0) }

  it('with no ownership information anywhere there is nothing to pick: the owner must be typed and confirmed', async () => {
    const { calls } = mockFetch(routes(nothingFound))
    mount()
    await upload('mine.json')
    const field = screen.getByTestId('owner-field')
    expect(within(field).queryByTestId('owner-candidates')).not.toBeInTheDocument()
    expect(within(field).queryByTestId('owner-documented')).not.toBeInTheDocument()
    expect(within(field).getByLabelText(/Employee ID/)).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Normalize Data' })).toBeDisabled()
    expect(screen.getByTestId('blocked')).toHaveTextContent(/Confirm: Employer ID, Employee ID|Confirm which employee owns/)
    expect(converts(calls)).toHaveLength(0)
  })

  it('a file that merely shares a bundled example’s NAME gets no documented owner', async () => {
    mockFetch(routes(nothingFound))
    mount()
    await upload('direct_deposits.json')
    expect(screen.queryByTestId('owner-documented')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Employee ID/)).toHaveValue('')
  })

  it('employees found in other uploaded files are candidates, never assumed', async () => {
    mockFetch(routes())
    mount()
    await upload('mine.json')
    const text = screen.getByTestId('owner-candidates').textContent ?? ''
    expect(text).toMatch(/2 employees appear in your files/)
    expect(screen.getByLabelText(/Employee ID/)).toHaveValue('')
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /I confirm that employee … owns/ })).toBeDisabled())
  })
})
