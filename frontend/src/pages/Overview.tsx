import { useQuery } from '@tanstack/react-query'
import { fetchHealth, fetchStats } from '../api/client'
import { ENTITIES, PROVIDERS, providerLabel } from '../api/types'
import { Badge, EmptyState, ErrorBanner, PageHeader, Spinner, Stat } from '../components/ui'

const ENTITY_NOTES: Record<string, string> = {
  company: 'Employer / legal entity',
  employee: 'Profile, employment, compensation, tax set-up',
  direct_deposit: 'Standing split of pay across accounts',
  deduction: 'Configured deductions (not paycheck lines)',
  payroll: 'Payroll runs and paychecks with line items',
}

export function Overview() {
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => fetchHealth(signal), refetchInterval: 15_000 })
  const stats = useQuery({ queryKey: ['stats'], queryFn: ({ signal }) => fetchStats(signal) })

  const totals = (stats.data?.scopes ?? []).reduce(
    (t, s) => ({
      companies: t.companies + s.companies,
      employees: t.employees + s.employees,
      deposits: t.deposits + s.direct_deposits,
      deductions: t.deductions + s.deductions,
      runs: t.runs + s.payroll_runs,
      paychecks: t.paychecks + s.paychecks,
    }),
    { companies: 0, employees: 0, deposits: 0, deductions: 0, runs: 0, paychecks: 0 },
  )

  return (
    <div>
      <PageHeader
        title="Overview"
        subtitle="Live status of the local Tada Normalize API and what is currently stored in PostgreSQL. Every number on this page comes from an API call."
        actions={<button className="btn-secondary" onClick={() => { void health.refetch(); void stats.refetch() }}>Refresh</button>}
      />

      <section aria-label="What this is" className="mb-8 rounded-xl border border-brand-100 bg-brand-50 p-5" data-testid="what-is-this">
        <h2 className="text-lg font-semibold text-slate-900">What is Tada Normalize?</h2>
        <p className="mt-1 max-w-4xl text-sm leading-relaxed text-slate-700">
          Payroll providers (PrismHR, isolved, UKG Ready) hold the same kinds of information, such as employees, pay, bank deposits and
          deductions, but each sends it in a different layout and vocabulary. Tada Normalize converts all of them into <strong>one common format</strong>, keeps
          anything it cannot safely interpret exactly as received, and can store the result in a database. Sensitive values such as SSNs and
          bank account numbers are never stored in full.
        </p>
        <a className="btn-primary mt-3" href="#/normalize">Try it with example data (about two minutes)</a>
      </section>

      <section aria-label="Connectivity" className="mb-8 grid gap-4 md:grid-cols-2">
        <div className="card p-4" data-testid="api-status">
          <p className="label">API</p>
          {health.isPending && <Spinner label="Checking API" />}
          {health.isError && (
            <div className="space-y-2">
              <Badge tone="red">unreachable</Badge>
              <p className="text-sm text-slate-600">{(health.error as Error).message}</p>
              <p className="text-xs text-slate-500">Start it with <code>uv run uvicorn normalization.api.app:app</code> and make sure the console’s proxy points at it (<code>API_URL</code>).</p>
            </div>
          )}
          {health.data && (
            <div className="flex items-center gap-2">
              <Badge tone={health.data.status === 'ok' ? 'green' : 'amber'}>{health.data.status === 'ok' ? 'API online' : health.data.status}</Badge>
              <span className="text-xs text-slate-500">GET /health</span>
            </div>
          )}
        </div>
        <div className="card p-4" data-testid="db-status">
          <p className="label">PostgreSQL</p>
          {health.isPending && <Spinner label="Checking database" />}
          {health.isError && <Badge tone="slate">unknown (API unreachable)</Badge>}
          {health.data && (
            <div className="flex items-center gap-2">
              <Badge tone={health.data.database === 'up' ? 'green' : 'red'}>{health.data.database === 'up' ? 'database up' : 'database down'}</Badge>
              {health.data.database !== 'up' && <span className="text-xs text-slate-500">Run <code>docker compose up -d</code> and <code>uv run alembic upgrade head</code>. Normalize still works; saving and browsing do not.</span>}
            </div>
          )}
        </div>
      </section>

      <section aria-label="Supported providers" className="mb-8">
        <h2 className="mb-3 text-lg font-semibold">Providers</h2>
        <div className="grid gap-4 md:grid-cols-3">
          {PROVIDERS.map((p) => {
            const backend = stats.data?.providers.includes(p.id)
            return (
              <div key={p.id} className="card p-4" data-testid={`provider-${p.id}`}>
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">{p.label}</h3>
                  {stats.data && (backend ? <Badge tone="green">supported by API</Badge> : <Badge tone="red">not reported by API</Badge>)}
                </div>
                <p className="mt-1 font-mono text-xs text-slate-500">{p.id}</p>
              </div>
            )
          })}
        </div>
      </section>

      <section aria-label="Entity types" className="mb-8">
        <h2 className="mb-3 text-lg font-semibold">Entity types</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {ENTITIES.map((e) => (
            <div key={e.id} className="card p-3" data-testid={`entity-${e.id}`}>
              <p className="text-sm font-semibold">{e.label}</p>
              <p className="mt-0.5 text-xs text-slate-500">{ENTITY_NOTES[e.id]}</p>
              <p className="mt-1 font-mono text-[11px] text-slate-500">{e.id}</p>
            </div>
          ))}
        </div>
      </section>

      <section aria-label="Stored records">
        <h2 className="mb-3 text-lg font-semibold">Stored records</h2>
        {stats.isPending && <Spinner label="Loading counts" />}
        {stats.isError && <ErrorBanner title="Could not load stored counts">{(stats.error as Error).message}</ErrorBanner>}
        {stats.data && stats.data.scopes.length === 0 && (
          <EmptyState title="The database is empty" action={<a className="btn-primary" href="#/normalize">Normalize and save a fixture</a>}>
            Nothing has been saved yet. Counts appear here after you save a record from the Normalize page.
          </EmptyState>
        )}
        {stats.data && stats.data.scopes.length > 0 && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-6" data-testid="totals">
              <Stat label="Companies" value={totals.companies} />
              <Stat label="Employees" value={totals.employees} />
              <Stat label="Direct deposits" value={totals.deposits} />
              <Stat label="Deductions" value={totals.deductions} />
              <Stat label="Payroll runs" value={totals.runs} />
              <Stat label="Paychecks" value={totals.paychecks} />
            </div>
            <div className="card overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm" data-testid="scope-table">
                <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Provider</th><th className="px-3 py-2">Tenant</th><th className="px-3 py-2">Company</th>
                    <th className="px-3 py-2 text-right">Co.</th><th className="px-3 py-2 text-right">Emp.</th><th className="px-3 py-2 text-right">Dep.</th>
                    <th className="px-3 py-2 text-right">Ded.</th><th className="px-3 py-2 text-right">Runs</th><th className="px-3 py-2 text-right">Checks</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {stats.data.scopes.map((s) => (
                    <tr key={`${s.provider}/${s.tenant_id}/${s.company_id}`}>
                      <td className="px-3 py-2">{providerLabel(s.provider)}</td>
                      <td className="px-3 py-2 font-mono text-xs">{s.tenant_id}</td>
                      <td className="px-3 py-2 font-mono text-xs">{s.company_id}</td>
                      <td className="px-3 py-2 text-right">{s.companies}</td><td className="px-3 py-2 text-right">{s.employees}</td>
                      <td className="px-3 py-2 text-right">{s.direct_deposits}</td><td className="px-3 py-2 text-right">{s.deductions}</td>
                      <td className="px-3 py-2 text-right">{s.payroll_runs}</td><td className="px-3 py-2 text-right">{s.paychecks}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
