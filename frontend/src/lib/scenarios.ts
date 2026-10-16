import { enc } from '../api/client'
import type { EntityId, ProviderId, Scope, SourceContext } from '../api/types'
import type { FixtureRef } from '../api/client'

/**
 * End-to-end scenarios for the Test Runs page. Each one names a synthetic fixture, the context to
 * send, and what the REAL responses must contain. The numbers are expectations that are checked
 * against live API output; the page never marks a step as passed without making the call.
 */
export interface Scenario {
  id: string
  title: string
  provider: ProviderId
  entity: EntityId
  fixture: FixtureRef
  context: Omit<SourceContext, 'tenant_id'>
  /** Expected `counts` of the normalize response; also the number of rows an ingest must insert. */
  expectedCounts: Record<string, number>
  /** A stored read that must return data after ingest, with the expected number of items. */
  readback: { label: string; path: (s: Scope) => string; expectItems: number }
}

const prism = { dir: 'prismhr' }
const isolved = { dir: 'isolved' }
const ukg = { dir: 'ukg_ready' }

const emp = (id: string) => (s: Scope) => `/tenants/${enc(s.tenant)}/${enc(s.provider)}/companies/${enc(s.company)}/employees/${enc(id)}`
const co = (s: Scope) => `/tenants/${enc(s.tenant)}/${enc(s.provider)}/companies/${enc(s.company)}`

export const SCENARIOS: Scenario[] = [
  {
    id: 'prismhr-company', title: 'PrismHR · client master → company', provider: 'prismhr', entity: 'company',
    fixture: { ...prism, name: 'client_master.json' }, context: { company_id: 'ACME' },
    expectedCounts: { company: 1 },
    readback: { label: 'GET company', path: (s) => co(s), expectItems: 1 },
  },
  {
    id: 'prismhr-employee', title: 'PrismHR · getEmployee → employees', provider: 'prismhr', entity: 'employee',
    fixture: { ...prism, name: 'employee_get_employee.json' }, context: { company_id: 'ACME' },
    expectedCounts: { employee: 2 },
    readback: { label: 'GET employees', path: (s) => `${co(s)}/employees`, expectItems: 2 },
  },
  {
    id: 'prismhr-direct-deposit', title: 'PrismHR · ACH vouchers → direct deposits', provider: 'prismhr', entity: 'direct_deposit',
    fixture: { ...prism, name: 'employee_get_employee.json' }, context: { company_id: 'ACME' },
    expectedCounts: { direct_deposit: 3 },
    readback: { label: 'GET E1001 direct deposits', path: (s) => `${emp('E1001')(s)}/direct-deposits`, expectItems: 3 },
  },
  {
    id: 'prismhr-deduction', title: 'PrismHR · scheduled deductions', provider: 'prismhr', entity: 'deduction',
    fixture: { ...prism, name: 'employee_get_employee.json' }, context: { company_id: 'ACME' },
    expectedCounts: { deduction: 2 },
    readback: { label: 'GET E1001 deductions', path: (s) => `${emp('E1001')(s)}/deductions`, expectItems: 2 },
  },
  {
    id: 'prismhr-payroll', title: 'PrismHR · payroll vouchers → run + paychecks', provider: 'prismhr', entity: 'payroll',
    fixture: { ...prism, name: 'payroll_vouchers.json' }, context: { company_id: 'ACME' },
    expectedCounts: { payroll_run: 1, paycheck: 2 },
    readback: { label: 'GET run B-2025-02 paychecks', path: (s) => `${co(s)}/payroll-runs/B-2025-02/paychecks`, expectItems: 2 },
  },
  {
    id: 'isolved-company', title: 'isolved · legal company', provider: 'isolved', entity: 'company',
    fixture: { ...isolved, name: 'legals.json' }, context: {},
    expectedCounts: { company: 1 },
    readback: { label: 'GET company 4501', path: (s) => co({ ...s, company: '4501' }), expectItems: 1 },
  },
  {
    id: 'isolved-employee', title: 'isolved · employees', provider: 'isolved', entity: 'employee',
    fixture: { ...isolved, name: 'employees.json' }, context: { company_id: '4501' },
    expectedCounts: { employee: 2 },
    readback: { label: 'GET employees', path: (s) => `${co({ ...s, company: '4501' })}/employees`, expectItems: 2 },
  },
  {
    id: 'isolved-direct-deposit', title: 'isolved · direct deposits (bare list)', provider: 'isolved', entity: 'direct_deposit',
    fixture: { ...isolved, name: 'direct_deposits.json' }, context: { company_id: '4501', employee_id: '90001' },
    expectedCounts: { direct_deposit: 3 },
    readback: { label: 'GET 90001 direct deposits', path: (s) => `${emp('90001')({ ...s, company: '4501' })}/direct-deposits`, expectItems: 3 },
  },
  {
    id: 'isolved-deduction', title: 'isolved · employee deductions', provider: 'isolved', entity: 'deduction',
    fixture: { ...isolved, name: 'deductions.json' }, context: { company_id: '4501' },
    expectedCounts: { deduction: 2 },
    readback: { label: 'GET 90001 deductions', path: (s) => `${emp('90001')({ ...s, company: '4501' })}/deductions`, expectItems: 2 },
  },
  {
    id: 'isolved-payroll', title: 'isolved · payroll run + check', provider: 'isolved', entity: 'payroll',
    fixture: { ...isolved, name: 'payroll.json' }, context: { company_id: '4501' },
    expectedCounts: { payroll_run: 1, paycheck: 1 },
    readback: { label: 'GET run 600123 paychecks', path: (s) => `${co({ ...s, company: '4501' })}/payroll-runs/600123/paychecks`, expectItems: 1 },
  },
  {
    id: 'ukg-company', title: 'UKG Ready · EINs → company', provider: 'ukg-ready', entity: 'company',
    fixture: { ...ukg, name: 'eins.json' }, context: { company_id: 'CID1' },
    expectedCounts: { company: 1 },
    readback: { label: 'GET company', path: (s) => co(s), expectItems: 1 },
  },
  {
    id: 'ukg-employee', title: 'UKG Ready · employee bundle', provider: 'ukg-ready', entity: 'employee',
    fixture: { ...ukg, name: 'employee_bundle.json' }, context: { company_id: 'CID1' },
    expectedCounts: { employee: 1 },
    readback: { label: 'GET employees', path: (s) => `${co(s)}/employees`, expectItems: 1 },
  },
  {
    id: 'ukg-direct-deposit', title: 'UKG Ready · direct deposits', provider: 'ukg-ready', entity: 'direct_deposit',
    fixture: { ...ukg, name: 'direct_deposits.json' }, context: { company_id: 'CID1', employee_id: '7770001' },
    expectedCounts: { direct_deposit: 4 },
    readback: { label: 'GET 7770001 direct deposits', path: (s) => `${emp('7770001')(s)}/direct-deposits`, expectItems: 4 },
  },
  {
    id: 'ukg-deduction', title: 'UKG Ready · deductions', provider: 'ukg-ready', entity: 'deduction',
    fixture: { ...ukg, name: 'deductions.json' }, context: { company_id: 'CID1', employee_id: '7770001' },
    expectedCounts: { deduction: 2 },
    readback: { label: 'GET 7770001 deductions', path: (s) => `${emp('7770001')(s)}/deductions`, expectItems: 2 },
  },
  {
    id: 'ukg-payroll', title: 'UKG Ready · payroll + pay statements', provider: 'ukg-ready', entity: 'payroll',
    fixture: { ...ukg, name: 'payroll.json' }, context: { company_id: 'CID1' },
    expectedCounts: { payroll_run: 1, paycheck: 2 },
    readback: { label: 'GET run 880001 paychecks', path: (s) => `${co(s)}/payroll-runs/880001/paychecks`, expectItems: 2 },
  },
]

/** The company id a scenario's stored data lives under. */
export function scenarioCompany(sc: Scenario): string {
  if (sc.context.company_id) return sc.context.company_id
  return sc.provider === 'isolved' ? '4501' : 'ACME'
}

/** Another provider, used to prove a read under the wrong provider finds nothing. */
export function otherProvider(p: ProviderId): ProviderId {
  return p === 'prismhr' ? 'isolved' : 'prismhr'
}
