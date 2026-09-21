import { describe, expect, it } from 'vitest'
import { linkifyText } from './linkify'

describe('linkifyText', () => {
  it('leaves plain text untouched', () => {
    expect(linkifyText('buy milk')).toEqual([{ kind: 'text', value: 'buy milk' }])
  })

  it('detects https URLs', () => {
    expect(linkifyText('see https://example.com/path')).toEqual([
      { kind: 'text', value: 'see ' },
      { kind: 'url', value: 'https://example.com/path', href: 'https://example.com/path' },
    ])
  })

  it('detects www URLs and adds https', () => {
    expect(linkifyText('go to www.example.com now')).toEqual([
      { kind: 'text', value: 'go to ' },
      { kind: 'url', value: 'www.example.com', href: 'https://www.example.com' },
      { kind: 'text', value: ' now' },
    ])
  })

  it('trims trailing punctuation from URLs', () => {
    expect(linkifyText('(https://example.com).')).toEqual([
      { kind: 'text', value: '(' },
      { kind: 'url', value: 'https://example.com', href: 'https://example.com' },
      { kind: 'text', value: ').' },
    ])
  })
})
