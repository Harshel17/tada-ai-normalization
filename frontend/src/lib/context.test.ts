import { describe, expect, it } from 'vitest'
import { contextNeeds, missingContext } from './context'

describe('context requirements (mirrors the adapters)', () => {
  it('always needs a tenant', () => {
    expect(missingContext('prismhr', 'company', {} as never)).toEqual(['tenant_id'])
  })
  it('PrismHR employee needs the clientId', () => {
    expect(missingContext('prismhr', 'employee', { tenant_id: 't' })).toEqual(['company_id'])
    expect(missingContext('prismhr', 'employee', { tenant_id: 't', company_id: 'ACME' })).toEqual([])
  })
  it('isolved company needs no company id, UKG company does', () => {
    expect(missingContext('isolved', 'company', { tenant_id: 't' })).toEqual([])
    expect(missingContext('ukg-ready', 'company', { tenant_id: 't' })).toEqual(['company_id'])
  })
  it('UKG deposits and deductions need the employee id', () => {
    expect(missingContext('ukg-ready', 'direct_deposit', { tenant_id: 't', company_id: 'C' })).toEqual(['employee_id'])
    expect(missingContext('ukg-ready', 'deduction', { tenant_id: 't', company_id: 'C' })).toEqual(['employee_id'])
  })
  it('treats whitespace as missing and flags response-shape dependent fields as conditional', () => {
    expect(missingContext('prismhr', 'employee', { tenant_id: ' ', company_id: 'A' })).toEqual(['tenant_id'])
    expect(contextNeeds('prismhr', 'deduction').conditional).toEqual(['employee_id'])
    expect(contextNeeds('ukg-ready', 'payroll').conditional).toEqual(['payroll_id'])
  })
})
