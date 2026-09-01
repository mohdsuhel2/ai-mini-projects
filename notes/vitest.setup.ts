import 'fake-indexeddb/auto'

if (!globalThis.crypto?.randomUUID) {
  // jsdom in older Node exposes crypto without randomUUID.
  const { webcrypto } = await import('node:crypto')
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto })
}
