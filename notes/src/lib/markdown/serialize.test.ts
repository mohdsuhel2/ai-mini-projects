import { describe, expect, it } from 'vitest'
import { firstLine, htmlToMarkdown } from './serialize'
import { renderMarkdown } from './render'

function root(html: string): HTMLElement {
  const node = document.createElement('div')
  node.innerHTML = html
  return node
}

const md = (html: string) => htmlToMarkdown(root(html))

describe('blocks', () => {
  it.each([
    ['<h1>Title</h1>', '# Title'],
    ['<h2>Section</h2>', '## Section'],
    ['<h3>Detail</h3>', '### Detail'],
    ['<p>Plain text</p>', 'Plain text'],
    ['<blockquote>Quoted</blockquote>', '> Quoted'],
    ['<hr>', '---'],
  ])('serialises %s', (html, expected) => {
    expect(md(html)).toBe(expected)
  })

  it('separates blocks with a blank line', () => {
    expect(md('<h2>One</h2><p>Two</p>')).toBe('## One\n\nTwo')
  })

  it('treats a bare div as a paragraph, which is what browsers produce', () => {
    expect(md('<div>Typed line</div>')).toBe('Typed line')
  })

  it('drops empty blocks rather than emitting blank markdown', () => {
    expect(md('<p></p><p>Real</p><div><br></div>')).toBe('Real')
  })
})

describe('inline', () => {
  it.each([
    ['<p><strong>bold</strong></p>', '**bold**'],
    ['<p><b>bold</b></p>', '**bold**'],
    ['<p><em>italic</em></p>', '*italic*'],
    ['<p><i>italic</i></p>', '*italic*'],
    ['<p><code>code</code></p>', '`code`'],
    ['<p><del>gone</del></p>', '~~gone~~'],
    ['<p><a href="https://x.com">link</a></p>', '[link](https://x.com)'],
  ])('serialises %s', (html, expected) => {
    expect(md(html)).toBe(expected)
  })

  it('keeps surrounding text', () => {
    expect(md('<p>a <strong>b</strong> c</p>')).toBe('a **b** c')
  })

  it('ignores empty emphasis rather than emitting bare asterisks', () => {
    expect(md('<p><strong> </strong>text</p>')).toBe('text')
  })

  it('keeps the text of an unsupported tag', () => {
    expect(md('<p><span style="color:red">kept</span></p>')).toBe('kept')
  })
})

describe('lists', () => {
  it('serialises an unordered list', () => {
    expect(md('<ul><li>a</li><li>b</li></ul>')).toBe('- a\n- b')
  })

  it('numbers an ordered list', () => {
    expect(md('<ol><li>a</li><li>b</li><li>c</li></ol>')).toBe('1. a\n2. b\n3. c')
  })

  it('keeps inline formatting inside items', () => {
    expect(md('<ul><li>a <strong>b</strong></li></ul>')).toBe('- a **b**')
  })

  it('serialises task items with their state', () => {
    const html =
      '<ul><li class="task"><input type="checkbox" checked><span>done</span></li>' +
      '<li class="task"><input type="checkbox"><span>open</span></li></ul>'
    expect(md(html)).toBe('- [x] done\n- [ ] open')
  })
})

describe('round trip', () => {
  it.each([
    '# Heading\n\nA paragraph with **bold** and *italic*.',
    '## Steps\n\n1. First\n2. Second',
    '- one\n- two\n- three',
    '- [x] done\n- [ ] open',
    '> A quote\n\nAfter the quote.',
    'Just a line of text.',
  ])('markdown survives render then serialise: %j', (source) => {
    expect(htmlToMarkdown(root(renderMarkdown(source)))).toBe(source)
  })
})

describe('firstLine', () => {
  it('strips a heading marker', () => {
    expect(firstLine('## Deploy runbook\n\nBody')).toBe('Deploy runbook')
  })

  it('strips a list marker', () => {
    expect(firstLine('- buy milk')).toBe('buy milk')
  })

  it('strips a task marker', () => {
    expect(firstLine('- [ ] buy milk')).toBe('buy milk')
  })

  it('skips leading blank lines', () => {
    expect(firstLine('\n\n\nReal content')).toBe('Real content')
  })

  it('is empty for an empty note', () => {
    expect(firstLine('')).toBe('')
    expect(firstLine('   \n  ')).toBe('')
  })
})
