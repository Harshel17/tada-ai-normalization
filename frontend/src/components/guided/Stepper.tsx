import type { ReactNode } from 'react'

export type StepState = 'done' | 'current' | 'todo'

export interface StepDef {
  n: number
  title: string
  hint: string
  state: StepState
}

/** The three-step progress bar at the top of the guided flow. */
export function Stepper({ steps }: { steps: StepDef[] }) {
  return (
    <ol className={`mb-6 grid gap-3 ${steps.length === 5 ? 'md:grid-cols-5' : 'md:grid-cols-3'}`} aria-label="Progress" data-testid="stepper">
      {steps.map((s) => (
        <li key={s.n} aria-current={s.state === 'current' ? 'step' : undefined}
          className={`flex items-start gap-3 rounded-xl border p-3 ${s.state === 'current' ? 'border-brand-500 bg-brand-50' : s.state === 'done' ? 'border-emerald-200 bg-emerald-50' : 'border-slate-200 bg-white'}`}>
          <span aria-hidden className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${s.state === 'done' ? 'bg-emerald-600 text-white' : s.state === 'current' ? 'bg-brand-600 text-white' : 'bg-slate-200 text-slate-600'}`}>
            {s.state === 'done' ? '✓' : s.n}
          </span>
          <div>
            <p className="text-sm font-semibold text-slate-900">
              <span className="sr-only">Step {s.n}: </span>{s.title}
              {s.state === 'done' && <span className="sr-only"> (done)</span>}
            </p>
            <p className="text-xs text-slate-500">{s.hint}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}

export function StepSection({ n, title, children, locked, lockedText }: { n: number; title: string; children: ReactNode; locked?: boolean; lockedText?: string }) {
  return (
    <section aria-labelledby={`step-${n}`} className={`card p-5 ${locked ? 'opacity-70' : ''}`} data-testid={`step-${n}`}>
      <h2 id={`step-${n}`} className="mb-3 flex items-center gap-2 text-lg font-semibold text-slate-900">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-xs text-white" aria-hidden>{n}</span>
        {title}
      </h2>
      {locked ? <p className="text-sm text-slate-500">{lockedText}</p> : children}
    </section>
  )
}
