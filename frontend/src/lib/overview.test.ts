import { describe, expect, it } from 'vitest'
import type { ConvertResponse, UnitResult } from '../api/types'
import { record } from '../test/utils'
import { overviewOf } from './overview'

const unit = (over: Partial<UnitResult>): UnitResult => ({ file: 'f.json', entity: 'payroll', label: 'x', status: 'converted', missing: [], counts: {}, issues: [], records: [], traces: [], ...over })

describe('overviewOf', () => {
  const emp = record({ source_id: 'E1', first_name: 'A', last_name: 'B', ssn_last4: '4417', extras: { a: 1, b: { c: 2 } }, provenance: { source_endpoint: null, source_path: null, redacted_paths: ['$.x.ssn'], derived_fields: [] } })
  const run = record({ record_type: 'payroll_run', source_id: 'R1', extras: {} })
  const check = record({ record_type: 'paycheck', source_id: 'P1', run_id: 'R1', employee_id: 'E1', extras: {} })
  const orphan = record({ record_type: 'paycheck', source_id: 'P2', run_id: 'R9', employee_id: 'E1', extras: {} })
  const dep = record({ record_type: 'direct_deposit', source_id: 'D1', employee_id: 'E1', account_number_masked: '****7788', extras: {} })
  const conv: ConvertResponse = {
    provider: 'prismhr', files: [], suggestions: [],
    results: [unit({ records: [emp, run, check, orphan, dep], issues: [{ severity: 'warning', code: 'w', message: 'm', path: null, source_id: null }] }), unit({ status: 'failed', records: [record({ source_id: 'IGNORED' })] })],
    integrity: { summary: { passed: 5, warning: 1, failed: 0, not_evaluated: 2 }, checks: [] },
  }
  const o = overviewOf(conv)
  it('counts records from converted sections only', () => {
    expect(o.records).toBe(5)
    expect(o.byType).toEqual({ employee: 1, payroll_run: 1, paycheck: 2, direct_deposit: 1 })
  })
  it('counts extras and protected values from the records, separately from typed fields', () => {
    expect(o.unmappedValues).toBe(2)
    expect(o.withheld).toBe(1)
    expect(o.masked).toBe(2) // one last-four SSN, one masked account
    expect(o.mappedFields).toBeGreaterThan(0)
  })
  it('lists real references and marks the ones that point outside the data', () => {
    expect(o.links).toHaveLength(5)
    const unresolved = o.links.filter((l) => !l.resolved)
    expect(unresolved.map((l) => l.to)).toEqual(['Payroll run R9'])
    expect(o.links.filter((l) => l.resolved)).toHaveLength(4)
  })
  it('carries integrity and issue totals as returned', () => {
    expect(o.integrity).toEqual({ passed: 5, warning: 1, failed: 0, not_evaluated: 2 })
    expect(o.issues).toEqual({ error: 0, warning: 1, info: 0 })
  })
  it('does not invent anything for an empty or integrity-less result', () => {
    const e = overviewOf({ provider: 'prismhr', files: [], suggestions: [], results: [] })
    expect(e).toMatchObject({ records: 0, mappedFields: 0, unmappedValues: 0, withheld: 0, masked: 0, links: [], integrity: null })
  })
})

describe('relationship labels', () => {
  const emp = record({ source_id: 'E1001', first_name: 'Jordan', last_name: 'Rivera', extras: {} })
  const dep1 = record({ record_type: 'direct_deposit', source_id: '111000025-6789-C-F-', employee_id: 'E1001', account_type_raw: 'C', bank_name: 'First Bank', account_number_masked: '****6789', extras: {} })
  const dep2 = record({ record_type: 'direct_deposit', source_id: '122000247-5544-S-P-', employee_id: 'E1001', account_type: 'savings', extras: {} })
  const ded = record({ record_type: 'deduction', source_id: '401K|2023-01-01', employee_id: 'E1001', name: 'Retirement', extras: {} })
  const ded2 = record({ record_type: 'deduction', source_id: 'X|2024-01-01', employee_id: 'E1001', extras: {} })
  const conv: ConvertResponse = { provider: 'prismhr', files: [], suggestions: [], results: [unit({ records: [emp, dep1, dep2, ded, ded2] })] }
  const links = overviewOf(conv).links

  it('uses a record type plus a name or code, never an internal id', () => {
    expect(links.map((l) => l.from)).toEqual(['Direct deposit #1 (C, First Bank)', 'Direct deposit #2 (savings)', 'Deduction Retirement', 'Deduction #2'])
    expect(links.every((l) => l.to === 'Employee Jordan Rivera' && l.resolved)).toBe(true)
  })
  it('shows no routing or account digits anywhere in a label', () => {
    for (const l of links) expect(`${l.from} ${l.to}`).not.toMatch(/\d{4,}|6789|5544|\*\*\*\*/)
  })
  it('keeps the exact relationship for the detailed view, withholding deposit ids', () => {
    const [d1, , r] = links
    expect(d1).toMatchObject({ fromId: null, toId: 'E1001', via: 'employee' })
    expect(r).toMatchObject({ fromId: '401K|2023-01-01', toId: 'E1001' })
  })
})
