import { describe, expect, it } from 'vitest'
import { renderMarkdown, plainTextPreview } from './render'

describe('escaping', () => {
  it('never emits raw HTML from the source', () => {
    const html = renderMarkdown('<script>alert(1)</script>')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })

  it('escapes HTML inside code spans and fences', () => {
    expect(renderMarkdown('`<b>x</b>`')).toContain('&lt;b&gt;')
    expect(renderMarkdown('```\n<img onerror=x>\n```')).toContain('&lt;img onerror=x&gt;')
  })

  it('escapes ampersands and quotes', () => {
    expect(renderMarkdown('Tom & "Jerry"')).toContain('Tom &amp; &quot;Jerry&quot;')
  })

  it('refuses a javascript: link but keeps its text', () => {
    const html = renderMarkdown('[click](javascript:alert(1))')
    expect(html).not.toContain('javascript:')
    expect(html).toContain('click')
  })

  it('allows http, https and mailto links', () => {
    expect(renderMarkdown('[a](https://example.com)')).toContain('href="https://example.com"')
    expect(renderMarkdown('[b](http://example.com)')).toContain('href="http://example.com"')
    expect(renderMarkdown('[c](mailto:a@b.com)')).toContain('href="mailto:a@b.com"')
  })

  it('adds rel and target to links', () => {
    const html = renderMarkdown('[a](https://example.com)')
    expect(html).toContain('rel="noopener noreferrer"')
    expect(html).toContain('target="_blank"')
  })
})

describe('block elements', () => {
  it('renders headings up to level three', () => {
    expect(renderMarkdown('# One')).toContain('<h1>One</h1>')
    expect(renderMarkdown('## Two')).toContain('<h2>Two</h2>')
    expect(renderMarkdown('### Three')).toContain('<h3>Three</h3>')
  })

  it('treats a hash without a space as literal text', () => {
    expect(renderMarkdown('#hashtag')).toContain('<p>#hashtag</p>')
  })

  it('renders paragraphs split by blank lines', () => {
    const html = renderMarkdown('One\n\nTwo')
    expect(html).toContain('<p>One</p>')
    expect(html).toContain('<p>Two</p>')
  })

  it('keeps a single newline inside one paragraph as a break', () => {
    expect(renderMarkdown('One\nTwo')).toContain('One<br />Two')
  })

  it('renders unordered lists', () => {
    const html = renderMarkdown('- a\n- b')
    expect(html).toContain('<ul>')
    expect(html).toContain('<li>a</li>')
    expect(html).toContain('<li>b</li>')
  })

  it('renders ordered lists', () => {
    const html = renderMarkdown('1. a\n2. b')
    expect(html).toContain('<ol>')
    expect(html).toContain('<li>a</li>')
  })

  it('renders task lists with checkbox state', () => {
    const html = renderMarkdown('- [ ] open\n- [x] done')
    expect(html).toContain('type="checkbox"')
    expect(html).toContain('checked')
    // Not editable as text, but still clickable so a task can be ticked.
    expect(html).toContain('contenteditable="false"')
  })

  it('renders blockquotes', () => {
    expect(renderMarkdown('> quoted')).toContain('<blockquote>')
  })

  it('renders fenced code blocks verbatim', () => {
    const html = renderMarkdown('```\nconst a = 1\nconst b = 2\n```')
    expect(html).toContain('<pre><code>')
    expect(html).toContain('const a = 1\nconst b = 2')
  })

  it('does not apply inline formatting inside a fence', () => {
    expect(renderMarkdown('```\n**not bold**\n```')).toContain('**not bold**')
  })

  it('renders horizontal rules', () => {
    expect(renderMarkdown('---')).toContain('<hr />')
  })

  it('leaves an unterminated fence as a code block rather than losing it', () => {
    const html = renderMarkdown('```\nunclosed')
    expect(html).toContain('<pre><code>')
    expect(html).toContain('unclosed')
  })
})

describe('inline elements', () => {
  it.each([
    ['**bold**', '<strong>bold</strong>'],
    ['*italic*', '<em>italic</em>'],
    ['_italic_', '<em>italic</em>'],
    ['`code`', '<code>code</code>'],
    ['~~gone~~', '<del>gone</del>'],
  ])('renders %s', (input, expected) => {
    expect(renderMarkdown(input)).toContain(expected)
  })

  it('renders bold inside a list item', () => {
    expect(renderMarkdown('- a **b** c')).toContain('<li>a <strong>b</strong> c</li>')
  })

  it('leaves an unmatched asterisk alone', () => {
    expect(renderMarkdown('2 * 3 = 6')).toContain('2 * 3 = 6')
  })

  it('does not format inside inline code', () => {
    expect(renderMarkdown('`**x**`')).toContain('<code>**x**</code>')
  })
})

describe('empty input', () => {
  it.each(['', '   ', '\n\n'])('returns nothing for %j', (input) => {
    expect(renderMarkdown(input)).toBe('')
  })
})

describe('plainTextPreview', () => {
  it('strips markup for a one-line preview', () => {
    expect(plainTextPreview('## Title\n\nSome **bold** text')).toBe('Title Some bold text')
  })

  it('truncates long text on a word boundary', () => {
    const preview = plainTextPreview('word '.repeat(60), 40)
    expect(preview.length).toBeLessThanOrEqual(41)
    expect(preview.endsWith('…')).toBe(true)
  })

  it('returns an empty string for an empty body', () => {
    expect(plainTextPreview('')).toBe('')
  })
})
