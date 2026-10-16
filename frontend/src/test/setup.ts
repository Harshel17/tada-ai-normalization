import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// the bundled example loader is replaced by a network-scripted double (see fixtureDouble.ts); api/fixtures.test.ts tests the real one
vi.mock('../api/fixtures', async () => import('./fixtureDouble'))

afterEach(() => cleanup())

// jsdom's File/Blob lack .text() (real browsers have it); shim it for the tests only.
if (!Blob.prototype.text) {
  Blob.prototype.text = function text(this: Blob) {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(reader.error)
      reader.readAsText(this)
    })
  }
}
