import type { ProviderId } from '../../api/types'
import { PROVIDERS } from '../../api/types'
import { EXAMPLE_INFO, PROVIDER_INFO } from '../../lib/examples'
import type { Scenario } from '../../lib/scenarios'
import { ENTITY_PLAIN } from '../../lib/examples'
import { Badge } from '../ui'

export function ProviderPicker({ value, onChange }: { value: ProviderId | null; onChange: (p: ProviderId) => void }) {
  return (
    <div role="radiogroup" aria-label="Payroll provider" className="grid gap-3 sm:grid-cols-3">
      {PROVIDERS.map((p) => {
        const on = value === p.id
        return (
          <button key={p.id} role="radio" aria-checked={on} onClick={() => onChange(p.id)} data-testid={`pick-${p.id}`} title={PROVIDER_INFO[p.id].blurb}
            className={`group flex items-center gap-3 rounded-xl border px-4 py-4 text-left text-base font-semibold transition-colors ${on ? 'border-brand-600 bg-brand-50 text-brand-800 shadow-card ring-1 ring-brand-600' : 'border-slate-200 bg-white text-slate-800 hover:border-slate-300 hover:bg-slate-50'}`}>
            <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-sm font-bold ${on ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-500 group-hover:bg-slate-200'}`} aria-hidden>{PROVIDER_INFO[p.id].name.charAt(0)}</span>
            <span className="min-w-0 flex-1">{PROVIDER_INFO[p.id].name}</span>
            {on && <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-brand-600" aria-hidden><path d="m5 12 4 4 10-10" /></svg>}
          </button>
        )
      })}
    </div>
  )
}

export function ExamplePicker({ examples, selectedId, loading, onLoad }: { examples: Scenario[]; selectedId: string | null; loading: string | null; onLoad: (s: Scenario) => void }) {
  return (
    <div>
      <p className="mb-3 max-w-3xl text-sm text-slate-600">
        These are made-up (synthetic) files shaped exactly like what this provider sends. Choose what kind of information to look at.
      </p>
      <ul className="grid gap-3 md:grid-cols-2" aria-label="Example data">
        {examples.map((sc) => {
          const info = EXAMPLE_INFO[sc.id]
          const plain = ENTITY_PLAIN[sc.entity]
          const on = selectedId === sc.id
          return (
            <li key={sc.id} className={`flex flex-col justify-between rounded-xl border p-4 ${on ? 'border-brand-600 bg-brand-50' : 'border-slate-200 bg-white'}`} data-testid={`example-${sc.id}`}>
              <div>
                <p className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  {info?.title ?? sc.title}
                  <Badge>{plain.title}</Badge>
                </p>
                <p className="mt-1 text-sm text-slate-600">{info?.about}</p>
                <p className="mt-1 text-xs text-slate-500">{sc.fixture.name}</p>
              </div>
              <button className={`${on ? 'btn-secondary' : 'btn-primary'} mt-3 self-start`} disabled={loading !== null} onClick={() => onLoad(sc)} aria-label={`Load example: ${info?.title ?? sc.title}`}>
                {loading === sc.id ? 'Loading…' : on ? 'Loaded ✓ (load again)' : 'Load this example'}
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
