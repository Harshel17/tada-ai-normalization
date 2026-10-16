import { describe, expect, it } from 'vitest'
import { record } from '../test/utils'
import { describeRecord, fieldLabel, recordHeading } from './describe'

/** label -> row for every row of every section of a record */
const flat = (r: Parameters<typeof describeRecord>[0]) => new Map(describeRecord(r).flatMap((s) => s.rows.map((x) => [x.label, x] as const)))

describe('describeRecord: employee', () => {
  const emp = record({
    first_name: 'Jordan', last_name: 'Rivera', employee_number: '1001', birth_date: '1988-04-17', gender: 'female', gender_raw: 'F',
    ssn_last4: '6789', email: 'j@example.test', employment_status: 'active', employment_status_raw: 'ACT', hire_date: '2021-03-01', company_id: 'ACME',
    job_code: 'ENG2', address: { line1: '12 Main St', city: 'Austin', state: 'TX', postal_code: '78701' },
    compensation: { pay_type: 'salary', pay_type_raw: 'S', annual_salary: '100000.00', hourly_rate: '0' },
    tax_withholdings: [{}, {}], emergency_contacts: [{}],
  })
  it('puts identity, employment, pay set-up and other details into named sections', () => {
    expect(describeRecord(emp).map((s) => s.title)).toEqual(['Who this is', 'Employment', 'Pay set-up', 'Other details converted'])
    expect(recordHeading(emp)).toBe('Jordan Rivera')
  })
  it('uses plain words and keeps the provider’s own code as a note', () => {
    const rows = flat(emp)
    expect(rows.get('Status')!).toMatchObject({ value: 'Active', note: expect.stringContaining('ACT') })
    expect(rows.get('Gender')!).toMatchObject({ value: 'Female', note: expect.stringContaining('“F”') })
    expect(rows.get('Paid')!).toMatchObject({ value: 'Salary' })
    expect(rows.get('Annual salary')!.value).toBe('100000.00')
    expect(rows.get('Home address')!.value).toBe('12 Main St, Austin, TX 78701')
    expect(rows.get('SSN')!.value).toBe('Last 4 digits only: 6789')
    expect(rows.get('Tax withholding elections')!.value).toBe('2')
  })
  it('keeps zero as a value and leaves out what the record does not have', () => {
    const rows = flat(emp)
    expect(rows.get('Hourly rate')!.value).toBe('0') // zero is a real value
    expect(rows.get('Left on')).toBeUndefined()
    expect(rows.get('Job title')).toBeUndefined()
  })
  it('shows an unrecognised status exactly as received, and says why', () => {
    const rows = flat(record({ employment_status: null, employment_status_raw: 'XYZ', gender: null, gender_raw: 'X' }))
    expect(rows.get('Status')!).toMatchObject({ value: 'XYZ', note: expect.stringContaining('does not interpret') })
    expect(rows.get('Gender')!.note).toMatch(/does not explain/)
  })
})

describe('describeRecord: other kinds of data', () => {
  it('company', () => {
    const rows = flat(record({ record_type: 'company', legal_name: 'Acme Widgets, Inc.', status: 'active', status_raw: 'ACT', federal_ein: '12-3456789', source_id: 'ACME' }))
    expect(rows.get('Legal name')!.value).toBe('Acme Widgets, Inc.')
    expect(rows.get('Tax ID (EIN)')!.value).toBe('12-3456789')
    expect(rows.get('Status')!.value).toBe('Active')
  })
  it('direct deposit explains the split rule in words and never shows a full account', () => {
    const fixed = flat(record({ record_type: 'direct_deposit', bank_name: 'First Test Bank', account_number_masked: '****6789', allocation_type: 'fixed_amount', amount: '500.00', account_type: 'checking' }))
    expect(fixed.get('Rule')!.value).toBe('A fixed amount')
    expect(fixed.get('Amount')!.value).toBe('500.00')
    expect(fixed.get('Account')!.value).toBe('****6789')
    const pct = flat(record({ record_type: 'direct_deposit', allocation_type: 'percentage', percentage: '25.5' }))
    expect(pct.get('Amount')!.value).toBe('25.5%')
    const rest = flat(record({ record_type: 'direct_deposit', allocation_type: 'remaining_balance' }))
    expect(rest.get('Rule')!.value).toBe('Whatever is left after the other deposits')
    const unknown = flat(record({ record_type: 'direct_deposit', allocation_type: null, allocation_raw: 'ZZ' }))
    expect(unknown.get('Rule')!.value).toBe('Provider code “ZZ”')
  })
  it('deduction distinguishes an amount from a percentage', () => {
    expect(flat(record({ record_type: 'deduction', name: 'Parking', setup_kind: 'scheduled', amount: '25.50' })).get('Amount taken')!.value).toBe('25.50')
    expect(flat(record({ record_type: 'deduction', percentage: '6.00', setup_kind: 'recurring' })).get('Percent of pay')!.value).toBe('6.00%')
    expect(flat(record({ record_type: 'deduction', name: 'x', setup_kind: 'configured' })).get('Kind')!.value).toBe('Configured for the employee')
  })
  it('pay run keeps the pay date and the work period apart', () => {
    const rows = flat(record({ record_type: 'payroll_run', pay_date: '2025-01-17', period_start: '2025-01-04', period_end: '2025-01-17' }))
    expect(rows.get('Paid on')!.value).toBe('2025-01-17')
    expect(rows.get('Work period covered')!.value).toBe('2025-01-04 to 2025-01-17')
  })
  it('paycheck lists real line items and flags a calculated gross', () => {
    const r = record({ record_type: 'paycheck', employee_id: 'E1', gross_pay: '3846.15', net_pay: '2410.37',
      earnings: [{ line_no: 1, code: 'REG', description: 'Regular', amount: '3846.15' }],
      taxes: [{ line_no: 1, code: 'FIT', amount: '512.30', liability: 'employee' }] })
    r.provenance.derived_fields = ['gross_pay']
    const rows = flat(r)
    expect(rows.get('Gross pay')!.note).toMatch(/Added up by this system/)
    expect(rows.get('Earned: REG · Regular')!.value).toBe('3846.15')
    expect(rows.get('Tax: FIT')!.note).toBe('Paid by the employee')
  })
  it('returns no sections for an unknown record type rather than inventing content', () => {
    expect(describeRecord(record({ record_type: 'mystery' }))).toEqual([])
  })
})

describe('fieldLabel', () => {
  it('turns field names into plain labels', () => {
    expect(fieldLabel('hire_date')).toBe('Hire date')
    expect(fieldLabel('compensation.annual_salary')).toBe('Pay: annual salary')
    expect(fieldLabel('org_assignments.kind')).toBe('Org assignments: kind')
    expect(fieldLabel('some_code_raw')).toBe('Some code (provider’s code)')
  })
})

describe('describeRecord: the second coverage step', () => {
  const emp = record({
    first_name: 'Ada', last_name: 'Lovelace', employee_type_effective_date: '2023-04-17', union_start_date: '2019-09-02', aca_employment_status_raw: 'Full-Time',
    compensation: { annual_salary: '100000.00', last_pay_date: '2026-09-30', next_review_date: '2027-01-15', compa_ratio: '0.9750', standard_hours_per_week: '40',
      last_performance_review_date: '2026-03-01', last_performance_review_rating_raw: 'Exceeds', last_performance_review_job_title: 'Staff Engineer', next_performance_review_date: '2027-03-01' },
  })
  it('shows each newly typed field with a plain label and its value', () => {
    const rows = flat(emp)
    expect(rows.get('Employment type effective')?.value).toBe('2023-04-17')
    expect(rows.get('Union member since')?.value).toBe('2019-09-02')
    expect(rows.get('ACA employment status (provider’s label)')?.value).toBe('Full-Time')
    expect(rows.get('Last paid')?.value).toBe('2026-09-30')
    expect(rows.get('Compa-ratio (as the provider gives it)')?.value).toBe('0.9750')
    expect(rows.get('Standard hours per week')?.value).toBe('40')
    expect(rows.get('Last performance review')?.note).toBe('Rating as the provider gave it: “Exceeds”')
    expect(rows.get('Job title at that review')?.value).toBe('Staff Engineer')
  })
  it('leaves out what the record does not have, and labels the new trace destinations', () => {
    const rows = flat(record({ first_name: 'B', compensation: { annual_salary: '1.00' } }))
    for (const label of ['Last paid', 'Compa-ratio (as the provider gives it)', 'Union member since', 'Standard hours per week']) expect(rows.has(label)).toBe(false)
    expect(fieldLabel('compensation.last_pay_date')).toBe('Pay: last paid')
    expect(fieldLabel('union_start_date')).toBe('Union member since')
  })
})
