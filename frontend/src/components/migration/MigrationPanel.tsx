import { useMutation } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { previewMigration } from '../../api/client'
import type { ConvertResponse, MigrationPreview, MigrationFieldStatus } from '../../api/types'
import { employeesIn, buildRequest, formProblem, REASON_TEXT, sourceSuggestions, sourceView } from '../../lib/migration'
import type { FormState } from '../../lib/migration'
import { prettyJson } from '../../lib/json'
import { maskObject } from '../../lib/mask'
import { recordHeading } from '../../lib/describe'
import { Badge, ErrorBanner, Field, Spinner } from '../ui'

export const DRY_RUN = 'DRY RUN — No data has been sent to the destination provider.'

const READINESS = {
  READY: { tone: 'green', title: 'Ready', text: 'Passed the documented local checks.' },
  NEEDS_REVIEW: { tone: 'amber', title: 'Needs review', text: 'A person has to look at the points below before this could be used.' },
  BLOCKED: { tone: 'red', title: 'Blocked', text: 'A value the destination requires is missing.' },
} as const
const FIELD_TONE: Record<MigrationFieldStatus, 'green' | 'indigo' | 'amber' | 'slate' | 'red'> = {
  mapped: 'green', operator_supplied: 'indigo', needs_review: 'amber', omitted: 'slate', missing_required: 'red',
}
const FIELD_TEXT: Record<MigrationFieldStatus, string> = {
  mapped: 'mapped', operator_supplied: 'you supplied', needs_review: 'needs review', omitted: 'left out', missing_required: 'missing',
}
const SEVERITY_TEXT = { blocking: 'Blocking', review: 'Review', info: 'Note' } as const
const SEVERITY_TONE = { blocking: 'red', review: 'amber', info: 'blue' } as const

const EMPTY: FormState = { companyId: '', username: '', confirmSourceUsername: false, useEmployeeNumber: false, useSourceId: false, addressCountry: '', einId: '' }

export function DryRunBanner() {
  return (
    <div role="note" data-testid="dry-run" className="sticky top-0 z-10 rounded-lg border-2 border-amber-400 bg-amber-50 px-4 py-3 text-sm font-semibold text-amber-950 shadow-sm">
      {DRY_RUN}
    </div>
  )
}

/**
 * Preview migrating one normalized employee to UKG Ready. The reviewer picks an employee from the result they just
 * produced; nothing is copied by hand. The server only builds a draft: it never contacts the destination.
 */
export function MigrationPanel({ conversion, fileTexts }: { conversion: ConvertResponse; fileTexts: Record<string, string> }) {
  const choices = useMemo(() => employeesIn(conversion), [conversion])
  const [key, setKey] = useState(choices[0]?.key ?? '')
  const [form, setForm] = useState<FormState>(EMPTY)
  const [moreOpen, setMoreOpen] = useState(false)
  const countryInput = useRef<HTMLInputElement>(null)
  const choice = choices.find((c) => c.key === key) ?? choices[0]
  const preview = useMutation<MigrationPreview, Error, void>({
    mutationFn: async () => previewMigration(buildRequest(choice!.record, form)),
  })

  // a new result or another employee starts from a clean form: confirmations never carry over
  useEffect(() => {
    setKey(choices[0]?.key ?? '')
    setForm(EMPTY)
    preview.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversion])

  if (!choice) return null
  const suggestions = sourceSuggestions(choice.record)
  const problem = formProblem(form, choice.record)
  const set = (patch: Partial<FormState>) => {
    setForm((f) => ({ ...f, ...patch }))
    preview.reset()
  }
  const pick = (k: string) => {
    setKey(k)
    setForm((f) => ({ ...f, username: '', confirmSourceUsername: false, useEmployeeNumber: false, useSourceId: false }))
    preview.reset()
  }
  /** Reveal the collapsed options and put the cursor in the country field; nothing is filled in for the reviewer. */
  const askForCountry = () => {
    setMoreOpen(true)
    setTimeout(() => countryInput.current?.focus(), 0)
  }
  const usingSourceUsername = !!suggestions.username && form.username.trim() === suggestions.username

  return (
    <section aria-labelledby="mig" data-testid="migration" className="card space-y-4 p-5 sm:p-6">
      <div>
        <h2 id="mig" className="text-lg font-semibold tracking-tight text-slate-900">Preview migration to UKG Ready</h2>
        <p className="mt-1 text-sm text-slate-600">
          See how one of the employees above would look as a UKG Ready “create employee” request. This only builds a draft for you to read.
        </p>
      </div>
      <DryRunBanner />

      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Employee" htmlFor="mig-employee" hint="Taken from the result above.">
          <select id="mig-employee" className="input" value={choice.key} onChange={(e) => pick(e.target.value)}>
            {choices.map((c) => <option key={c.key} value={c.key}>{recordHeading(c.record)} · {c.file}</option>)}
          </select>
        </Field>
        <Field label="Destination company ID" required htmlFor="mig-company" hint="The UKG Ready company the request would be sent to. It goes in the URL, not in the data.">
          <input id="mig-company" className="input" value={form.companyId} autoComplete="off" spellCheck={false} onChange={(e) => set({ companyId: e.target.value })} />
        </Field>
        <div>
          <Field label="Username in UKG Ready" required htmlFor="mig-username" hint="UKG requires one and the source data cannot supply it, so you choose it. Nothing is guessed.">
            <input id="mig-username" className="input" value={form.username} autoComplete="off" spellCheck={false} onChange={(e) => set({ username: e.target.value, confirmSourceUsername: false })} />
          </Field>
          {suggestions.username && !usingSourceUsername && (
            <button type="button" className="mt-1 rounded-full bg-white px-2 py-0.5 text-xs text-brand-700 ring-1 ring-inset ring-brand-300 hover:bg-brand-50" onClick={() => set({ username: suggestions.username, confirmSourceUsername: false })}>
              Suggestion: the source username “{suggestions.username}”
            </button>
          )}
          {usingSourceUsername && (
            <label className="mt-2 flex items-start gap-2 text-sm text-slate-700">
              <input type="checkbox" className="mt-1" checked={form.confirmSourceUsername} onChange={(e) => set({ confirmSourceUsername: e.target.checked })} />
              <span>I confirm “{suggestions.username}” is the right username in UKG Ready. Until then it is not sent.</span>
            </label>
          )}
        </div>
        <fieldset className="space-y-1.5">
          <legend className="label">Source ids <span className="font-normal text-slate-500">(UKG assigns its own employee id)</span></legend>
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input type="checkbox" className="mt-1" disabled={!suggestions.employeeNumber} checked={form.useEmployeeNumber} onChange={(e) => set({ useEmployeeNumber: e.target.checked })} />
            <span>Send the source employee number{suggestions.employeeNumber ? ` (${suggestions.employeeNumber})` : ' (this record has none)'} as <code>employee_id</code></span>
          </label>
          <label className="flex items-start gap-2 text-sm text-slate-700">
            <input type="checkbox" className="mt-1" checked={form.useSourceId} onChange={(e) => set({ useSourceId: e.target.checked })} />
            <span>Send the source record id ({suggestions.sourceId}) as <code>external_id</code></span>
          </label>
        </fieldset>
      </div>
      <details className="text-sm" open={moreOpen} onToggle={(e) => setMoreOpen((e.target as HTMLDetailsElement).open)}>
        <summary className="cursor-pointer text-slate-600">More options</summary>
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          <Field label="Address country (ISO alpha-3)" htmlFor="mig-country" hint="Only if the source address has no usable country, for example USA.">
            <input id="mig-country" ref={countryInput} className="input" value={form.addressCountry} maxLength={3} onChange={(e) => set({ addressCountry: e.target.value })} />
          </Field>
          <Field label="Destination EIN id" htmlFor="mig-ein" hint="Only for multi-EIN companies.">
            <input id="mig-ein" className="input" inputMode="numeric" value={form.einId} onChange={(e) => set({ einId: e.target.value })} />
          </Field>
        </div>
      </details>

      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-primary" disabled={!!problem || preview.isPending} onClick={() => preview.mutate()}>
          {preview.isPending ? <Spinner label="Building the draft" /> : 'Preview migration to UKG Ready'}
        </button>
        {problem && <span className="text-sm text-amber-800" data-testid="mig-problem">{problem}</span>}
      </div>
      {preview.error && <ErrorBanner title="The preview could not be built">{preview.error.message}</ErrorBanner>}
      {preview.data && <PreviewResult preview={preview.data} choice={choice} fileText={fileTexts[choice.file]} onAskCountry={askForCountry} />}
    </section>
  )
}

function PreviewResult({ preview, choice, fileText, onAskCountry }: { preview: MigrationPreview; choice: ReturnType<typeof employeesIn>[number]; fileText: string | undefined; onAskCountry: () => void }) {
  const r = READINESS[preview.readiness]
  const sourcePath = choice.record.provenance.source_path
  const source = sourceView(fileText, sourcePath)
  const grouped = Object.entries(
    preview.not_migrated.reduce<Record<string, typeof preview.not_migrated>>((acc, n) => ((acc[n.reason] ??= []).push(n), acc), {}),
  )
  return (
    <div className="space-y-5" data-testid="mig-result" aria-live="polite">
      <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4 sm:p-5" data-testid="mig-readiness" data-readiness={preview.readiness}>
        <p className="flex flex-wrap items-center gap-2 text-base font-semibold text-slate-900">
          Readiness <Badge tone={r.tone}>{r.title}</Badge>
        </p>
        <p className="mt-1 text-sm text-slate-700">{r.text}</p>
        <p className="mt-2 text-sm text-slate-700">
          <strong>Local checks only.</strong> {preview.readiness_meaning}
        </p>
        <p className="mt-2 text-xs text-slate-500">
          Would be: <code>{preview.contract.method} {preview.contract.endpoint}</code> (not sent). {preview.contract.caveat}
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-3" data-testid="mig-panes">
        <Pane title="1 · Source" note={`${choice.file} · ${sourcePath ?? 'location unknown'}`} testId="pane-source">
          {source ? <pre className="max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-xs text-slate-100">{source}</pre> : <p className="text-sm text-slate-500">The original text is not available for this record (it may have come from a file you removed). Nothing is made up.</p>}
        </Pane>
        <Pane title="2 · Canonical" note="the standard model" testId="pane-canonical">
          <pre className="max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-xs text-slate-100">{prettyJson(maskObject({ ...choice.record, provenance: undefined }))}</pre>
        </Pane>
        <Pane title="3 · UKG Ready draft" note="what would be the request body" testId="pane-destination">
          <pre className="max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 font-mono text-xs text-slate-100">{prettyJson(maskObject(preview.request_body))}</pre>
        </Pane>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-800">What UKG Ready requires</h3>
        <ul className="space-y-1 text-sm" data-testid="mig-requirements">
          {preview.requirements.map((q) => (
            <li key={q.field} className="flex items-center gap-2">
              <Badge tone={q.satisfied ? 'green' : 'red'}>{q.satisfied ? 'supplied' : 'missing'}</Badge>
              <code>{q.field}</code>
            </li>
          ))}
        </ul>
      </div>

      {preview.issues.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-800">Points to look at</h3>
          <ul className="space-y-1.5 text-sm" data-testid="mig-issues">
            {preview.issues.map((i, n) => (
              <li key={`${i.code}-${n}`} className="flex flex-wrap items-start gap-2">
                <Badge tone={SEVERITY_TONE[i.severity]}>{SEVERITY_TEXT[i.severity]}</Badge>
                <span className="min-w-0 text-slate-800">{i.message}</span>
                {(i.code === 'address_country_required' || i.code === 'country_not_alpha3') && (
                  <button type="button" className="text-brand-700 underline" onClick={onAskCountry}>Enter the address country</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-800">Field by field</h3>
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm" data-testid="mig-fields">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr><th className="px-3 py-2">UKG field</th><th className="px-3 py-2">From</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Value</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {preview.fields.map((f, n) => (
                <tr key={`${f.destination}-${n}`}>
                  <td className="px-3 py-1.5 font-mono text-xs">{f.destination}</td>
                  <td className="px-3 py-1.5 text-slate-600">{f.source ?? 'you'}</td>
                  <td className="px-3 py-1.5"><Badge tone={FIELD_TONE[f.status]}>{FIELD_TEXT[f.status]}</Badge>{f.note && <span className="ml-2 text-xs text-slate-500">{f.note}</span>}</td>
                  <td className="px-3 py-1.5 font-mono text-xs break-all">{f.value === null || f.value === undefined ? '' : typeof f.value === 'object' ? JSON.stringify(maskObject(f.value)) : String(f.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 className="mb-1 text-sm font-semibold text-slate-800">Not in the request</h3>
        <p className="mb-2 text-xs text-slate-500">Nothing is dropped silently. These canonical fields are not part of the draft, and why.</p>
        <div className="space-y-3" data-testid="mig-not-migrated">
          {grouped.map(([reason, items]) => (
            <div key={reason}>
              <p className="text-sm font-medium text-slate-800">{REASON_TEXT[reason as keyof typeof REASON_TEXT]?.title ?? reason} <span className="font-normal text-slate-500">· {REASON_TEXT[reason as keyof typeof REASON_TEXT]?.help}</span></p>
              <ul className="ml-5 list-disc text-sm text-slate-700">
                {items.map((n) => <li key={n.source}><code>{n.source}</code> <span className="text-slate-500">{n.note}</span></li>)}
              </ul>
            </div>
          ))}
          {grouped.length === 0 && <p className="text-sm text-slate-500">Everything in this record is in the draft.</p>}
        </div>
      </div>
    </div>
  )
}

function Pane({ title, note, children, testId }: { title: string; note: string; children: React.ReactNode; testId: string }) {
  return (
    <div className="min-w-0 space-y-1" data-testid={testId}>
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      <p className="truncate text-xs text-slate-500" title={note}>{note}</p>
      {children}
    </div>
  )
}
