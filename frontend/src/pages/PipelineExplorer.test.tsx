import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect } from 'react'
import { describe, expect, it } from 'vitest'
import type { ConvertResponse, FileDetection, IdSuggestion, TraceStep, UnitResult } from '../api/types'
import { ConversionProvider, useConversion } from '../state/conversion'
import { record, renderWithClient } from '../test/utils'
import { PipelineExplorer } from './PipelineExplorer'

const steps: TraceStep[] = [
  { dest: 'first_name', op: 'copy', value: 'Jordan', sources: [{ path: '$.employee[0].firstName', raw: 'Jordan' }] },
  { dest: 'employment_status', op: 'enum', value: 'active', table: 'EMPLOYMENT_STATUS', sources: [{ path: '$.employee[0].client.statusClass', raw: 'A' }] },
  { dest: 'ssn_last4', op: 'redact', value: '6789', sources: [{ path: '$.employee[0].compensation.ssn', raw: '[withheld]' }] },
  { dest: 'hire_date', op: 'date', value: '2021-03-01', sources: [{ path: '$.employee[0].client.lastHireDate', raw: '2021-03-01' }] },
  { dest: 'compensation.annual_salary', op: 'decimal', value: '100000.00', sources: [{ path: '$.employee[0].compensation.annualPay', raw: '100000.00' }] },
]
const data = (traces: TraceStep[][] = [steps, []]): ConvertResponse => ({
  provider: 'prismhr', files: [], suggestions: [],
  results: [{
    file: 'employee.json', entity: 'employee', label: 'x', status: 'converted', missing: [], counts: { employee: 2 }, issues: [],
    records: [
      record({ first_name: 'Jordan', last_name: 'Rivera', ssn_last4: '6789', employment_status: 'active', hire_date: '2021-03-01', compensation: { annual_salary: '100000.00' }, extras: { person: { ethnicCode: '2' } }, provenance: { source_endpoint: 'x', source_path: '$.employee[0]', redacted_paths: ['$.employee[0].compensation.ssn'], derived_fields: [] } }),
      record({ source_id: 'E2', first_name: 'Casey', last_name: 'Nguyen' }),
    ],
    traces,
  }],
})

function Seed({ value }: { value: ConvertResponse | null }) {
  const { setConversion } = useConversion()
  useEffect(() => setConversion(value), [value, setConversion])
  return null
}
const mount = (value: ConvertResponse | null) => renderWithClient(<ConversionProvider><Seed value={value} /><PipelineExplorer /></ConversionProvider>)

describe('Pipeline Explorer', () => {
  it('asks for a conversion first when there is none', () => {
    mount(null)
    expect(screen.getByText('Nothing to explore yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Normalize' })).toHaveAttribute('href', '#/normalize')
  })

  it('shows original field+value → what was done → standard field+value for each traced field', async () => {
    mount(data())
    const rows = await screen.findAllByTestId('pipeline-row')
    expect(rows).toHaveLength(5)
    const first = rows[0]!
    expect(within(first).getByTestId('origin')).toHaveTextContent('firstName')
    expect(within(first).getByTestId('origin')).toHaveTextContent('Jordan')
    expect(first).toHaveTextContent('Copied as text')
    expect(within(first).getByTestId('destination')).toHaveTextContent('first_name')
    expect(rows[1]).toHaveTextContent('EMPLOYMENT_STATUS')
    expect(rows[1]).toHaveTextContent('A')
    expect(rows[1]).toHaveTextContent('active')
    expect(rows[1]).toHaveTextContent('client.statusClass')
  })

  it('never shows a raw sensitive value; the source side is withheld', async () => {
    mount(data())
    const ssn = (await screen.findAllByTestId('pipeline-row')).find((r) => r.textContent?.includes('ssn_last4'))!
    expect(within(ssn).getByTestId('origin')).toHaveTextContent('[withheld]')
    expect(ssn).toHaveTextContent('6789')
    expect(screen.getByTestId('withheld')).toHaveTextContent('compensation.ssn')
  })

  it('lists values without a recorded mapping as “Source mapping not available” and invents no source', async () => {
    mount(data())
    const box = await screen.findByTestId('unmapped')
    expect(box).toHaveTextContent('Source mapping not available (1)')
    expect(box).toHaveTextContent('last_name')
    expect(box).toHaveTextContent('Rivera')
  })

  it('reports provider-specific data kept as received, by key only', async () => {
    mount(data())
    expect(await screen.findByTestId('preserved')).toHaveTextContent('person')
    expect(screen.getByTestId('preserved')).not.toHaveTextContent('ethnicCode')
  })

  it('searches and filters', async () => {
    mount(data())
    await screen.findAllByTestId('pipeline-row')
    await userEvent.setup().type(screen.getByRole('searchbox', { name: 'Search the pipeline' }), 'status')
    expect(screen.getAllByTestId('pipeline-row')).toHaveLength(1)
    await userEvent.setup().clear(screen.getByRole('searchbox', { name: 'Search the pipeline' }))
    await userEvent.setup().click(within(screen.getByRole('group', { name: 'Filter by what was done' })).getByRole('button', { name: /Withheld/ }))
    expect(screen.getAllByTestId('pipeline-row')).toHaveLength(1)
    // the operation filter and the search combine
    await userEvent.setup().type(screen.getByRole('searchbox', { name: 'Search the pipeline' }), 'nomatch')
    expect(screen.queryAllByTestId('pipeline-row')).toHaveLength(0)
    expect(screen.getByText('No fields match your search or filters.')).toBeInTheDocument()
  })

  it('switches records, and says plainly when a record has no recorded mappings', async () => {
    mount(data())
    await screen.findAllByTestId('pipeline-row')
    await userEvent.setup().click(screen.getByRole('tab', { name: /Casey/ }))
    expect(screen.queryAllByTestId('pipeline-row')).toHaveLength(0)
    expect(screen.getByTestId('no-trace')).toHaveTextContent('Source mapping is not available')
    expect(screen.getByTestId('unmapped')).toHaveTextContent('Casey')
  })

  it('shows a compact header from the real conversion: provider, data, record, status and actual coverage', async () => {
    mount(data())
    const head = await screen.findByTestId('pipeline-header')
    expect(head).toHaveTextContent('PrismHR')
    expect(head).toHaveTextContent('Employee records')
    expect(within(head).getByTestId('selected-record')).toHaveTextContent('Jordan')
    expect(head).toHaveTextContent('converted')
    const filled = 8 // first_name, last_name, ssn_last4, employment_status, hire_date, compensation.annual_salary + 2 defaults
    expect(within(head).getByTestId('coverage').textContent).toMatch(/^5 of \d+ fields/)
    expect(filled).toBeGreaterThan(5)
  })

  it('groups mappings by their real destination: Identity, Employment, Dates, Compensation, Privacy', async () => {
    mount(data())
    await screen.findAllByTestId('pipeline-row')
    for (const g of ['Identity', 'Employment', 'Dates', 'Compensation', 'Privacy']) expect(screen.getByTestId(`group-${g}`)).toBeInTheDocument()
    expect(screen.queryByTestId('group-Job')).not.toBeInTheDocument() // no job fields were mapped: no empty group
    expect(within(screen.getByTestId('group-Dates')).getByText('hire_date')).toBeInTheDocument()
    expect(within(screen.getByTestId('group-Privacy')).getByText('ssn_last4')).toBeInTheDocument()
    expect(screen.getByTestId('group-Employment')).toHaveTextContent('1 converted') // the enum
  })

  it('opens on the employee record that has mapping evidence, not the first record or unit', async () => {
    const d = data([[], steps])
    d.results.unshift({ ...d.results[0]!, entity: 'company', file: 'client_master.json', records: [record({ record_type: 'company', source_id: 'C1' })], traces: [[]] })
    mount(d)
    await screen.findAllByTestId('pipeline-row')
    expect(screen.getByTestId('selected-record')).toHaveTextContent('Casey') // record 0 has no trace; record 1 does
    expect(screen.getByRole('tab', { name: /Casey/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('Choose converted data')).toHaveDisplayValue(/Employee records/)
    // and every converted record is still reachable
    await userEvent.setup().click(screen.getByRole('tab', { name: /Jordan/ }))
    expect(screen.getByTestId('no-trace')).toBeInTheDocument()
  })

  it('keeps untraced fields in a separate, collapsed section and never gives them a source', async () => {
    mount(data())
    const box = await screen.findByTestId('unmapped')
    expect(box).not.toHaveAttribute('open')
    expect(box.tagName).toBe('DETAILS')
    expect(box).toHaveTextContent('Source mapping not available (')
    expect(box).toHaveTextContent('last_name')
    const rows = screen.getAllByTestId('pipeline-row').map((r) => r.textContent)
    expect(rows.some((t) => t?.includes('last_name'))).toBe(false)
    expect(screen.getByTestId('pipeline')).not.toHaveTextContent('100% ')
  })

  it('expands a row for details and collapses it again', async () => {
    mount(data())
    const row = (await screen.findAllByTestId('pipeline-row')).find((r) => r.textContent?.includes('employment_status'))!
    const toggle = within(row).getByRole('button')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.setup().click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const details = within(row).getByTestId('row-details')
    expect(details).toHaveTextContent('Lookup table: EMPLOYMENT_STATUS')
    expect(details).toHaveTextContent('$.employee[0].client.statusClass')
    await userEvent.setup().click(toggle)
    expect(within(row).queryByTestId('row-details')).not.toBeInTheDocument()
  })

  it('“Only real conversions” hides plain copies and keeps enum, date and withheld rows', async () => {
    mount(data())
    await screen.findAllByTestId('pipeline-row')
    await userEvent.setup().click(screen.getByRole('checkbox', { name: 'Only real conversions' }))
    const text = screen.getAllByTestId('pipeline-row').map((r) => r.textContent ?? '')
    expect(text.some((t) => t.includes('first_name'))).toBe(false)
    expect(text.some((t) => t.includes('employment_status'))).toBe(true)
    expect(text.some((t) => t.includes('hire_date'))).toBe(true)
    expect(text.some((t) => t.includes('ssn_last4'))).toBe(true)
  })

  it('never shows a full SSN, account number or hash even if a record contains one', async () => {
    const d = data()
    d.results[0]!.records[0] = { ...d.results[0]!.records[0]!, account_number: '000123456789', account_hash: 'deadbeefcafe' }
    mount(d)
    await screen.findAllByTestId('pipeline-row')
    const text = document.body.textContent ?? ''
    expect(text).not.toContain('000123456789')
    expect(text).not.toContain('deadbeefcafe')
  })

  describe('record types beyond employees', () => {
    const base = { provider: 'prismhr', files: [] as FileDetection[], suggestions: [] as IdSuggestion[] }
    const unitOf = (entity: UnitResult['entity'], rec: ReturnType<typeof record>, traces: TraceStep[], file = 'f.json'): UnitResult => ({
      file, entity, label: 'x', status: 'converted', missing: [], counts: { [rec.record_type]: 1 }, issues: [], records: [rec], traces: [traces],
    })
    const depositSteps: TraceStep[] = [
      { dest: 'routing_number', op: 'copy', value: '111000025', sources: [{ path: '$.employee[0].directDeposit.achVoucher[0].transitNum', raw: '111000025' }] },
      { dest: 'account_number_masked', op: 'redact', value: '****6789', sources: [{ path: '$.employee[0].directDeposit.achVoucher[0].accountNum', raw: '[withheld]' }] },
      { dest: 'allocation_type', op: 'enum', value: 'fixed_amount', table: 'ACH_METHOD', sources: [{ path: '$.employee[0].directDeposit.achVoucher[0].method', raw: 'F' }] },
      { dest: 'amount', op: 'route', value: '500.00', note: 'method F: a fixed amount', sources: [{ path: '$.employee[0].directDeposit.achVoucher[0].amount', raw: '500.00' }] },
      { dest: 'effective_from', op: 'date', value: '2024-01-01', sources: [{ path: '$.employee[0].directDeposit.achVoucher[0].start', raw: '2024-01-01' }] },
      { dest: 'employee_id', op: 'copy', value: 'E1001', sources: [{ path: '$.employee[0].id', raw: 'E1001' }] },
    ]
    const deposit = () => record({ record_type: 'direct_deposit', source_id: '111000025-6789-C-F-', employee_id: 'E1001', routing_number: '111000025', account_number_masked: '****6789', allocation_type: 'fixed_amount', amount: '500.00', effective_from: '2024-01-01', provenance: { source_endpoint: 'x', source_path: '$.employee[0].directDeposit.achVoucher[0]', redacted_paths: ['$.employee[0].directDeposit.achVoucher[0].accountNum'], derived_fields: [] } })

    it('groups a direct deposit by account, split rule and dates, with the account withheld under Privacy', async () => {
      mount({ ...base, results: [unitOf('direct_deposit', deposit(), depositSteps)] })
      await screen.findAllByTestId('pipeline-row')
      for (const g of ['Account', 'How pay is split', 'Status and dates', 'Identity and links', 'Privacy']) expect(screen.getByTestId(`group-${g}`)).toBeInTheDocument()
      expect(screen.queryByTestId('group-Identity')).not.toBeInTheDocument() // employee-only group names are not used here
      expect(within(screen.getByTestId('group-Privacy')).getByText('account_number_masked')).toBeInTheDocument()
      expect(within(screen.getByTestId('group-Privacy')).getByTestId('origin')).toHaveTextContent('[withheld]')
      expect(within(screen.getByTestId('group-How pay is split')).getAllByTestId('destination').map((d) => d.textContent)).toEqual(expect.arrayContaining([expect.stringContaining('amount')]))
      expect(screen.getByTestId('pipeline-header')).toHaveTextContent('Bank deposit set-up')
    })

    it('shows “No source field” for a value the normalizer set, and the entered id for a context value', async () => {
      const ded = record({ record_type: 'deduction', source_id: 'D1', setup_kind: 'recurring', employee_id: 'E1001', deduction_code: '401K' })
      const steps: TraceStep[] = [
        { dest: 'setup_kind', op: 'assigned', value: 'recurring', note: 'this response lists the employee’s recurring deductions', sources: [] },
        { dest: 'employee_id', op: 'context', value: 'E1001', sources: [{ path: 'context.employee_id', raw: 'E1001' }] },
        { dest: 'deduction_code', op: 'copy', value: '401K', sources: [{ path: '$.employeeDeductions.deduction[0].deductionCode', raw: '401K' }] },
      ]
      mount({ ...base, results: [unitOf('deduction', ded, steps)] })
      const rows = await screen.findAllByTestId('pipeline-row')
      const assigned = rows.find((r) => r.textContent?.includes('setup_kind'))!
      expect(within(assigned).getByTestId('no-source')).toHaveTextContent('No source field')
      expect(assigned).toHaveTextContent('Set by the normalizer')
      const context = rows.find((r) => r.textContent?.includes('employee_id'))!
      expect(within(context).getByTestId('origin')).toHaveTextContent('context.employee_id')
      expect(context).toHaveTextContent('Taken from your input')
      expect(screen.getByTestId('group-What it is')).toHaveTextContent('setup_kind')
      expect(screen.getByTestId('group-Identity and links')).toHaveTextContent('employee_id')
    })

    it('explains that paycheck line items are not traced yet, and still groups the header', async () => {
      const check = record({ record_type: 'paycheck', source_id: 'V1', gross_pay: '3846.15', pay_date: '2025-01-17', earnings: [{ amount: '3846.15', code: 'REG' }], taxes: [{ amount: '1.00' }] })
      const steps: TraceStep[] = [
        { dest: 'gross_pay', op: 'decimal', value: '3846.15', sources: [{ path: '$.payrollVouchers[0].totalEarnings', raw: '3846.15' }] },
        { dest: 'pay_date', op: 'date', value: '2025-01-17', sources: [{ path: '$.payrollVouchers[0].payDate', raw: '2025-01-17' }] },
      ]
      mount({ ...base, results: [unitOf('payroll', check, steps)] })
      await screen.findAllByTestId('pipeline-row')
      expect(screen.getByTestId('group-Totals')).toHaveTextContent('gross_pay')
      expect(screen.getByTestId('group-Pay period and dates')).toHaveTextContent('pay_date')
      expect(screen.getByTestId('untraced-why')).toHaveTextContent('Line items (earnings, taxes) are not traced yet.')
      expect(screen.getByTestId('unmapped')).toHaveTextContent('earnings[0].amount')
    })

    it('shows a derived run with its real voucher paths and the reason, never a single invented source', async () => {
      const run = record({ record_type: 'payroll_run', source_id: 'B-1', pay_date: '2025-01-17', provenance: { source_endpoint: 'x', source_path: null, redacted_paths: [], derived_fields: ['pay_date'] } })
      const steps: TraceStep[] = [{ dest: 'pay_date', op: 'derive', value: '2025-01-17', note: 'derived, not copied: all 2 voucher(s) of batch B-1 carry this date', sources: [{ path: '$.payrollVouchers[0].payDate', raw: '2025-01-17' }, { path: '$.payrollVouchers[1].payDate', raw: '2025-01-17' }] }]
      mount({ ...base, results: [unitOf('payroll', run, steps)] })
      const row = (await screen.findAllByTestId('pipeline-row'))[0]!
      expect(within(row).getAllByTestId('origin')).toHaveLength(1)
      expect(row).toHaveTextContent('payrollVouchers[0].payDate')
      expect(row).toHaveTextContent('payrollVouchers[1].payDate')
      await userEvent.setup().click(within(row).getByRole('button'))
      expect(within(row).getByTestId('row-details')).toHaveTextContent('derived, not copied')
      expect(screen.getByTestId('group-Dates')).toBeInTheDocument()
    })

    it('never shows a full account number, SSN or hash from a trace', async () => {
      const bad: TraceStep[] = [...depositSteps, { dest: 'account_number_hash', op: 'copy', value: 'deadbeefcafe', sources: [{ path: '$.x.accountNumber', raw: '000123456789' }] }]
      mount({ ...base, results: [unitOf('direct_deposit', { ...deposit(), account_number: '000123456789' }, bad)] })
      await screen.findAllByTestId('pipeline-row')
      const text = document.body.textContent ?? ''
      expect(text).not.toContain('000123456789')
      expect(text).not.toContain('deadbeefcafe')
    })
  })
})

