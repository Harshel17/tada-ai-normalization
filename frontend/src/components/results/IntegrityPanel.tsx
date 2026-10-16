import type { IntegrityCheck, IntegrityReport, IntegrityStatus } from '../../api/types'
import { Badge } from '../ui'

const STATUS: Record<IntegrityStatus, { label: string; tile: string; tone: 'green' | 'amber' | 'red' | 'slate'; rank: number; bar: string; dot: string }> = {
  failed: { label: 'Failed', tile: 'Failed', tone: 'red', rank: 0, bar: 'bg-rose-500', dot: 'bg-rose-500' },
  warning: { label: 'Warning', tile: 'Warnings', tone: 'amber', rank: 1, bar: 'bg-amber-400', dot: 'bg-amber-400' },
  passed: { label: 'Passed', tile: 'Passed', tone: 'green', rank: 2, bar: 'bg-emerald-500', dot: 'bg-emerald-500' },
  not_evaluated: { label: 'Not evaluated', tile: 'Not evaluated', tone: 'slate', rank: 3, bar: 'bg-slate-300', dot: 'bg-slate-300' },
}
const ORDER: IntegrityStatus[] = ['passed', 'warning', 'failed', 'not_evaluated']
const ROW: Record<IntegrityStatus, string> = {
  failed: 'border-l-2 border-rose-400 bg-rose-50/60',
  warning: 'border-l-2 border-amber-400 bg-amber-50/60',
  passed: 'border-l-2 border-transparent',
  not_evaluated: 'border-l-2 border-transparent',
}

/** Failed first, then warnings, passed and not evaluated; within a status the engine's own order is kept. */
export const sortChecks = (checks: IntegrityCheck[]): IntegrityCheck[] =>
  checks.map((c, i) => ({ c, i })).sort((a, b) => STATUS[a.c.status].rank - STATUS[b.c.status].rank || a.i - b.i).map((x) => x.c)

/** Checks grouped by the category the engine gave them: worst category first, the engine's order otherwise. */
function byCategory(checks: IntegrityCheck[]): { group: string; checks: IntegrityCheck[] }[] {
  const groups = new Map<string, IntegrityCheck[]>()
  for (const c of checks) groups.set(c.group, [...(groups.get(c.group) ?? []), c])
  const worst = (cs: IntegrityCheck[]) => Math.min(...cs.map((c) => STATUS[c.status].rank))
  return [...groups].map(([group, cs], i) => ({ group, checks: sortChecks(cs), i })).sort((a, b) => worst(a.checks) - worst(b.checks) || a.i - b.i)
}

function StatusIcon({ status }: { status: IntegrityStatus }) {
  const common = { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', strokeWidth: 1.9, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true, className: 'shrink-0' }
  switch (status) {
    case 'passed':
      return <svg {...common} stroke="#059669"><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></svg>
    case 'warning':
      return <svg {...common} stroke="#d97706"><path d="M12 4 3 20h18L12 4Z" /><path d="M12 10v4M12 17h.01" /></svg>
    case 'failed':
      return <svg {...common} stroke="#e11d48"><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></svg>
    default:
      return <svg {...common} stroke="#94a3b8"><circle cx="12" cy="12" r="9" strokeDasharray="3 3" /><path d="M9 12h6" /></svg>
  }
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/**
 * A data-quality dashboard over the deterministic checks. Every result is one of four statuses, a check that lacks what it
 * needs says "Not evaluated" and why, failed and warning checks come first and are tinted, and nothing is collapsed.
 * Passing checks show that records are consistent with each other; they never prove ownership or correctness.
 */
export function IntegrityPanel({ report }: { report: IntegrityReport }) {
  const total = ORDER.reduce((n, s) => n + (report.summary[s] ?? 0), 0)
  const attention = (report.summary.failed ?? 0) + (report.summary.warning ?? 0)
  return (
    <section aria-labelledby="integrity-title" className="card p-5 sm:p-6" data-testid="integrity-panel">
      <h3 id="integrity-title" className="text-lg font-semibold tracking-tight text-slate-900">Data quality &amp; integrity</h3>
      <p className="mt-1 max-w-3xl text-sm leading-relaxed text-slate-600">
        Automatic checks that the converted records fit together. They only compare what the files contain: a check that does not have what it needs says
        “Not evaluated” and why. Nothing is guessed, and nothing here changes your data.
      </p>
      <p className="mt-2 max-w-3xl text-xs leading-relaxed text-slate-500" data-testid="integrity-caveat">
        A passing check means the records are consistent with each other, not that they are correct. In particular, these checks cannot verify who owns records whose
        employee you assigned yourself.
      </p>
      <ul className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Check results" data-testid="integrity-summary">
        {ORDER.map((s) => (
          <li key={s} className="rounded-xl border border-slate-200 bg-white px-3.5 py-3">
            <p className="flex items-center gap-1.5 text-xs font-medium text-slate-600">
              <span className={`h-2 w-2 rounded-full ${STATUS[s].dot}`} aria-hidden />
              {STATUS[s].tile}
            </p>
            <p className="num mt-1 text-2xl font-semibold tracking-tight text-slate-900" data-testid={`integrity-count-${s}`}>{report.summary[s] ?? 0}</p>
          </li>
        ))}
      </ul>
      {total > 0 && (
        <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-slate-100" aria-hidden>
          {ORDER.map((s) => (report.summary[s] ? <span key={s} className={STATUS[s].bar} style={{ width: `${((report.summary[s] ?? 0) / total) * 100}%` }} /> : null))}
        </div>
      )}
      {attention > 0 && (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900" data-testid="integrity-attention">
          {plural(attention, 'check needs', 'checks need')} your attention. They are listed first in their category and highlighted.
        </p>
      )}
      <div className="mt-5 space-y-5" aria-label="All checks">
        {byCategory(report.checks).map(({ group, checks }) => {
          const counts = ORDER.map((s) => [s, checks.filter((c) => c.status === s).length] as const).filter(([, n]) => n > 0)
          return (
            <div key={group} data-testid={`integrity-group-${group}`}>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-sm font-semibold text-slate-800">{group}</h4>
                <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                  {counts.map(([s, n]) => (
                    <span key={s} className="inline-flex items-center gap-1"><span className={`h-1.5 w-1.5 rounded-full ${STATUS[s].dot}`} aria-hidden />{n} {STATUS[s].label.toLowerCase()}</span>
                  ))}
                </p>
              </div>
              <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
                {checks.map((c) => (
                  <li key={c.id} className={`px-4 py-3.5 ${ROW[c.status]}`} data-testid={`check-${c.id}`}>
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5"><StatusIcon status={c.status} /></span>
                      <div className="min-w-0 flex-1">
                        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-900">
                          <span data-testid={`check-status-${c.id}`}><Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge></span>
                          <span>{c.title}</span>
                          <span className="sr-only">Category: {c.group}</span>
                        </p>
                        <p className={`mt-1 text-sm leading-relaxed ${c.status === 'not_evaluated' ? 'text-slate-500' : 'text-slate-600'}`}>{c.reason}</p>
                        {c.examples.length > 0 && (
                          <details className="mt-1 text-sm">
                            <summary className="cursor-pointer text-brand-700">{c.examples.length === c.flagged ? `Show the ${c.examples.length}` : `Show ${c.examples.length} of ${c.flagged}`} affected</summary>
                            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-slate-700">
                              {c.examples.map((e, i) => <li key={`${e.record}-${i}`}><span className="font-mono text-xs">{e.record}</span>: {e.detail}</li>)}
                            </ul>
                          </details>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      </div>
    </section>
  )
}
