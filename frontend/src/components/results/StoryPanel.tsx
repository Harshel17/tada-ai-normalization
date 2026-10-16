import type { Issue } from '../../api/types'
import { explainIssue } from '../../lib/explain'
import type { DataStory } from '../../lib/explain'

const GROUPS: { key: 'problems' | 'notUnderstood' | 'providerGaps' | 'notes'; title: string; hint: string; tone: string }[] = [
  { key: 'problems', title: 'Needs attention', hint: 'These stop a record from being saved.', tone: 'border-rose-200 bg-rose-50' },
  { key: 'notUnderstood', title: 'Not understood, kept as received', hint: 'Nothing was guessed.', tone: 'border-amber-200 bg-amber-50' },
  { key: 'providerGaps', title: 'Not provided by this provider', hint: 'Left blank rather than invented.', tone: 'border-sky-200 bg-sky-50' },
  { key: 'notes', title: 'Worth knowing', hint: '', tone: 'border-slate-200 bg-slate-50' },
]

/** Issues of one kind, merged by code ("×3") with the plain-English meaning of that code. */
function byCode(issues: Issue[]): { code: string; count: number; text: string; where: string | null }[] {
  const map = new Map<string, { code: string; count: number; text: string; where: string | null }>()
  for (const i of issues) {
    const hit = map.get(i.code)
    if (hit) hit.count += 1
    else map.set(i.code, { code: i.code, count: 1, text: explainIssue(i).text, where: i.path })
  }
  return [...map.values()]
}

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

/** What happened to the data, counted from the response: nothing here is estimated. */
export function StoryPanel({ story }: { story: DataStory }) {
  return (
    <div className="space-y-3" data-testid="data-story">
      <dl className="grid gap-3 sm:grid-cols-3">
        <Stat id="story-converted" label="Filled in the common format" value={plural(story.convertedFields, 'field')} hint={`across ${plural(story.records, 'record')}`} />
        <Stat id="story-preserved" label="Kept exactly as received" value={plural(story.preservedValues, 'value')} hint="no standard field for these, nothing is dropped" />
        <Stat id="story-withheld" label="Sensitive, never stored in full" value={plural(story.withheld, 'value')} hint="such as SSNs and bank account numbers" />
      </dl>
      {GROUPS.map((g) => {
        const items = byCode(story[g.key])
        if (items.length === 0) return null
        return (
          <div key={g.key} className={`rounded-xl border p-4 ${g.tone}`} data-testid={`story-${g.key}`}>
            <p className="text-sm font-semibold text-slate-900">{g.title} <span className="font-normal text-slate-500">{g.hint}</span></p>
            <ul className="mt-1 space-y-1 text-sm text-slate-800">
              {items.map((it) => (
                <li key={it.code}>
                  {it.text} {it.count > 1 && <span className="text-slate-500">(×{it.count})</span>}
                  {it.where && <span className="block font-mono text-xs text-slate-500">{it.where}</span>}
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </div>
  )
}

function Stat({ id, label, value, hint }: { id: string; label: string; value: string; hint: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card" data-testid={id}>
      <dt className="eyebrow">{label}</dt>
      <dd className="num mt-1 text-lg font-semibold tracking-tight text-slate-900">{value}</dd>
      <dd className="text-xs text-slate-500">{hint}</dd>
    </div>
  )
}
