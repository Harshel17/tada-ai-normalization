import { useState } from 'react'
import type { ConvertResponse } from '../../api/types'
import { overviewOf } from '../../lib/overview'
import { Stat } from '../ui'

const NOUN: Record<string, [string, string]> = {
  employee: ['employee', 'employees'], paycheck: ['paycheck', 'paychecks'], payroll_run: ['payroll run', 'payroll runs'],
  direct_deposit: ['direct deposit', 'direct deposits'], deduction: ['deduction', 'deductions'], company: ['company', 'companies'],
}
const countText = (type: string, n: number) => `${n} ${(NOUN[type] ?? [type.replace(/_/g, ' '), `${type.replace(/_/g, ' ')}s`])[n === 1 ? 0 : 1]}`

/**
 * A summary of THIS result. Every figure is counted from the returned records; the footnote says exactly what is counted
 * so the numbers are not mistaken for a coverage score.
 */
export function ResultsOverview({ conversion }: { conversion: ConvertResponse }) {
  const o = overviewOf(conversion)
  const [showLinks, setShowLinks] = useState(false)
  const [exactIds, setExactIds] = useState(false)
  if (o.records === 0) return null
  const types = Object.entries(o.byType).map(([k, n]) => countText(k, n)).join(', ')
  const resolved = o.links.filter((l) => l.resolved).length
  const unresolved = o.links.length - resolved
  const integrity = o.integrity
  return (
    <section aria-label="Result summary" data-testid="overview" className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat label="Records produced" value={o.records} sub={types} />
        <Stat label="Mapped fields" value={o.mappedFields} sub="typed fields filled in the standard model" />
        <Stat label="Preserved, not mapped" value={o.unmappedValues} sub="source values kept as-is in “extras”" />
        <Stat label="Relationships" value={o.links.length} sub={o.links.length ? `${resolved} found${unresolved ? `, ${unresolved} point outside this data` : ''}` : 'no references between these records'} />
        <Stat label="Integrity checks" value={integrity ? `${integrity.passed} passed` : 'n/a'} sub={integrity ? `${integrity.warning} warning · ${integrity.failed} failed · ${integrity.not_evaluated} not evaluated` : 'not run for this result'} />
        <Stat label="Protected values" value={o.withheld + o.masked} sub={`${o.withheld} withheld from extras · ${o.masked} shown masked or as last four`} />
      </div>
      {o.links.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm shadow-card" data-testid="relationships">
          <button className="font-medium text-brand-700 underline" aria-expanded={showLinks} onClick={() => setShowLinks((v) => !v)}>
            {showLinks ? 'Hide' : 'Show'} how these records connect
          </button>
          <span className="ml-2 text-slate-500">Each line is a reference stored in a record, checked against the records returned here.</span>
          {showLinks && (
            <>
              <label className="mt-2 flex items-center gap-2 text-xs text-slate-600">
                <input type="checkbox" checked={exactIds} onChange={(e) => setExactIds(e.target.checked)} />
                Show the exact record ids (bank-deposit ids are never shown: they contain account digits)
              </label>
              <ul className="mt-2 space-y-1" aria-label="Record relationships">
                {o.links.map((l, i) => (
                  <li key={`${l.from}-${l.to}-${i}`} className="flex flex-wrap gap-x-2 text-slate-800">
                    <span className="font-medium">{l.from}</span>
                    <span className="text-slate-500">→ {l.via}</span>
                    <span>{l.to}</span>
                    <span className={l.resolved ? 'text-emerald-700' : 'text-amber-700'}>{l.resolved ? 'found' : 'not in this data'}</span>
                    {exactIds && (
                      <span className="font-mono text-xs text-slate-500" data-testid="exact-ids">
                        {l.fromId ?? 'id withheld'} → {l.toId}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      <p className="text-xs text-slate-500">
        Counts come from this result only: “mapped” counts the typed fields that hold a value (not a percentage of the provider’s schema);
        “preserved” counts values kept in extras because no typed field exists for them.
      </p>
    </section>
  )
}
