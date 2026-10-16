/**
 * Which employee each BUNDLED example file belongs to (tests/fixtures/example_owners.json), for the few files that do not name
 * their owner. It documents how the synthetic examples were written and is used only for the bundled examples: an owner is never
 * guessed for an uploaded file, whatever it is called.
 */
const RAW = import.meta.glob('../../../tests/fixtures/example_owners.json', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

interface Entry {
  employee_id: string
  evidence: string
}

const OWNERS: Record<string, Entry> = (() => {
  try {
    const parsed = JSON.parse(Object.values(RAW)[0] ?? '{}') as Record<string, unknown>
    return Object.fromEntries(Object.entries(parsed).filter(([k]) => !k.startsWith('_'))) as Record<string, Entry>
  } catch {
    return {}
  }
})()

/** The documented owner of a bundled example file (`dir` is the fixture folder, e.g. `isolved`), or null if none is documented. */
export function documentedOwner(dir: string, name: string): string | null {
  return OWNERS[`${dir}/${name}`]?.employee_id ?? null
}
