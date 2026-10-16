import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import {
  fetchCompany, fetchDeductions, fetchDeposits, fetchEmployee, fetchEmployeePaychecks, fetchEmployees,
  fetchPaycheck, fetchRunPaychecks, fetchRuns, fetchStats,
} from '../api/client'
import type { CanonicalRecord, Scope } from '../api/types'
import { providerLabel } from '../api/types'
import { FieldTable, JsonBlock } from '../components/Records'
import { Badge, EmptyState, ErrorBanner, Field, PageHeader, Spinner, Tabs } from '../components/ui'

type View = 'company' | 'employees' | 'payroll'
const PAGE = 25

const str = (v: unknown): string => (v === null || v === undefined || v === '' ? '—' : String(v))

function Table({ head, rows, onRow, testId, selected }: { head: string[]; rows: { key: string; cells: React.ReactNode[] }[]; onRow?: (key: string) => void; testId: string; selected?: string | null }) {
  return (
    <div className="card overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-sm" data-testid={testId}>
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>{head.map((h) => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r) => (
            <tr key={r.key} onClick={() => onRow?.(r.key)} onKeyDown={(e) => { if (e.key === 'Enter') onRow?.(r.key) }} tabIndex={onRow ? 0 : undefined}
              className={`${onRow ? 'cursor-pointer hover:bg-slate-50' : ''} ${selected === r.key ? 'bg-brand-50' : ''}`}>
              {r.cells.map((c, i) => <td key={i} className="px-3 py-2 align-top">{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Pager({ offset, count, onChange }: { offset: number; count: number; onChange: (o: number) => void }) {
  return (
    <div className="mt-2 flex items-center justify-between text-sm text-slate-500">
      <span>{count === 0 ? 'No rows' : `Rows ${offset + 1}–${offset + count}`}</span>
      <span className="flex gap-2">
        <button className="btn-secondary !py-1" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE))}>Previous</button>
        <button className="btn-secondary !py-1" disabled={count < PAGE} onClick={() => onChange(offset + PAGE)}>Next</button>
      </span>
    </div>
  )
}

function Section<T>({ q, empty, children }: { q: { isPending: boolean; isError: boolean; error: unknown; data?: T }; empty: string; children: (d: T) => React.ReactNode }) {
  if (q.isPending) return <Spinner />
  if (q.isError) return <ErrorBanner title="Could not load">{(q.error as Error).message}</ErrorBanner>
  if (Array.isArray(q.data) && q.data.length === 0) return <EmptyState title={empty} />
  return <>{children(q.data as T)}</>
}

function EmployeeDetail({ scope, id }: { scope: Scope; id: string }) {
  const [tab, setTab] = useState<'profile' | 'deposits' | 'deductions' | 'paychecks'>('profile')
  const emp = useQuery({ queryKey: ['emp', scope, id], queryFn: () => fetchEmployee(scope, id) })
  const deps = useQuery({ queryKey: ['deps', scope, id], queryFn: () => fetchDeposits(scope, id) })
  const deds = useQuery({ queryKey: ['deds', scope, id], queryFn: () => fetchDeductions(scope, id) })
  const checks = useQuery({ queryKey: ['empchecks', scope, id], queryFn: () => fetchEmployeePaychecks(scope, id) })
  return (
    <div className="card mt-4 space-y-3 p-4" data-testid="employee-detail">
      <h3 className="text-base font-semibold">Employee <span className="font-mono">{id}</span></h3>
      <Tabs label="Employee data" value={tab} onChange={setTab} tabs={[
        { id: 'profile', label: 'Profile' },
        { id: 'deposits', label: 'Direct deposits', badge: deps.data?.length },
        { id: 'deductions', label: 'Deductions', badge: deds.data?.length },
        { id: 'paychecks', label: 'Paychecks', badge: checks.data?.length },
      ]} />
      {tab === 'profile' && <Section q={emp} empty="Not found">{(e) => <FieldTable record={e} />}</Section>}
      {tab === 'deposits' && (
        <Section q={deps} empty="No direct deposits stored for this employee">
          {(rows) => <Table testId="deposits-table" head={['#', 'Account', 'Type', 'Allocation', 'Amount / %', 'Status']}
            rows={rows.map((d) => ({ key: d.source_id, cells: [str(d.sequence ?? d.source_position), <span className="font-mono">{str(d.account_number_masked)}</span>, str(d.account_type ?? d.account_type_raw), str(d.allocation_type ?? d.allocation_raw), str(d.amount ?? d.percentage), <Badge tone={d.active === false ? 'slate' : 'green'}>{str(d.status ?? d.status_raw ?? (d.active ? 'active' : 'inactive'))}</Badge>] }))} />}
        </Section>
      )}
      {tab === 'deductions' && (
        <Section q={deds} empty="No deduction setup stored for this employee">
          {(rows) => <Table testId="deductions-table" head={['Code', 'Name', 'Kind', 'Amount', 'Percent', 'Start', 'Stop']}
            rows={rows.map((d) => ({ key: d.source_id, cells: [str(d.deduction_code), str(d.name), str(d.setup_kind), str(d.amount), str(d.percentage), str(d.start_date), str(d.stop_date)] }))} />}
        </Section>
      )}
      {tab === 'paychecks' && (
        <Section q={checks} empty="No paychecks stored for this employee">
          {(rows) => <Table testId="emp-paychecks-table" head={['Paycheck', 'Pay date', 'Period', 'Gross', 'Net']}
            rows={rows.map((c) => ({ key: c.source_id, cells: [<span className="font-mono">{c.source_id}</span>, str(c.pay_date), `${str(c.period_start)} → ${str(c.period_end)}`, str(c.gross_pay), str(c.net_pay)] }))} />}
        </Section>
      )}
    </div>
  )
}

function PaycheckDetail({ scope, id }: { scope: Scope; id: string }) {
  const q = useQuery({ queryKey: ['paycheck', scope, id], queryFn: () => fetchPaycheck(scope, id) })
  return (
    <div className="card mt-4 space-y-4 p-4" data-testid="paycheck-detail">
      <h3 className="text-base font-semibold">Paycheck <span className="font-mono">{id}</span></h3>
      <Section q={q} empty="Not found">
        {(c) => (
          <>
            <FieldTable record={{ ...c, earnings: [], taxes: [], deductions: [], benefits: [], payments: [] }} />
            {(['earnings', 'taxes', 'deductions', 'benefits', 'payments'] as const).map((k) => {
              const lines = (c[k] as Record<string, unknown>[] | undefined) ?? []
              return (
                <div key={k}>
                  <h4 className="mb-1 text-sm font-semibold capitalize text-slate-700">{k} <span className="font-normal text-slate-500">({lines.length})</span></h4>
                  {lines.length === 0 ? <p className="text-sm text-slate-500">None.</p> : <JsonBlock value={lines.map(({ extras: _x, ...rest }) => rest)} maxHeight={220} />}
                </div>
              )
            })}
          </>
        )}
      </Section>
    </div>
  )
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

export function Explorer() {
  const stats = useQuery({ queryKey: ['stats'], queryFn: ({ signal }) => fetchStats(signal) })
  const scopes = useMemo(() => stats.data?.scopes ?? [], [stats.data])
  // What the user picked. The EFFECTIVE selection below falls back to the first option that really
  // exists in the database, computed during render (no effect, so no flash of empty dropdowns).
  const [pickedProvider, setProvider] = useState('')
  const [pickedTenant, setTenant] = useState('')
  const [pickedCompany, setCompany] = useState('')
  const [view, setView] = useState<View>('employees')
  const [offset, setOffset] = useState(0)
  const [employeeId, setEmployeeId] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [paycheckId, setPaycheckId] = useState<string | null>(null)

  const providers = useMemo(() => [...new Set(scopes.map((s) => s.provider))], [scopes])
  const provider = providers.includes(pickedProvider) ? pickedProvider : (providers[0] ?? '')
  const tenants = useMemo(() => [...new Set(scopes.filter((s) => s.provider === provider).map((s) => s.tenant_id))], [scopes, provider])
  const tenant = tenants.includes(pickedTenant) ? pickedTenant : (tenants[0] ?? '')
  const companies = useMemo(
    () => [...new Set(scopes.filter((s) => s.provider === provider && s.tenant_id === tenant).map((s) => s.company_id))],
    [scopes, provider, tenant],
  )
  const company = companies.includes(pickedCompany) ? pickedCompany : (companies[0] ?? '')

  const scope: Scope | null = provider && tenant && company ? { provider, tenant, company } : null
  const reset = () => { setOffset(0); setEmployeeId(null); setRunId(null); setPaycheckId(null) }

  const companyQ = useQuery({ queryKey: ['company', scope], queryFn: () => fetchCompany(scope!), enabled: !!scope && view === 'company' })
  const employeesQ = useQuery({ queryKey: ['employees', scope, offset], queryFn: () => fetchEmployees(scope!, PAGE, offset), enabled: !!scope && view === 'employees' })
  const runsQ = useQuery({ queryKey: ['runs', scope, offset], queryFn: () => fetchRuns(scope!, PAGE, offset), enabled: !!scope && view === 'payroll' })
  const runChecksQ = useQuery({ queryKey: ['runchecks', scope, runId], queryFn: () => fetchRunPaychecks(scope!, runId!), enabled: !!scope && !!runId })

  const counts = scopes.find((s) => s.provider === provider && s.tenant_id === tenant && s.company_id === company)

  return (
    <div>
      <PageHeader title="Data Explorer" subtitle="Browse what is stored in PostgreSQL through the read API. Account numbers are masked, SSNs are last-4 only, and raw source extras are never returned." actions={<button className="btn-secondary" onClick={() => void stats.refetch()}>Refresh</button>} />

      {stats.isPending && <Spinner label="Loading stored scopes" />}
      {stats.isError && <ErrorBanner title="Could not reach the API">{(stats.error as Error).message}</ErrorBanner>}
      {stats.data && scopes.length === 0 && (
        <EmptyState title="Nothing stored yet" action={<a className="btn-primary" href="#/normalize">Go to Normalize</a>}>Save a fixture to PostgreSQL from the Normalize page, then come back to browse it here.</EmptyState>
      )}

      {scopes.length > 0 && (
        <>
          <div className="card mb-4 grid gap-4 p-4 sm:grid-cols-3" role="group" aria-label="Filters">
            <Field label="Provider" htmlFor="x-provider">
              <select id="x-provider" className="input" value={provider} onChange={(e) => { setProvider(e.target.value); reset() }}>
                {providers.map((p) => <option key={p} value={p}>{providerLabel(p)}</option>)}
              </select>
            </Field>
            <Field label="Tenant" htmlFor="x-tenant">
              <select id="x-tenant" className="input" value={tenant} onChange={(e) => { setTenant(e.target.value); reset() }}>
                {tenants.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="Company" htmlFor="x-company">
              <select id="x-company" className="input" value={company} onChange={(e) => { setCompany(e.target.value); reset() }}>
                {companies.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
          </div>

          {scope && (
            <>
              <Tabs label="Data type" value={view} onChange={(v) => { setView(v); reset() }} tabs={[
                { id: 'company', label: 'Company', badge: counts?.companies },
                { id: 'employees', label: 'Employees', badge: counts?.employees },
                { id: 'payroll', label: 'Payroll', badge: counts?.payroll_runs },
              ]} />
              <p className="mt-2 text-xs text-slate-500">
                Stored for this scope: {plural(counts?.direct_deposits ?? 0, 'direct deposit')} · {plural(counts?.deductions ?? 0, 'deduction')} · {plural(counts?.paychecks ?? 0, 'paycheck')}. Direct deposits and deductions are shown per employee.
              </p>

              <div className="mt-4">
                {view === 'company' && (
                  <Section q={companyQ} empty="No company record">
                    {(c: CanonicalRecord) => <div className="card p-4" data-testid="company-detail"><FieldTable record={c} /></div>}
                  </Section>
                )}
                {view === 'employees' && (
                  <>
                    <Section q={employeesQ} empty="No employees stored for this scope">
                      {(rows) => (
                        <>
                          <Table testId="employees-table" selected={employeeId} onRow={(k) => setEmployeeId(k)}
                            head={['Employee id', 'Name', 'Status', 'Hired', 'Job', 'Manager']}
                            rows={rows.map((e) => ({ key: e.source_id, cells: [<span className="font-mono">{e.source_id}</span>, [e.first_name, e.last_name].filter(Boolean).join(' ') || '—', str(e.employment_status ?? e.employment_status_raw), str(e.hire_date), str(e.job_code ?? e.job_title), str(e.manager_id)] }))} />
                          <Pager offset={offset} count={rows.length} onChange={(o) => { setOffset(o); setEmployeeId(null) }} />
                        </>
                      )}
                    </Section>
                    {employeeId && <EmployeeDetail scope={scope} id={employeeId} />}
                  </>
                )}
                {view === 'payroll' && (
                  <>
                    <Section q={runsQ} empty="No payroll runs stored for this scope">
                      {(rows) => (
                        <>
                          <Table testId="runs-table" selected={runId} onRow={(k) => { setRunId(k); setPaycheckId(null) }}
                            head={['Run', 'Name', 'Pay date', 'Period start', 'Period end', 'Status']}
                            rows={rows.map((r) => ({ key: r.source_id, cells: [<span className="font-mono">{r.source_id}</span>, str(r.name), str(r.pay_date), str(r.period_start), str(r.period_end), str(r.status_raw)] }))} />
                          <Pager offset={offset} count={rows.length} onChange={(o) => { setOffset(o); setRunId(null) }} />
                        </>
                      )}
                    </Section>
                    {runId && (
                      <div className="mt-4">
                        <h3 className="mb-2 text-base font-semibold">Paychecks in run <span className="font-mono">{runId}</span></h3>
                        <Section q={runChecksQ} empty="No paychecks reference this run">
                          {(rows) => <Table testId="paychecks-table" selected={paycheckId} onRow={(k) => setPaycheckId(k)}
                            head={['Paycheck', 'Employee', 'Pay date', 'Gross', 'Net']}
                            rows={rows.map((c) => ({ key: c.source_id, cells: [<span className="font-mono">{c.source_id}</span>, str(c.employee_id), str(c.pay_date), str(c.gross_pay), str(c.net_pay)] }))} />}
                        </Section>
                      </div>
                    )}
                    {paycheckId && <PaycheckDetail scope={scope} id={paycheckId} />}
                  </>
                )}
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
