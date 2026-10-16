/**
 * Display-time masking. The console never shows SSNs, bank account numbers, document contents
 * or credentials. This is defence in depth: the backend already redacts, and the browser view of
 * *input* JSON (which the user supplied) is masked too. Request bodies are never altered by it.
 */

// Lower-cased key names whose values are secrets (mirrors SENSITIVE_KEYS in adapters/base.py).
const SENSITIVE = new Set(
  [
    'ssn', 'cobraSSN', 'coveredSsn', 'eeSSN', 'i9SSN', 'employeeSSN', 'ssnUnmasked', 'socialSecurity',
    'usSocialSecurityNumber', 'social_security', 'social_insurance', 'national_insurance', 'bsn',
    'accountNum', 'accountNumber', 'account_number', 'bank_account_number', 'account',
    'foreignPassportNum', 'foreignPassportNumber', 'driverLicenseId', 'password', 'default_password',
    'pin', 'secret', 'client_secret', 'ssoToken', 'access_token', 'refresh_token',
    'documentContent', 'profilePicture', 'documentNumber', 'account_number_hash',
  ].map((k) => k.toLowerCase()),
)

// Keys that look sensitive but hold a deliberately safe, already-reduced value.
const SAFE_SUFFIX = /(_last4|_masked)$/i

export const MASK = '••••'

export function isSensitiveKey(key: string): boolean {
  return SENSITIVE.has(key.toLowerCase()) && !SAFE_SUFFIX.test(key)
}

function maskScalar(value: unknown): string {
  if (typeof value === 'string') {
    const alnum = value.replace(/[^A-Za-z0-9]/g, '')
    return alnum.length > 4 ? `${MASK}${alnum.slice(-4)}` : MASK
  }
  return MASK
}

/** Masks sensitive keys in a parsed value (used for API output shown in the Explorer). */
export function maskObject<T>(value: T): T {
  if (Array.isArray(value)) return value.map((v) => maskObject(v)) as unknown as T
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] =
        isSensitiveKey(k) && v !== null && v !== '' && typeof v !== 'object' ? maskScalar(v) : maskObject(v)
    }
    return out as T
  }
  return value
}

const KEY_VALUE = /("([^"\\]+)"\s*:\s*)("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?)/g
const NATIONAL_IDS = /("national_id_numbers"\s*:\s*\[)([\s\S]*?)(\])/

/**
 * Masks JSON *text* without parsing it, so everything else (number scale such as 500.00,
 * key order, formatting) stays exactly as the user wrote it.
 */
export function maskJsonText(text: string): string {
  const masked = text.replace(KEY_VALUE, (whole, head: string, key: string, value: string) => {
    if (!isSensitiveKey(key)) return whole
    // quoted text keeps its last four characters; a bare number (PIN, id) is hidden entirely
    const inner: unknown = value.startsWith('"') ? (JSON.parse(value) as string) : Number(value)
    return `${head}${JSON.stringify(maskScalar(inner))}`
  })
  // UKG national_id_numbers[].value is an identity number under a generic key name
  return masked.replace(NATIONAL_IDS, (_w, open: string, body: string, close: string) => {
    const hidden = body.replace(/("value"\s*:\s*)"((?:[^"\\]|\\.)*)"/g, (_m, h: string, v: string) => {
      return `${h}${JSON.stringify(maskScalar(v))}`
    })
    return `${open}${hidden}${close}`
  })
}
