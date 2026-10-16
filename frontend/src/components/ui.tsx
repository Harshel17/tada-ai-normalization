import type { ReactNode } from 'react'
import type { Severity } from '../api/types'

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-slate-500">
      <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden>
        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-25" />
        <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" className="opacity-90" />
      </svg>
      {label}…
    </span>
  )
}

type Tone = 'slate' | 'green' | 'amber' | 'red' | 'blue' | 'indigo'
const TONES: Record<Tone, string> = {
  slate: 'bg-slate-100 text-slate-700 ring-slate-200',
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  amber: 'bg-amber-50 text-amber-800 ring-amber-600/25',
  red: 'bg-rose-50 text-rose-700 ring-rose-600/20',
  blue: 'bg-sky-50 text-sky-700 ring-sky-600/20',
  indigo: 'bg-brand-50 text-brand-700 ring-brand-600/20',
}

export function Badge({ tone = 'slate', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  )
}

export const severityTone = (s: Severity): Tone => (s === 'error' ? 'red' : s === 'warning' ? 'amber' : 'blue')

export function ErrorBanner({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3.5 text-sm text-rose-800">
      <p className="font-semibold">{title}</p>
      {children && <div className="mt-1 text-rose-700">{children}</div>}
    </div>
  )
}

export function InfoBanner({ children, tone = 'blue' }: { children: ReactNode; tone?: 'blue' | 'amber' | 'green' }) {
  const cls = { blue: 'border-sky-200 bg-sky-50 text-sky-800', amber: 'border-amber-200 bg-amber-50 text-amber-900', green: 'border-emerald-200 bg-emerald-50 text-emerald-800' }[tone]
  return <div role="status" className={`rounded-lg border p-3.5 text-sm ${cls}`}>{children}</div>
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white/70 px-6 py-12 text-center">
      <p className="text-sm font-semibold text-slate-700">{title}</p>
      {children && <p className="mt-1 max-w-md text-sm text-slate-500">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  )
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { id: T; label: string; badge?: string | number }[]; value: T; onChange: (t: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b border-slate-200">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${value === t.id ? 'border-brand-600 text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-800'}`}
        >
          {t.label}
          {t.badge !== undefined && <span className="num ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{t.badge}</span>}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, hint, required, children, htmlFor }: { label: string; hint?: string; required?: boolean; children: ReactNode; htmlFor?: string }) {
  return (
    <div>
      <label className="label" htmlFor={htmlFor}>
        {label} {required && <span className="text-rose-600" aria-label="required">*</span>}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="card p-4">
      <p className="eyebrow">{label}</p>
      <p className="num mt-1.5 text-2xl font-semibold tracking-tight text-slate-900">{value}</p>
      {sub && <p className="mt-1 text-xs leading-relaxed text-slate-500">{sub}</p>}
    </div>
  )
}
