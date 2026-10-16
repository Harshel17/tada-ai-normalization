import { useState } from 'react'
import type { CanonicalRecord } from '../../api/types'
import { recordHeading } from '../../lib/describe'
import { readableSections } from '../../lib/results'

const TABS_UP_TO = 8

/**
 * Plain-language view of the converted records: one section per topic, each row built only from fields that
 * exist on the record the API returned. Records are chosen with tabs (a few) or a list (many).
 */
export function RecordSummary({ records }: { records: CanonicalRecord[] }) {
  const [picked, setPicked] = useState(0)
  const index = Math.min(picked, Math.max(records.length - 1, 0))
  const record = records[index]
  if (!record) return null
  const sections = readableSections(record)
  return (
    <div className="space-y-4" data-testid="readable-summary">
      {records.length > 1 && records.length <= TABS_UP_TO && (
        <div role="tablist" aria-label="Converted records" className="flex flex-wrap gap-1.5">
          {records.map((r, i) => (
            <button key={`${r.record_type}-${r.source_id}-${i}`} role="tab" aria-selected={i === index} onClick={() => setPicked(i)}
              className={`rounded-full px-3.5 py-1.5 text-sm font-medium ring-1 ring-inset transition-colors ${i === index ? 'bg-brand-600 text-white ring-brand-600 shadow-sm' : 'bg-white text-slate-700 ring-slate-300 hover:bg-slate-50'}`}>
              {recordHeading(r)}
            </button>
          ))}
        </div>
      )}
      {records.length > TABS_UP_TO && (
        <label className="block text-sm text-slate-600">
          Record{' '}
          <select className="input inline-block w-auto max-w-full" aria-label="Choose a converted record" value={index} onChange={(e) => setPicked(Number(e.target.value))}>
            {records.map((r, i) => <option key={`${r.record_type}-${r.source_id}-${i}`} value={i}>{recordHeading(r)}</option>)}
          </select>
          <span className="ml-2 text-slate-500">({index + 1} of {records.length})</span>
        </label>
      )}
      <h4 className="text-lg font-semibold tracking-tight text-slate-900" data-testid="record-heading">{recordHeading(record)}</h4>
      {sections.length === 0 && <p className="text-sm text-slate-500">This record has no values to describe in plain words.</p>}
      {sections.map((s) => (
        <section key={s.title} aria-label={s.title} className="rounded-xl border border-slate-200 bg-white p-4">
          <h5 className="eyebrow mb-3">{s.title}</h5>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-[minmax(11rem,16rem)_1fr]">
            {s.rows.map((r, i) => (
              <div key={`${r.label}-${i}`} className="contents">
                <dt className="text-sm text-slate-600">{r.label}</dt>
                <dd className="min-w-0 pb-1">
                  <span className="break-words text-sm font-medium text-slate-900">{r.value}</span>
                  {r.note && <span className="block text-xs text-slate-500">{r.note}</span>}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  )
}
