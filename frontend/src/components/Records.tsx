import type { ReactNode } from 'react'
import { useMemo, useState } from 'react'
import type { CanonicalRecord, Issue, Severity, StoredCounts } from '../api/types'
import { entityLabel } from '../api/types'
import { prettyJson, leafCount } from '../lib/json'
import { maskObject } from '../lib/mask'
import { emptyFieldCount, filledFields } from '../lib/summary'
import { Badge, severityTone } from './ui'

/** Pretty, MASKED JSON for small inline blocks. */
export function JsonBlock({ value, maxHeight = 260 }: { value: unknown; maxHeight?: number }) {
  return (
    <pre className="overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-xs leading-relaxed text-slate-100" style={{ maxHeight }}>
      {prettyJson(maskObject(value))}
    </pre>
  )
}

const humanize = (k: string) => k.replace(/_/g, ' ')

function Scalar({ value }: { value: unknown }): ReactNode {
  if (typeof value === 'boolean') return <Badge tone={value ? 'green' : 'slate'}>{value ? 'yes' : 'no'}</Badge>
  return <span className="break-all font-mono text-[13px] text-slate-800">{String(value)}</span>
}

function FieldValue({ value }: { value: unknown }): ReactNode {
  if (Array.isArray(value)) {
    return (
      <details>
        <summary className="cursor-pointer text-sm text-brand-700">{value.length} item{value.length === 1 ? '' : 's'}</summary>
        <div className="mt-2"><JsonBlock value={value} /></div>
      </details>
    )
  }
  if (value && typeof value === 'object') {
    const inline = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== null && v !== '' && typeof v !== 'object')
    return (
      <details>
        <summary className="cursor-pointer text-sm text-brand-700">
          {inline.slice(0, 3).map(([k, v]) => `${humanize(k)}: ${String(v)}`).join(' · ') || 'details'}
        </summary>
        <div className="mt-2"><JsonBlock value={value} /></div>
      </details>
    )
  }
  return <Scalar value={value} />
}

export function FieldTable({ record }: { record: CanonicalRecord }) {
  const rows = filledFields(record)
  if (!rows.length) return <p className="text-sm text-slate-500">No canonical fields are set on this record.</p>
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-[minmax(10rem,14rem)_1fr]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500 sm:pt-0.5">{humanize(k)}</dt>
          <dd className="min-w-0 pb-1"><FieldValue value={v} /></dd>
        </div>
      ))}
    </dl>
  )
}

/** One canonical record with its provenance, withheld fields and unmapped (extras) data. */
export function RecordCard({ record, defaultOpen = false }: { record: CanonicalRecord; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const p = record.provenance
  const extrasLeaves = useMemo(() => leafCount(record.extras), [record.extras])
  return (
    <article className="card" data-testid={`record-${record.record_type}`}>
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left">
        <Badge tone="indigo">{record.record_type.replace('_', ' ')}</Badge>
        <span className="font-mono text-sm font-medium text-slate-900">{record.source_id}</span>
        <span className="text-xs text-slate-500">
          {filledFields(record).length} mapped · {emptyFieldCount(record)} empty · {extrasLeaves} unmapped values kept
        </span>
        {p.redacted_paths.length > 0 && <Badge tone="amber" title="Sensitive values deliberately not stored">{p.redacted_paths.length} withheld</Badge>}
        {p.derived_fields.length > 0 && <Badge tone="blue" title="Computed by the adapter, not copied from the source">{p.derived_fields.length} derived</Badge>}
        <span className="ml-auto text-slate-400" aria-hidden>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="space-y-5 border-t border-slate-100 px-4 py-4">
          <section>
            <h4 className="mb-2 text-sm font-semibold text-slate-700">Canonical fields</h4>
            <FieldTable record={record} />
          </section>
          <section>
            <h4 className="mb-2 text-sm font-semibold text-slate-700">Provenance</h4>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-[minmax(10rem,14rem)_1fr]">
              <dt className="text-xs font-semibold uppercase text-slate-500">Source endpoint</dt>
              <dd className="font-mono text-[13px]">{p.source_endpoint ?? '—'}</dd>
              <dt className="text-xs font-semibold uppercase text-slate-500">Source path</dt>
              <dd className="font-mono text-[13px]">{p.source_path ?? '— (derived record)'}</dd>
              <dt className="text-xs font-semibold uppercase text-slate-500">Withheld (redacted)</dt>
              <dd className="font-mono text-[13px]">{p.redacted_paths.length ? p.redacted_paths.join(', ') : 'none'}</dd>
              <dt className="text-xs font-semibold uppercase text-slate-500">Derived by the adapter</dt>
              <dd className="font-mono text-[13px]">{p.derived_fields.length ? p.derived_fields.join(', ') : 'none'}</dd>
            </dl>
          </section>
          <section>
            <h4 className="mb-1 text-sm font-semibold text-slate-700">Not mapped, kept verbatim ({extrasLeaves} values)</h4>
            <p className="mb-2 text-xs text-slate-500">Source fields without a dedicated canonical field. Nothing is dropped; sensitive names are redacted.</p>
            {extrasLeaves ? <JsonBlock value={record.extras} /> : <p className="text-sm text-slate-500">Nothing: every source value was mapped.</p>}
          </section>
        </div>
      )}
    </article>
  )
}

export function IssuesTable({ issues }: { issues: Issue[] }) {
  const [filter, setFilter] = useState<Severity | 'all'>('all')
  const shown = issues.filter((i) => filter === 'all' || i.severity === filter)
  if (!issues.length) return <p className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">No issues reported.</p>
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1" role="group" aria-label="Filter by severity">
        {(['all', 'error', 'warning', 'info'] as const).map((s) => {
          const n = s === 'all' ? issues.length : issues.filter((i) => i.severity === s).length
          return (
            <button key={s} onClick={() => setFilter(s)} aria-pressed={filter === s} className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${filter === s ? 'bg-slate-900 text-white ring-slate-900' : 'bg-white text-slate-600 ring-slate-300'}`}>
              {s} ({n})
            </button>
          )
        })}
      </div>
      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="min-w-full divide-y divide-slate-200 text-sm" data-testid="issues-table">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr><th className="px-3 py-2">Severity</th><th className="px-3 py-2">Code</th><th className="px-3 py-2">Message</th><th className="px-3 py-2">Path</th><th className="px-3 py-2">Record</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {shown.map((i, n) => (
              <tr key={n}>
                <td className="px-3 py-2"><Badge tone={severityTone(i.severity)}>{i.severity}</Badge></td>
                <td className="px-3 py-2 font-mono text-xs">{i.code}</td>
                <td className="px-3 py-2">{i.message}</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-600">{i.path ?? '—'}</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-600">{i.source_id ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** inserted / updated / unchanged per record type, with a plain-language reading. */
export function StoredOutcome({ stored }: { stored: Record<string, StoredCounts> }) {
  const entries = Object.entries(stored)
  if (!entries.length) return null
  return (
    <div className="space-y-2" data-testid="stored-outcome">
      {entries.map(([type, c]) => (
        <div key={type} className="flex flex-wrap items-center gap-2 text-sm">
          <span className="w-32 font-medium text-slate-700">{entityLabel(type) === type ? type.replace('_', ' ') : entityLabel(type)}</span>
          <Badge tone={c.inserted ? 'green' : 'slate'}>{c.inserted} inserted</Badge>
          <Badge tone={c.updated ? 'amber' : 'slate'}>{c.updated} updated</Badge>
          <Badge tone={c.unchanged ? 'blue' : 'slate'}>{c.unchanged} unchanged</Badge>
          <span className="text-xs text-slate-500">
            {c.inserted + c.updated + c.unchanged === c.unchanged ? 'identical to what is already stored: nothing written' : c.updated ? 'existing rows replaced; version bumped' : 'new rows written'}
          </span>
        </div>
      ))}
    </div>
  )
}
