export type JsonCheck = { ok: true } | { ok: false; message: string; line?: number; column?: number }

class Bad extends Error {
  constructor(readonly pos: number, reason: string) {
    super(reason)
  }
}

/**
 * A strict JSON syntax scanner used only to LOCATE an error. It exists because the built-in
 * JSON.parse messages quote a snippet of the input (which may be a pasted SSN or account number)
 * and often omit the position. Our messages contain a reason and a line/column, never input text.
 */
function scan(text: string): void {
  let i = 0
  const ws = () => { while (i < text.length && ' \t\n\r'.includes(text.charAt(i))) i++ }
  const fail = (reason: string, at = i): never => { throw new Bad(at, reason) }
  const lit = (word: string) => {
    if (text.startsWith(word, i)) i += word.length
    else fail('Unexpected token')
  }
  const str = () => {
    const start = i
    i++ // opening quote
    while (i < text.length) {
      const c = text.charAt(i)
      if (c === '"') { i++; return }
      if (c === '\\') {
        const n = text.charAt(i + 1)
        if (n === 'u') {
          if (!/^[0-9a-fA-F]{4}$/.test(text.slice(i + 2, i + 6))) fail('Bad unicode escape', i)
          i += 6
        } else if ('"\\/bfnrt'.includes(n) && n !== '') i += 2
        else fail('Bad escape sequence', i)
      } else if (c < ' ') fail('Control character in string (use \\n)', i)
      else i++
    }
    fail('Unterminated string', start)
  }
  const num = () => {
    const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i))
    if (!m) fail('Invalid number')
    else i += m[0].length
  }
  const value = (depth: number): void => {
    if (depth > 200) fail('Nesting too deep')
    ws()
    const c = text.charAt(i)
    if (c === '{') {
      i++; ws()
      if (text.charAt(i) === '}') { i++; return }
      for (;;) {
        ws()
        if (text.charAt(i) !== '"') fail('Expected a quoted property name')
        str(); ws()
        if (text.charAt(i) !== ':') fail("Expected ':'")
        i++; value(depth + 1); ws()
        if (text.charAt(i) === ',') { i++; continue }
        if (text.charAt(i) === '}') { i++; return }
        fail("Expected ',' or '}'")
      }
    } else if (c === '[') {
      i++; ws()
      if (text.charAt(i) === ']') { i++; return }
      for (;;) {
        value(depth + 1); ws()
        if (text.charAt(i) === ',') { i++; continue }
        if (text.charAt(i) === ']') { i++; return }
        fail("Expected ',' or ']'")
      }
    } else if (c === '"') str()
    else if (c === 't') lit('true')
    else if (c === 'f') lit('false')
    else if (c === 'n') lit('null')
    else if (c === '-' || (c >= '0' && c <= '9')) num()
    else if (i >= text.length) fail('Unexpected end of input')
    else fail('Unexpected token')
  }
  value(0)
  ws()
  if (i < text.length) fail('Unexpected content after the JSON value')
}

/** Validates JSON text for the editor. Messages never echo the text itself. */
export function checkJson(text: string): JsonCheck {
  if (!text.trim()) return { ok: false, message: 'Input is empty.' }
  try {
    scan(text)
  } catch (e) {
    if (!(e instanceof Bad)) throw e
    const before = text.slice(0, e.pos)
    const line = before.split('\n').length
    const column = e.pos - before.lastIndexOf('\n')
    return { ok: false, message: `${e.message} at line ${line}, column ${column}`, line, column }
  }
  try {
    JSON.parse(text)
  } catch {
    return { ok: false, message: 'Not valid JSON.' } // the scanner and the engine disagree: report, never quote
  }
  return { ok: true }
}

export function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

/** Number of non-empty scalar leaves in a JSON value. */
export function leafCount(value: unknown): number {
  if (value === null || value === undefined || value === '') return 0
  if (Array.isArray(value)) return value.reduce<number>((n, v) => n + leafCount(v), 0)
  if (typeof value === 'object') {
    return Object.values(value as Record<string, unknown>).reduce<number>((n, v) => n + leafCount(v), 0)
  }
  return 1
}
