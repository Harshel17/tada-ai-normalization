import { describe, expect, it } from 'vitest'
import type { TraceStep } from '../api/types'
import { record } from '../test/utils'
import dests from '../test/fixtures/trace-dests.json'
import { coverageOf, defaultSelection, describeStep, filterSteps, flattenCanonical, groupOf, groupOrder, groupSteps, isMeaningful, opCounts, relativeSource, safeStep, unmappedLeaves, untracedExplanation } from './pipeline'

const step = (over: Partial<TraceStep> = {}): TraceStep => ({ dest: 'first_name', op: 'copy', value: 'Jordan', sources: [{ path: '$.employee[0].firstName', raw: 'Jordan' }], ...over })

describe('pipeline helpers', () => {
  it('flattens only filled scalar fields and skips structural keys', () => {
    const r = record({ first_name: 'Jo', middle_name: null, compensation: { annual_salary: '100.00', pay_type: '' }, tags: ['a', 'b'] })
    const paths = flattenCanonical(r).map((l) => l.path)
    expect(paths).toEqual(expect.arrayContaining(['first_name', 'last_name', 'compensation.annual_salary', 'tags[0]', 'tags[1]']))
    for (const skipped of ['source_id', 'tenant_id', 'provenance', 'extras', 'middle_name', 'compensation.pay_type']) expect(paths.some((p) => p.startsWith(skipped))).toBe(false)
  })

  it('masks sensitive keys in the canonical side too', () => {
    const r = record({ account_number: '000123456789' })
    const leaf = flattenCanonical(r).find((l) => l.path === 'account_number')
    expect(String(leaf?.value)).not.toContain('000123456789')
  })

  it('lists filled fields that have no trace as “not available”, never inventing a source', () => {
    const r = record({ first_name: 'Jo', last_name: 'Ri', job_code: 'X' })
    const missing = unmappedLeaves(r, [step({ dest: 'first_name' })]).map((l) => l.path)
    expect(missing).toContain('last_name')
    expect(missing).toContain('job_code')
    expect(missing).not.toContain('first_name')
  })

  it('shortens a source path relative to its record', () => {
    expect(relativeSource('$.employee[0].client.jobCode', '$.employee[0]')).toBe('client.jobCode')
    expect(relativeSource('$.pay_info.standard_work_day', '$.employee')).toBe('pay_info.standard_work_day')
  })

  it('describes enum, redaction and derived steps in words', () => {
    expect(describeStep(step({ op: 'enum', table: 'EMPLOYMENT_STATUS', value: 'active', sources: [{ path: 'p', raw: 'A' }] }))).toBe('Looked up in the EMPLOYMENT_STATUS table: “A” → “active”')
    expect(describeStep(step({ op: 'redact' }))).toContain('last four')
    expect(describeStep(step({ op: 'derive', note: 'milliseconds divided by 3,600,000' }))).toBe('milliseconds divided by 3,600,000')
  })

  it('filters by search text in any column and by operation', () => {
    const steps = [step(), step({ dest: 'hire_date', op: 'date', value: '2021-03-01', sources: [{ path: '$.employee[0].client.lastHireDate', raw: '2021-03-01' }] })]
    expect(filterSteps(steps, 'hire', 'all').map((s) => s.dest)).toEqual(['hire_date'])
    expect(filterSteps(steps, '2021', 'all')).toHaveLength(1)
    expect(filterSteps(steps, '', 'date')).toHaveLength(1)
    expect(filterSteps(steps, 'zzz', 'all')).toHaveLength(0)
    expect(opCounts(steps)).toEqual([['copy', 1], ['date', 1]])
  })
})

describe('grouping, coverage and default selection', () => {
  const at = (dest: string, op: TraceStep['op'] = 'copy') => step({ dest, op })

  it('derives the group from the real destination path and operation', () => {
    const cases: [string, TraceStep['op'], string][] = [
      ['first_name', 'copy', 'Identity'], ['gender', 'enum', 'Identity'], ['employee_number', 'copy', 'Identity'],
      ['employment_status', 'enum', 'Employment'], ['termination_reason_raw', 'copy', 'Employment'],
      ['hire_date', 'date', 'Dates'], ['birth_date', 'date', 'Dates'], ['job_effective_date', 'date', 'Dates'],
      ['job_code', 'copy', 'Job'], ['manager_id', 'copy', 'Job'], ['department_code', 'copy', 'Job'],
      ['compensation.annual_salary', 'decimal', 'Compensation'], ['compensation.effective_date', 'date', 'Compensation'],
      ['ssn_last4', 'redact', 'Privacy'], ['anything', 'redact', 'Privacy'],
      ['something_else', 'copy', 'Other'],
    ]
    for (const [dest, op, group] of cases) expect(groupOf({ dest, op }), dest).toBe(group)
  })

  it('returns groups in a fixed order and omits empty ones', () => {
    const g = groupSteps([at('compensation.hourly_rate', 'decimal'), at('first_name'), at('ssn_last4', 'redact'), at('hire_date', 'date')])
    expect(g.map((x) => x.group)).toEqual(['Identity', 'Dates', 'Compensation', 'Privacy'])
  })

  it('calls enum, boolean, date, derive and redact conversions meaningful, but not plain copies', () => {
    expect((['enum', 'boolean', 'date', 'derive', 'redact', 'route', 'combine'] as const).every((op) => isMeaningful(at('x', op)))).toBe(true)
    expect((['copy', 'decimal', 'integer'] as const).some((op) => isMeaningful(at('x', op)))).toBe(false)
  })

  it('reports coverage from real counts and never claims completeness', () => {
    const r = record({ first_name: 'Jo', last_name: 'Ri', job_code: 'X' })
    const leaves = flattenCanonical(r).length
    expect(coverageOf(r, [])).toEqual({ traced: 0, total: leaves })
    expect(coverageOf(r, [at('first_name')])).toEqual({ traced: 1, total: leaves })
    expect(coverageOf(r, [at('not_a_field')]).traced).toBe(0)
  })

  it('opens on an employee with evidence, then any unit with evidence, then the first unit', () => {
    const u = (entity: string, traces: TraceStep[][]) => ({ entity, status: 'converted', records: traces.map(() => ({})), traces })
    const s = [at('x')]
    expect(defaultSelection([u('company', [s]), u('employee', [[], s]), u('payroll', [s])])).toEqual({ unit: 1, record: 1 })
    expect(defaultSelection([u('company', [[]]), u('payroll', [[], s])])).toEqual({ unit: 1, record: 1 })
    expect(defaultSelection([u('company', [[]]), u('employee', [[]])])).toEqual({ unit: 0, record: 0 })
    expect(defaultSelection([u('employee', [[]]), u('employee', [s])])).toEqual({ unit: 1, record: 0 })
  })
})

describe('grouping for every record type, checked against the destinations the backend really traces', () => {
  const real = dests as unknown as Record<string, Record<string, string[]>>

  it('knows all six record types and traces something for each', () => {
    expect(Object.keys(real).sort()).toEqual(['company', 'deduction', 'direct_deposit', 'employee', 'paycheck', 'payroll_run'])
  })

  it.each(['company', 'deduction', 'direct_deposit', 'paycheck', 'payroll_run'])('%s: no traced field falls into “Other”', (type) => {
    const other = Object.entries(real[type]!).filter(([dest, ops]) => groupOf({ dest, op: ops[0] as TraceStep['op'] }, type) === 'Other').map(([d]) => d)
    expect(other).toEqual([])
  })

  it.each(Object.keys(real))('%s: every group a field lands in is one the page shows, in a fixed order', (type) => {
    const order = groupOrder(type)
    for (const [dest, ops] of Object.entries(real[type]!)) expect(order, `${type}.${dest}`).toContain(groupOf({ dest, op: ops[0] as TraceStep['op'] }, type))
  })

  it('employees keep their original groups unchanged', () => {
    expect(groupOrder('employee')).toEqual(['Identity', 'Employment', 'Dates', 'Job', 'Compensation', 'Privacy', 'Other'])
    expect(groupOrder()).toEqual(groupOrder('employee'))
    expect(groupOf({ dest: 'hire_date', op: 'date' }, 'employee')).toBe('Dates')
    expect(groupOf({ dest: 'compensation.annual_salary', op: 'decimal' })).toBe('Compensation')
  })

  it('places the mapping journey sensibly for each record type', () => {
    const at = (type: string, dest: string, op: TraceStep['op'] = 'copy') => groupOf({ dest, op }, type)
    expect([at('direct_deposit', 'routing_number'), at('direct_deposit', 'account_type', 'enum'), at('direct_deposit', 'bank_name')]).toEqual(['Account', 'Account', 'Account'])
    expect([at('direct_deposit', 'allocation_type', 'enum'), at('direct_deposit', 'amount', 'route'), at('direct_deposit', 'percentage', 'route')]).toEqual(['How pay is split', 'How pay is split', 'How pay is split'])
    expect([at('direct_deposit', 'effective_from', 'date'), at('direct_deposit', 'active', 'derive')]).toEqual(['Status and dates', 'Status and dates'])
    expect([at('deduction', 'deduction_code'), at('deduction', 'percentage', 'route'), at('deduction', 'start_date', 'date'), at('deduction', 'payee_name'), at('deduction', 'employee_id', 'context')]).toEqual(['What it is', 'Amount and rules', 'Dates and status', 'Payee and references', 'Identity and links'])
    expect([at('deduction', 'calc_params[0].type'), at('deduction', 'calc_params[1].value', 'decimal')]).toEqual(['Amount and rules', 'Amount and rules'])
    expect([at('payroll_run', 'pay_date', 'derive'), at('payroll_run', 'run_type_raw'), at('payroll_run', 'source_id', 'derive')]).toEqual(['Dates', 'The run', 'Identity'])
    expect([at('paycheck', 'gross_pay', 'decimal'), at('paycheck', 'pay_date', 'date'), at('paycheck', 'run_id'), at('paycheck', 'paycheck_type', 'enum')]).toEqual(['Totals', 'Pay period and dates', 'Identity and links', 'Type and status'])
    expect([at('company', 'address.postal_code', 'combine'), at('company', 'legal_entities[0].tax_id'), at('company', 'status', 'enum'), at('company', 'phone')]).toEqual(['Address', 'Legal entities', 'Status and dates', 'Contact'])
  })

  it('withheld values are Privacy whatever the record type, and unknown fields are Other, never guessed', () => {
    for (const type of ['company', 'direct_deposit', 'deduction', 'paycheck', 'employee']) expect(groupOf({ dest: 'account_number_masked', op: 'redact' }, type)).toBe('Privacy')
    expect(groupOf({ dest: 'something_new', op: 'copy' }, 'paycheck')).toBe('Other')
    expect(groupOf({ dest: 'anything', op: 'copy' }, 'unknown_type')).toBe('Other')
  })

  it('returns groups in order for a record type and omits empty ones', () => {
    const steps = [step({ dest: 'net_pay', op: 'decimal' }), step({ dest: 'source_id' }), step({ dest: 'pay_date', op: 'date' })]
    expect(groupSteps(steps, 'paycheck').map((g) => g.group)).toEqual(['Identity and links', 'Pay period and dates', 'Totals'])
  })

  it('treats ids from your input and values set by the normalizer as quiet, and explains them in words', () => {
    expect(isMeaningful(step({ op: 'context' }))).toBe(false)
    expect(isMeaningful(step({ op: 'assigned' }))).toBe(false)
    expect(describeStep(step({ op: 'context', sources: [{ path: 'context.employee_id', raw: 'E1' }] }))).toContain('the id you entered')
    expect(describeStep(step({ op: 'assigned', sources: [], note: 'this response lists recurring deductions' }))).toBe('this response lists recurring deductions')
  })

  it('names the parts that are not traced yet instead of implying a source', () => {
    const leaves = [{ path: 'earnings[0].amount', value: '1' }, { path: 'taxes[1].amount', value: '2' }, { path: 'earnings[1].amount', value: '3' }]
    expect(untracedExplanation(leaves)).toContain('Line items (earnings, taxes) are not traced yet.')
    expect(untracedExplanation([{ path: 'last_name', value: 'x' }])).not.toContain('Line items')
    expect(untracedExplanation([])).toContain('We do not guess')
  })
})

describe('safeStep: a second line of defence in the browser', () => {
  it('hides sources with sensitive key names and hash or credential destinations, whatever the server sent', () => {
    const out = safeStep({ dest: 'account_number_hash', op: 'copy', value: 'deadbeefcafe', sources: [{ path: '$.x.accountNumber', raw: '000123456789' }, { path: '$.x.ssn', raw: '123-45-6789' }, { path: '$.x.password', raw: 'hunter2hunter2' }] })
    expect(JSON.stringify(out)).not.toMatch(/deadbeef|000123456789|123-45-6789|hunter2/)
    expect(out.value).toBe('[withheld]')
    expect(out.sources.every((x) => x.raw === '[withheld]')).toBe(true)
  })

  it('leaves ordinary values and the masked or last-4 outputs alone', () => {
    const copy = step()
    expect(safeStep(copy)).toEqual(copy)
    const masked = safeStep({ dest: 'account_number_masked', op: 'redact', value: '****6789', sources: [{ path: '$.v.accountNum', raw: '[withheld]' }] })
    expect(masked.value).toBe('****6789')
    expect(safeStep({ dest: 'ssn_last4', op: 'redact', value: '6789', sources: [{ path: '$.p.ssn', raw: '[withheld]' }] }).value).toBe('6789')
  })
})

