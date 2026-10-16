/**
 * A small JSON reader that keeps every number exactly as written (`3846.150` stays "3846.150").
 * The built-in JSON.parse turns numbers into floats, which would show the user a different value
 * than the one they sent. Only used to display and trace original values, never to build requests.
 */

export class RawNumber {
  constructor(readonly raw: string) {}
  toString(): string {
    return this.raw
  }
}

export type RawJson = null | boolean | string | RawNumber | RawJson[] | { [key: string]: RawJson }

export function parseRaw(text: string): RawJson | undefined {
  let i = 0
  const ws = () => {
    while (i < text.length && ' \t\n\r'.includes(text.charAt(i))) i++
  }
  const fail = (): never => {
    throw new SyntaxError('invalid')
  }
  const str = (): string => {
    const start = i
    i++
    while (i < text.length) {
      const c = text.charAt(i)
      if (c === '"') {
        i++
        return JSON.parse(text.slice(start, i)) as string
      }
      i += c === '\\' ? 2 : 1
    }
    return fail()
  }
  const value = (): RawJson => {
    ws()
    const c = text.charAt(i)
    if (c === '{') {
      i++
      ws()
      const out: { [key: string]: RawJson } = {}
      if (text.charAt(i) === '}') return (i++, out)
      for (;;) {
        ws()
        if (text.charAt(i) !== '"') fail()
        const key = str()
        ws()
        if (text.charAt(i++) !== ':') fail()
        out[key] = value()
        ws()
        const n = text.charAt(i++)
        if (n === '}') return out
        if (n !== ',') fail()
      }
    }
    if (c === '[') {
      i++
      ws()
      const out: RawJson[] = []
      if (text.charAt(i) === ']') return (i++, out)
      for (;;) {
        out.push(value())
        ws()
        const n = text.charAt(i++)
        if (n === ']') return out
        if (n !== ',') fail()
      }
    }
    if (c === '"') return str()
    for (const [word, v] of [['true', true], ['false', false], ['null', null]] as const) {
      if (text.startsWith(word, i)) return (i += word.length, v)
    }
    const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i))
    if (!m) return fail()
    i += m[0].length
    return new RawNumber(m[0])
  }
  try {
    const v = value()
    ws()
    return i === text.length ? v : undefined
  } catch {
    return undefined
  }
}

export type PathStep = string | number

/** One scalar value in a document, with the path that leads to it. */
export interface Leaf {
  path: PathStep[]
  value: string | boolean | null
  /** true when the source wrote it as a JSON number */
  numeric: boolean
}

export function leavesOf(node: RawJson, path: PathStep[] = [], out: Leaf[] = []): Leaf[] {
  if (Array.isArray(node)) node.forEach((v, i) => leavesOf(v, [...path, i], out))
  else if (node instanceof RawNumber) out.push({ path, value: node.raw, numeric: true })
  else if (node && typeof node === 'object') {
    for (const [k, v] of Object.entries(node)) {
      if (k === 'links' || k === '_links') continue // navigation URLs, not data
      leavesOf(v, [...path, k], out)
    }
  } else out.push({ path, value: node, numeric: false })
  return out
}

/** "employee[0].person.gender" */
export function pathLabel(path: PathStep[]): string {
  return path.reduce<string>((s, p) => (typeof p === 'number' ? `${s}[${p}]` : s ? `${s}.${p}` : p), '')
}

/** Resolves the `$.a.b[0]` style paths the API reports in provenance.source_path. */
export function resolveSourcePath(doc: RawJson, sourcePath: string | null): { node: RawJson; path: PathStep[] } | null {
  if (!sourcePath || !sourcePath.startsWith('$')) return null
  const steps: PathStep[] = []
  const re = /\.([A-Za-z0-9_-]+)|\[(\d+)\]/g
  let m: RegExpExecArray | null
  let consumed = 1
  while ((m = re.exec(sourcePath.slice(1)))) {
    if (m.index !== consumed - 1) return null
    steps.push(m[1] !== undefined ? m[1] : Number(m[2]))
    consumed = m.index + m[0].length + 1
  }
  if (consumed !== sourcePath.length) return null
  let node: RawJson | undefined = doc
  for (const s of steps) {
    if (typeof s === 'number') node = Array.isArray(node) ? node[s] : undefined
    else if (node && typeof node === 'object' && !Array.isArray(node) && !(node instanceof RawNumber)) node = (node as { [k: string]: RawJson })[s]
    else node = undefined
    if (node === undefined) return null // that path does not exist in the document
  }
  return { node, path: steps }
}
