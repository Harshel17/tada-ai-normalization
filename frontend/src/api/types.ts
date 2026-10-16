/** Types mirroring the FastAPI schemas (see GET /openapi.json). Not invented: names match the backend. */

export const PROVIDERS = [
  { id: 'prismhr', label: 'PrismHR', fixtureDir: 'prismhr' },
  { id: 'isolved', label: 'isolved', fixtureDir: 'isolved' },
  { id: 'ukg-ready', label: 'UKG Ready', fixtureDir: 'ukg_ready' },
] as const

export const ENTITIES = [
  { id: 'company', label: 'Company' },
  { id: 'employee', label: 'Employee' },
  { id: 'direct_deposit', label: 'Direct deposit' },
  { id: 'deduction', label: 'Deduction setup' },
  { id: 'payroll', label: 'Payroll' },
] as const

export type ProviderId = (typeof PROVIDERS)[number]['id']
export type EntityId = (typeof ENTITIES)[number]['id']

export const providerLabel = (id: string): string => PROVIDERS.find((p) => p.id === id)?.label ?? id
export const entityLabel = (id: string): string => ENTITIES.find((e) => e.id === id)?.label ?? id

export type Severity = 'error' | 'warning' | 'info'

export interface Issue {
  severity: Severity
  code: string
  message: string
  path: string | null
  source_id: string | null
}

export interface Provenance {
  source_endpoint: string | null
  source_path: string | null
  redacted_paths: string[]
  derived_fields: string[]
}

/** Any canonical record. Concrete fields differ per record_type, so they are read generically. */
export interface CanonicalRecord {
  record_type: string
  provider: ProviderId
  tenant_id: string
  company_id: string | null
  source_id: string
  extras: Record<string, unknown>
  provenance: Provenance
  [field: string]: unknown
}

export interface NormalizeResponse {
  provider: string
  entity_type: string
  ok: boolean
  counts: Record<string, number>
  issues: Issue[]
  records: CanonicalRecord[]
}

export interface StoredCounts {
  inserted: number
  updated: number
  unchanged: number
}

export interface IngestResponse {
  provider: string
  entity_type: string
  ok: boolean
  counts: Record<string, number>
  stored: Record<string, StoredCounts>
  issues: Issue[]
}

export interface HealthResponse {
  status: string
  database: string
}

export interface ScopeCounts {
  provider: string
  tenant_id: string
  company_id: string
  companies: number
  employees: number
  direct_deposits: number
  deductions: number
  payroll_runs: number
  paychecks: number
}

export interface StatsResponse {
  providers: string[]
  entity_types: string[]
  scopes: ScopeCounts[]
}

/** The `context` object of a normalize / ingest request (SourceContext). */
export interface SourceContext {
  tenant_id: string
  company_id?: string
  parent_company_id?: string
  employee_id?: string
  payroll_id?: string
}

export interface Scope {
  provider: string
  tenant: string
  company: string
}

// --- detection and preview conversion (POST /detect/{provider}, POST /convert/{provider}) ----------
export type DetectStatus = 'ok' | 'ambiguous' | 'unsupported' | 'empty'

/** One kind of data found inside a file. */
export interface DetectedUnit {
  entity: EntityId
  shape: string
  label: string
  keys: string[] | null
  counts: Record<string, number>
  derived_counts: Record<string, number>
  evidence: string[]
  requires: string[]
  optional: string[]
  nested_in: string | null
  convention: boolean
  strength: 'strong' | 'weak'
  notes: string[]
}

export interface Detection {
  status: DetectStatus
  units: DetectedUnit[]
  candidates: { entity: EntityId; reason: string }[]
  warnings: string[]
  message: string | null
  provider_hint: string | null
}

export interface FileDetection {
  name: string
  detection: Detection
}

export interface IdSuggestion {
  field: string
  value: string
  source_file: string
  why: string
}

export interface DetectResponse {
  provider: string
  files: FileDetection[]
  suggestions: IdSuggestion[]
}

/** One step of the provenance trace: which source field(s) produced one canonical field, and how. */
export interface TraceStep {
  dest: string
  op: 'copy' | 'decimal' | 'integer' | 'boolean' | 'date' | 'datetime' | 'enum' | 'redact' | 'combine' | 'derive' | 'route' | 'assigned' | 'context'
  value: unknown
  sources: { path: string; raw: unknown }[]
  table?: string
  note?: string
}

export interface UnitResult {
  file: string
  entity: EntityId
  label: string
  status: 'converted' | 'needs_context' | 'failed'
  missing: string[]
  counts: Record<string, number>
  issues: Issue[]
  records: CanonicalRecord[]
  traces: TraceStep[][]
}

export type IntegrityStatus = 'passed' | 'warning' | 'failed' | 'not_evaluated'

export interface IntegrityCheck {
  id: string
  group: string
  title: string
  status: IntegrityStatus
  reason: string
  evaluated: number
  flagged: number
  examples: { record: string; detail: string }[]
}

/** Deterministic checks over all converted records (opt-in: POST /convert with `"integrity": true`). */
export interface IntegrityReport {
  summary: Record<IntegrityStatus, number>
  checks: IntegrityCheck[]
}

export interface ConvertResponse {
  provider: string
  files: FileDetection[]
  results: UnitResult[]
  suggestions: IdSuggestion[]
  integrity?: IntegrityReport
}

// --- migration preview (POST /migrate/preview/ukg-ready): a DRY RUN, nothing is sent anywhere ------
export type MigrationReadiness = 'READY' | 'NEEDS_REVIEW' | 'BLOCKED'
export type MigrationFieldStatus = 'mapped' | 'operator_supplied' | 'needs_review' | 'omitted' | 'missing_required'
export type NotMigratedReason = 'sensitive' | 'separate_endpoint' | 'unsupported' | 'not_confirmed' | 'uncertain'

export interface MigrationOperator {
  username?: string
  use_source_employee_number?: boolean
  use_source_id_as_external_id?: boolean
  address_country?: string
}

export interface MigrationRequestBody {
  employee: CanonicalRecord
  destination: { company_id: string; ein_id?: number }
  operator: MigrationOperator
}

export interface MigrationPreview {
  dry_run: true
  notice: string
  destination_provider: string
  destination_company_id: string
  source: Record<string, string | null>
  contract: { provider: string; method: string; endpoint: string; schema_file: string; schema_anchor: string; caveat: string }
  readiness: MigrationReadiness
  readiness_meaning: string
  request_body: Record<string, unknown>
  fields: { destination: string; source: string | null; status: MigrationFieldStatus; value: unknown; note: string }[]
  requirements: { field: string; satisfied: boolean; detail: string }[]
  issues: { severity: 'blocking' | 'review' | 'info'; code: string; field: string | null; message: string }[]
  not_migrated: { source: string; reason: NotMigratedReason; note: string }[]
}
