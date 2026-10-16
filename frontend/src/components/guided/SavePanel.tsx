import type { IngestResponse, StoredCounts } from '../../api/types'
import { plural } from '../../lib/explain'
import { explainIssue } from '../../lib/explain'
import { Badge, Spinner } from '../ui'

interface Props {
  workspace: string
  canSave: boolean
  reason: string | null
  pending: boolean
  error: string | null
  response: { status: number; body: IngestResponse } | undefined
  onSave: () => void
}

/** What a save did, in words, from the `stored` counts the API returned. */
export function describeSave(stored: Record<string, StoredCounts>): string[] {
  const out: string[] = []
  for (const [type, c] of Object.entries(stored)) {
    const noun = type.replace('_', ' ')
    if (c.inserted) out.push(`${plural(c.inserted, `new ${noun} record`)} saved`)
    if (c.updated) out.push(`${plural(c.updated, `${noun} record`)} updated (it already existed with different details)`)
    if (c.unchanged) out.push(`${plural(c.unchanged, `${noun} record`)} already saved, so nothing changed`)
  }
  return out
}

export function SavePanel({ workspace, canSave, reason, pending, error, response, onSave }: Props) {
  const ok = response?.status === 200 && response.body.ok
  return (
    <section aria-labelledby="save-title" className="rounded-xl border-2 border-dashed border-slate-300 bg-white p-5" data-testid="save-panel">
      <h2 id="save-title" className="text-lg font-semibold text-slate-900">Optional: save the converted data</h2>
      <p className="mt-1 max-w-3xl text-sm text-slate-600" data-testid="save-explainer">
        <strong>Converting does not save anything.</strong> If you want to keep these records, saving stores them in the local PostgreSQL database
        under the workspace “{workspace || '…'}”, so you can browse them later in Data Explorer. Saving the same file again changes nothing. A record that two files both contain is stored once; saving the second file updates that row (its version and the file location it records).
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button className="btn-secondary" disabled={!canSave || pending} onClick={onSave}>
          {pending ? <Spinner label="Saving" /> : 'Save to Database'}
        </button>
        {!canSave && reason && <span className="text-sm text-slate-500">{reason}</span>}
      </div>
      {error && <p role="alert" className="mt-3 rounded-lg bg-rose-50 p-2 text-sm text-rose-800">Could not save: {error}</p>}
      {response && ok && (
        <div className="mt-4 space-y-2" data-testid="save-outcome">
          <Badge tone="green">saved</Badge>
          <ul className="list-disc pl-5 text-sm text-slate-800">
            {describeSave(response.body.stored).map((t) => <li key={t}>{t}</li>)}
          </ul>
          <a className="inline-block text-sm font-medium text-brand-700 underline" href="#/explorer">Browse saved data in Data Explorer →</a>
        </div>
      )}
      {response && !ok && (
        <div className="mt-4 space-y-2" data-testid="save-outcome">
          <Badge tone="red">not saved</Badge>
          <p className="text-sm text-slate-800">Nothing was written. A file is only saved if every record in it converts without errors.</p>
          <ul className="list-disc pl-5 text-sm text-slate-700">
            {response.body.issues.filter((i) => i.severity === 'error').map((i, n) => <li key={n}>{explainIssue(i).text}</li>)}
          </ul>
        </div>
      )}
    </section>
  )
}
