import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

// The REAL loader (the global setup swaps in a network double for the other tests).
const real = () => vi.importActual<typeof import('./fixtures')>('./fixtures')
const ROOT = path.resolve(__dirname, '../../../tests/fixtures')
afterEach(() => vi.unstubAllGlobals())

describe('bundled example files', () => {
  it('lists exactly the synthetic files of the three provider folders, in a stable order', async () => {
    const { fetchFixtureIndex } = await real()
    const index = await fetchFixtureIndex()
    const onDisk = ['isolved', 'prismhr', 'ukg_ready'].flatMap((dir) =>
      readdirSync(path.join(ROOT, dir)).filter((n) => n.endsWith('.json')).sort().map((name) => ({ dir, name })),
    )
    expect(index).toEqual(onDisk)
    expect(new Set(index.map((f) => f.dir))).toEqual(new Set(['isolved', 'prismhr', 'ukg_ready']))
  })

  it('never includes the live-shape folder or anything outside the three folders', async () => {
    const { fetchFixtureIndex, fetchFixtureText } = await real()
    const index = await fetchFixtureIndex()
    expect(index.some((f) => /live/i.test(f.dir) || f.name.includes('/'))).toBe(false)
    await expect(fetchFixtureText({ dir: 'live_shapes', name: 'anything.json' })).rejects.toMatchObject({ status: 404 })
    await expect(fetchFixtureText({ dir: '../..', name: 'package.json' })).rejects.toMatchObject({ status: 404 })
    await expect(fetchFixtureText({ dir: 'prismhr', name: '../isolved/legals.json' })).rejects.toMatchObject({ status: 404 })
  })

  it('returns each file byte for byte, exactly as stored, and makes no network request', async () => {
    const fetchSpy = vi.fn(() => { throw new Error('network used') })
    vi.stubGlobal('fetch', fetchSpy)
    const { fetchFixtureIndex, fetchFixtureText } = await real()
    for (const f of await fetchFixtureIndex()) {
      expect(await fetchFixtureText(f)).toBe(readFileSync(path.join(ROOT, f.dir, f.name), 'utf8'))
    }
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(await fetchFixtureText({ dir: 'prismhr', name: 'employee_get_employee.json' })).toContain('"annualPay": "100000.00"')
  })

  it('keeps the example identifiers the examples were written with', async () => {
    const { fetchFixtureText } = await real()
    expect(await fetchFixtureText({ dir: 'prismhr', name: 'client_master.json' })).toContain('"ACME"')
    expect(await fetchFixtureText({ dir: 'ukg_ready', name: 'employee_bundle.json' })).toContain('"mdiaz"')
  })
})
