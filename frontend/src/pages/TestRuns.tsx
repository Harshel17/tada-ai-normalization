import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { providerLabel } from '../api/types'
import { Badge, EmptyState, Field, InfoBanner, PageHeader, Spinner } from '../components/ui'
import type { StepResult, StepState } from '../lib/runner'
import { initialSteps, runScenario } from '../lib/runner'
import { SCENARIOS } from '../lib/scenarios'

const newTenant = () => `console-run-${Date.now().toString(36)}`

const STATE_BADGE: Record<StepState, { tone: 'slate' | 'green' | 'red' | 'amber' | 'blue'; text: string }> = {
  pending: { tone: 'slate', text: 'pending' },
  running: { tone: 'blue', text: 'running' },
  pass: { tone: 'green', text: 'pass' },
  fail: { tone: 'red', text: 'fail' },
  error: { tone: 'red', text: 'error' },
  skipped: { tone: 'amber', text: 'skipped' },
}

type Results = Record<string, StepResult[]>

export function TestRuns() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set(SCENARIOS.map((s) => s.id)))
  const [tenant, setTenant] = useState(newTenant)
  const [results, setResults] = useState<Results>({})
  const [running, setRunning] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

  const run = async () => {
    setRunning(true)
    setResults({})
    const chosen = SCENARIOS.filter((s) => selected.has(s.id))
    setResults(Object.fromEntries(chosen.map((s) => [s.id, initialSteps()])))
    for (const sc of chosen) {
      const final = await runScenario(sc, tenant.trim(), { onStep: (steps) => setResults((r) => ({ ...r, [sc.id]: steps })) })
      setResults((r) => ({ ...r, [sc.id]: final }))
    }
    setRunning(false)
    void queryClient.invalidateQueries({ queryKey: ['stats'] })
  }

  const done = Object.entries(results)
  const verdict = (steps: StepResult[]) => (steps.some((s) => s.state === 'running') ? 'running' : steps.every((s) => s.state === 'pending') ? 'pending' : steps.every((s) => s.state === 'pass') ? 'pass' : 'fail')
  const finished = done.filter(([, s]) => ['pass', 'fail'].includes(verdict(s)))
  const passed = finished.filter(([, s]) => verdict(s) === 'pass').length

  return (
    <div>
      <PageHeader
        title="Test Runs"
        subtitle="Runs the synthetic end-to-end scenarios through the public API: normalize, save, save again, read back, and check tenant and provider isolation. Each verdict is computed from the real responses."
      />
      <InfoBanner tone="amber">Runs write synthetic records to your local PostgreSQL database under the tenant below. Use a fresh tenant for a clean first-save check.</InfoBanner>

      <div className="card mt-4 grid items-end gap-4 p-4 md:grid-cols-[1fr_auto_auto]">
        <Field label="Tenant for this run" htmlFor="run-tenant" hint="Records are saved under this tenant id">
          <div className="flex gap-2">
            <input id="run-tenant" className="input font-mono" value={tenant} onChange={(e) => setTenant(e.target.value)} disabled={running} />
            <button className="btn-secondary" onClick={() => setTenant(newTenant())} disabled={running}>New</button>
          </div>
        </Field>
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={() => setSelected(new Set(SCENARIOS.map((s) => s.id)))} disabled={running}>Select all</button>
          <button className="btn-secondary" onClick={() => setSelected(new Set())} disabled={running}>None</button>
        </div>
        <button className="btn-primary" onClick={() => void run()} disabled={running || selected.size === 0 || !tenant.trim()}>
          {running ? <Spinner label="Running" /> : `Run ${selected.size} scenario${selected.size === 1 ? '' : 's'}`}
        </button>
      </div>

      {done.length > 0 && (
        <p className="mt-4 text-sm text-slate-600" data-testid="run-summary">
          {running ? 'Running…  ' : 'Finished.  '}
          <strong>{passed}</strong> of <strong>{finished.length}</strong> completed scenarios passed all steps.
        </p>
      )}

      <div className="mt-4 space-y-3">
        {SCENARIOS.map((sc) => {
          const steps = results[sc.id]
          const v = steps ? verdict(steps) : null
          return (
            <article key={sc.id} className="card" data-testid={`scenario-${sc.id}`}>
              <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                <input type="checkbox" checked={selected.has(sc.id)} onChange={() => toggle(sc.id)} disabled={running} aria-label={`Select ${sc.title}`} />
                <button className="flex flex-1 flex-wrap items-center gap-2 text-left" onClick={() => setOpen(open === sc.id ? null : sc.id)} aria-expanded={open === sc.id}>
                  <span className="text-sm font-medium text-slate-900">{sc.title}</span>
                  <Badge>{providerLabel(sc.provider)}</Badge>
                  <Badge tone="indigo">{sc.entity.replace('_', ' ')}</Badge>
                </button>
                {v && v !== 'pending' && <Badge tone={v === 'pass' ? 'green' : v === 'running' ? 'blue' : 'red'}>{v === 'pass' ? 'all steps passed' : v}</Badge>}
              </div>
              {steps && (open === sc.id || v === 'fail') && (
                <ol className="space-y-1 border-t border-slate-100 px-4 py-3">
                  {steps.map((s) => (
                    <li key={s.id} className="flex flex-wrap items-start gap-2 text-sm" data-testid={`step-${sc.id}-${s.id}`}>
                      <Badge tone={STATE_BADGE[s.state].tone}>{STATE_BADGE[s.state].text}</Badge>
                      <span className="w-56 font-medium text-slate-700">{s.label}</span>
                      <span className="min-w-0 flex-1 text-slate-600">{s.detail}</span>
                      {s.ms !== undefined && <span className="text-xs text-slate-500">{s.ms} ms</span>}
                    </li>
                  ))}
                </ol>
              )}
            </article>
          )
        })}
      </div>
      {done.length === 0 && <div className="mt-4"><EmptyState title="No run yet">Choose scenarios and press Run. Nothing is shown as passed until it has actually been executed.</EmptyState></div>}
    </div>
  )
}
