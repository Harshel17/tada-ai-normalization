import { describe, expect, it } from 'vitest'
import { checkJson, leafCount } from './json'

describe('checkJson', () => {
  it('accepts valid JSON', () => expect(checkJson('{"a": [1, 2]}')).toEqual({ ok: true }))
  it('rejects empty input', () => expect(checkJson('  ')).toMatchObject({ ok: false, message: 'Input is empty.' }))
  it('reports the line of a syntax error without echoing the input', () => {
    const secret = '900-44-3333'
    const r = checkJson(`{\n  "ssn": "${secret}",\n  "a": oops\n}`)
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(r.line).toBe(3)
      expect(r.message).not.toContain(secret)
    }
  })
})

describe('checkJson: locating errors', () => {
  const bad = (text: string) => {
    const r = checkJson(text)
    if (r.ok) throw new Error('expected invalid')
    return r
  }
  it.each([
    ['{"a": 1,}', 'Expected a quoted property name'],
    ['{"a" 1}', "Expected ':'"],
    ['[1 2]', "Expected ',' or ']'"],
    ['{"a": "x', 'Unterminated string'],
    ['{"a": 01}', "Expected ',' or '}'"],
    ['{"a": tru}', 'Unexpected token'],
    ['{"a": 1} extra', 'Unexpected content after the JSON value'],
    ['', 'Input is empty.'],
    ['{"a": "x\ny"}', 'Control character in string'],
  ])('%j -> %s', (text, reason) => expect(bad(text).message).toContain(reason))

  it('agrees with JSON.parse on a corpus of valid and invalid documents', () => {
    const docs = ['{}', '[]', '0', '-1.5e+3', 'null', '"x"', '{"a":[1,{"b":null}]}', '{"a":1', '[,]', '{"a":}', "{'a':1}", 'NaN', '[1,]', '{"a":"\\u12"}', '{"a":"\\q"}', ' {"a" : [ true , false ] } ']
    for (const d of docs) {
      let valid = true
      try { JSON.parse(d) } catch { valid = false }
      expect(checkJson(d).ok, d).toBe(valid)
    }
  })

  it('reports line and column and never quotes the input', () => {
    const r = bad('{\n  "ssn": "900-44-3333",\n  "a": oops\n}')
    expect(r).toMatchObject({ line: 3, column: 8 })
    expect(r.message).not.toContain('900')
    expect(r.message).not.toContain('oops')
  })
})

describe('leafCount', () => {
  it('counts non-empty scalars only', () => {
    expect(leafCount({ a: 1, b: '', c: null, d: [1, { e: 'x', f: 0 }], g: {} })).toBe(4)
  })
})
