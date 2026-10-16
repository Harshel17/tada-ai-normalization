import type { EntityId, ProviderId, SourceContext } from '../api/types'
import type { Scenario } from './scenarios'

/** How each payroll provider talks, in words a newcomer can follow (taken from the provider contracts). */
export const PROVIDER_INFO: Record<ProviderId, { name: string; blurb: string; vocabulary: string }> = {
  prismhr: {
    name: 'PrismHR',
    blurb: 'A professional employer organization (PEO) platform for HR and payroll.',
    vocabulary: 'Calls an employer a “client” and a pay stub a “voucher”.',
  },
  isolved: {
    name: 'isolved',
    blurb: 'HR and payroll software for employers.',
    vocabulary: 'Calls an employer a “legal company” and a pay stub a “check”.',
  },
  'ukg-ready': {
    name: 'UKG Ready',
    blurb: 'Workforce management and payroll software.',
    vocabulary: 'Calls a person an “account” and a pay stub a “pay statement”.',
  },
}

/** What each kind of data means, in plain English. */
export const ENTITY_PLAIN: Record<EntityId, { title: string; what: string; result: string }> = {
  company: {
    title: 'Employer',
    what: 'The company that employs people: its name, tax id, address and status.',
    result: 'employer',
  },
  employee: {
    title: 'Employee records',
    what: 'People who work for the employer: who they are, their job, pay set-up, and contact details.',
    result: 'employee',
  },
  direct_deposit: {
    title: 'Bank deposit set-up',
    what: 'How an employee’s pay is split across bank accounts (fixed amounts, percentages, or “whatever is left”).',
    result: 'bank deposit instruction',
  },
  deduction: {
    title: 'Paycheck deductions',
    what: 'Amounts set up to be taken out of an employee’s pay, such as benefits, retirement or parking.',
    result: 'deduction',
  },
  payroll: {
    title: 'Payroll run and paychecks',
    what: 'A pay run (pay date and period) with each employee’s paycheck: earnings, taxes and deductions.',
    result: 'payroll record',
  },
}

/** Friendly description of each example file (keyed by scenario id), written from the fixture contents. */
export const EXAMPLE_INFO: Record<string, { title: string; about: string }> = {
  'prismhr-company': { title: 'PrismHR employer record', about: 'The employer (PrismHR’s “client”): legal name, tax id, address, status.' },
  'prismhr-employee': { title: 'PrismHR employee records', about: 'Employees with job, pay set-up, contact, tax and benefit details.' },
  'prismhr-direct-deposit': { title: 'PrismHR bank deposit set-up', about: 'The same employee file, read for how pay is split across bank accounts.' },
  'prismhr-deduction': { title: 'PrismHR scheduled deductions', about: 'The same employee file, read for one-off and temporary paycheck deductions.' },
  'prismhr-payroll': { title: 'PrismHR payroll run', about: 'Paychecks (PrismHR “vouchers”) with earnings, taxes, deductions and benefits.' },
  'isolved-company': { title: 'isolved employer record', about: 'The employer (isolved’s “legal company”) and the client it belongs to.' },
  'isolved-employee': { title: 'isolved employee records', about: 'Employees with name, address, job, pay rates, taxes and team.' },
  'isolved-direct-deposit': { title: 'isolved bank deposit set-up', about: 'A list of bank accounts that split one employee’s pay.' },
  'isolved-deduction': { title: 'isolved paycheck deductions', about: 'Deductions set up for an employee, with their start and stop dates.' },
  'isolved-payroll': { title: 'isolved payroll run', about: 'A pay run and one check (isolved’s pay stub) with line items.' },
  'ukg-company': { title: 'UKG Ready employer record', about: 'The employer’s tax entities (EINs). UKG provides no company name or address.' },
  'ukg-employee': { title: 'UKG Ready employee record', about: 'One employee assembled from UKG’s profile, demographics, pay and compensation parts.' },
  'ukg-direct-deposit': { title: 'UKG Ready bank deposit set-up', about: 'Bank accounts and rules that split one employee’s pay.' },
  'ukg-deduction': { title: 'UKG Ready paycheck deductions', about: 'Deductions configured for an employee, with employer-side limits.' },
  'ukg-payroll': { title: 'UKG Ready payroll run', about: 'A pay run and its pay statements (UKG’s pay stubs).' },
}

/** Friendly names for the ids the system needs, per provider (the provider’s own word in brackets). */
export const ID_LABELS: Record<ProviderId, Partial<Record<keyof SourceContext, string>>> = {
  prismhr: { company_id: 'Employer ID (PrismHR “client ID”)', employee_id: 'Employee ID', payroll_id: 'Payroll run ID' },
  isolved: { company_id: 'Employer ID (isolved “legal company ID”)', employee_id: 'Employee ID', payroll_id: 'Payroll run ID' },
  'ukg-ready': { company_id: 'Employer ID (UKG “company ID”)', employee_id: 'Employee ID (UKG “account ID”)', payroll_id: 'Payroll run ID' },
}

export const WORKSPACE_LABEL = 'Workspace name'
export const WORKSPACE_HELP = 'A label you choose to keep this demo’s data separate from anything else. Any name works.'

export const idLabel = (provider: ProviderId, key: keyof SourceContext): string =>
  key === 'tenant_id' ? WORKSPACE_LABEL : (ID_LABELS[provider][key] ?? key.replace(/_/g, ' '))

export const examplesFor = (scenarios: Scenario[], provider: ProviderId): Scenario[] =>
  scenarios.filter((s) => s.provider === provider)
