import type { EntityId, ProviderId, SourceContext } from '../api/types'

export type ContextField = keyof SourceContext

export const CONTEXT_FIELDS: { key: ContextField; label: string; hint: string }[] = [
  { key: 'tenant_id', label: 'Tenant id', hint: 'Your own label for this provider account / environment' },
  { key: 'company_id', label: 'Company id', hint: 'PrismHR clientId · isolved legalId · UKG company id' },
  { key: 'parent_company_id', label: 'Parent company id', hint: 'isolved clientId (optional)' },
  { key: 'employee_id', label: 'Employee id', hint: 'For responses that do not repeat the employee id' },
  { key: 'payroll_id', label: 'Payroll id', hint: 'UKG: for a single pay statement' },
]

interface Need {
  required: ContextField[]
  /** Needed only for some response shapes of this entity (documented in the adapters). */
  conditional: ContextField[]
}

const NONE: Need = { required: ['tenant_id'], conditional: [] }
const needs = (required: ContextField[], conditional: ContextField[] = []): Need => ({
  required: ['tenant_id', ...required],
  conditional,
})

/** Context each adapter requires (see the module docstrings of src/normalization/adapters/). */
const TABLE: Record<ProviderId, Record<EntityId, Need>> = {
  prismhr: {
    company: NONE,
    employee: needs(['company_id']),
    direct_deposit: needs(['company_id']),
    deduction: needs(['company_id'], ['employee_id']), // getDeductions does not echo the employee
    payroll: needs(['company_id']),
  },
  isolved: {
    company: NONE,
    employee: needs(['company_id']),
    direct_deposit: needs(['company_id'], ['employee_id']), // a bare deposit list
    deduction: needs(['company_id']),
    payroll: needs(['company_id']),
  },
  'ukg-ready': {
    company: needs(['company_id']),
    employee: needs(['company_id']),
    direct_deposit: needs(['company_id', 'employee_id']),
    deduction: needs(['company_id', 'employee_id']),
    payroll: needs(['company_id'], ['payroll_id']),
  },
}

export function contextNeeds(provider: ProviderId, entity: EntityId): Need {
  return TABLE[provider][entity]
}

export function missingContext(provider: ProviderId, entity: EntityId, ctx: SourceContext): ContextField[] {
  return contextNeeds(provider, entity).required.filter((k) => !(ctx[k] ?? '').trim())
}
