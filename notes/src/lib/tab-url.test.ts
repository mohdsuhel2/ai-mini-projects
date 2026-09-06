import { describe, expect, it } from 'vitest'
import { DEFAULT_TAB, modeToTab, parseTab, tabToMode, urlForTab } from './tab-url'

describe('parseTab', () => {
  it('reads a valid tab', () => {
    expect(parseTab('?tab=notes')).toBe('notes')
    expect(parseTab('?tab=todos')).toBe('todos')
    expect(parseTab('?tab=insights')).toBe('insights')
  })

  it('defaults to Notes when the parameter is absent', () => {
    expect(parseTab('')).toBe('notes')
    expect(parseTab('?other=1')).toBe('notes')
    expect(DEFAULT_TAB).toBe('notes')
  })

  it('tolerates case and stray whitespace', () => {
    expect(parseTab('?tab=TODOS')).toBe('todos')
    expect(parseTab('?tab=%20notes%20')).toBe('notes')
  })

  it('falls back rather than failing on an unknown value', () => {
    expect(parseTab('?tab=nonsense')).toBe('notes')
    expect(parseTab('?tab=')).toBe('notes')
  })

  it('reads the tab alongside other parameters', () => {
    expect(parseTab('?utm_source=x&tab=todos&ref=y')).toBe('todos')
  })
})

describe('urlForTab', () => {
  it('sets the parameter on a bare path', () => {
    expect(urlForTab('/', 'todos')).toBe('/?tab=todos')
  })

  it('replaces an existing tab rather than appending a second one', () => {
    expect(urlForTab('/?tab=notes', 'todos')).toBe('/?tab=todos')
  })

  it('preserves other parameters and the hash', () => {
    expect(urlForTab('/?ref=abc#section', 'notes')).toBe('/?ref=abc&tab=notes#section')
  })

  it('keeps a non-root path', () => {
    expect(urlForTab('/privacy?x=1', 'todos')).toBe('/privacy?x=1&tab=todos')
  })
})

describe('tab and mode map to each other', () => {
  it.each([
    ['notes', 'notes'],
    ['todos', 'day'],
    ['insights', 'insights'],
  ] as const)('%s tab is the %s mode', (tab, mode) => {
    expect(tabToMode(tab)).toBe(mode)
    expect(modeToTab(mode)).toBe(tab)
  })
})
