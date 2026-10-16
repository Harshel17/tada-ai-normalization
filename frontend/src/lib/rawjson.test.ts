import { describe, expect, it } from 'vitest'
import { leavesOf, parseRaw, pathLabel, RawNumber, resolveSourcePath } from './rawjson'

describe('parseRaw', () => {
  it('keeps numbers exactly as written', () => {
    const doc = parseRaw('{"a": 3846.150, "b": 100000.00, "c": -0.5e3, "d": 7}') as Record<string, RawNumber>
    expect(Object.values(doc).map((n) => n.raw)).toEqual(['3846.150', '100000.00', '-0.5e3', '7'])
  })
  it('reads strings with escapes, booleans, null, arrays and nesting', () => {
    expect(parseRaw('{"s": "a\\"b\\u0041", "t": true, "n": null, "l": [1, {"x": "y"}]}')).toMatchObject({ s: 'a"bA', t: true, n: null })
  })
  it.each(['', '{', '{"a":}', '[1,]', '{"a":1} x', "{'a':1}"])('returns undefined for invalid input %j', (bad) => {
    expect(parseRaw(bad)).toBeUndefined()
  })
  it('agrees with JSON.parse about which documents are valid', () => {
    for (const d of ['{}', '[]', '0', '"x"', 'null', '{"a":[1,2,{"b":false}]}', '{"a":1', '[,]', 'NaN']) {
      let valid = true
      try { JSON.parse(d) } catch { valid = false }
      expect(parseRaw(d) !== undefined, d).toBe(valid)
    }
  })
})

describe('leaves and paths', () => {
  const doc = parseRaw('{"employee": [{"id": "E1", "person": {"gender": "F"}, "n": 1.50, "links": [{"href": "x"}]}]}')!
  it('lists every scalar with its path, skipping navigation links', () => {
    const leaves = leavesOf(doc).map((l) => `${pathLabel(l.path)}=${String(l.value)}`)
    expect(leaves).toEqual(['employee[0].id=E1', 'employee[0].person.gender=F', 'employee[0].n=1.50'])
  })
  it('resolves $-style provenance paths', () => {
    const hit = resolveSourcePath(doc, '$.employee[0]')
    expect(hit && pathLabel(hit.path)).toBe('employee[0]')
    expect(resolveSourcePath(doc, '$.employee[5]')).toBeNull()
    expect(resolveSourcePath(doc, '$.nope')).toBeNull()
    expect(resolveSourcePath(doc, null)).toBeNull()
    expect(resolveSourcePath(doc, 'garbage')).toBeNull()
  })
})
