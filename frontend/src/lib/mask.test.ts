import { describe, expect, it } from 'vitest'
import { isSensitiveKey, maskJsonText, maskObject } from './mask'

describe('isSensitiveKey', () => {
  it.each(['ssn', 'accountNum', 'accountNumber', 'account_number', 'social_security', 'documentContent', 'password', 'PIN'])('%s is sensitive', (k) => {
    expect(isSensitiveKey(k)).toBe(true)
  })
  it.each(['ssn_last4', 'account_number_masked', 'first_name', 'routing_number', 'amount'])('%s is not', (k) => {
    expect(isSensitiveKey(k)).toBe(false)
  })
})

describe('maskObject', () => {
  it('masks sensitive keys recursively and keeps last four characters', () => {
    const out = maskObject({ ssn: '123-45-6789', nested: [{ accountNum: '000123456789', bank: 'X' }], ssn_last4: '6789' })
    expect(out).toEqual({ ssn: '••••6789', nested: [{ accountNum: '••••6789', bank: 'X' }], ssn_last4: '6789' })
  })
  it('hides short values completely and does not mutate its input', () => {
    const input = { account: '7788', pin: 1234 }
    const out = maskObject(input)
    expect(out).toEqual({ account: '••••', pin: '••••' })
    expect(input).toEqual({ account: '7788', pin: 1234 })
  })
  it('leaves null / empty values alone', () => {
    expect(maskObject({ ssn: null, accountNum: '' })).toEqual({ ssn: null, accountNum: '' })
  })
})

describe('maskJsonText', () => {
  const text = `{\n  "ssn": "123-45-6789",\n  "salary": 3846.150,\n  "annualPay": "100000.00",\n  "accountNum": "000123456789",\n  "firstName": "Jordan"\n}`
  it('masks secrets but preserves every other character, including number scale', () => {
    const out = maskJsonText(text)
    expect(out).toContain('"ssn": "••••6789"')
    expect(out).toContain('"accountNum": "••••6789"')
    expect(out).toContain('"salary": 3846.150')
    expect(out).toContain('"annualPay": "100000.00"')
    expect(out).toContain('"firstName": "Jordan"')
    expect(out).not.toContain('123-45-6789')
    expect(out).not.toContain('000123456789')
  })
  it('masks UKG national id values and still produces valid JSON', () => {
    const t = '{"national_id_numbers":[{"type":"SSN","value":"900-44-3333","primary":true}]}'
    const out = maskJsonText(t)
    expect(out).not.toContain('900-44-3333')
    expect(() => JSON.parse(out)).not.toThrow()
  })
  it('masks numeric secrets and also works on text that is not valid JSON yet', () => {
    const out = maskJsonText('{"pin": 123456, oops')
    expect(out).toContain('"pin": "••••"')
    expect(out).not.toContain('123456')
  })
  it('is a no-op for text without secrets', () => {
    const plain = '{"a": 1, "b": "x"}'
    expect(maskJsonText(plain)).toBe(plain)
  })
})
