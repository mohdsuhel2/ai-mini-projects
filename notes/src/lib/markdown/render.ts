/**
 * A deliberately small markdown renderer for note bodies.
 *
 * The security model is the point: HTML is escaped unconditionally, before any
 * markup is generated, and the renderer only ever emits tags it constructs
 * itself. Raw HTML in a note body therefore cannot execute — a stronger
 * guarantee than sanitising a general-purpose parser's output, at no
 * dependency cost.
 *
 * Anything outside the supported set renders as literal text. That is the
 * intended failure mode: a note never silently loses content.
 */

const SAFE_PROTOCOL = /^(https?:|mailto:)/i

/**
 * Private-use codepoints, so a placeholder can never collide with something
 * the author actually typed, and never survives escaping as markup.
 */
const MARK_OPEN = ''
const MARK_CLOSE = ''

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Inline code is lifted out first so nothing inside it is formatted further. */
function renderInline(raw: string): string {
  const codeSpans: string[] = []
  const withPlaceholders = raw
    .replace(new RegExp(`[${MARK_OPEN}${MARK_CLOSE}]`, 'g'), '')
    .replace(/`([^`]+)`/g, (_match, code: string) => {
      codeSpans.push(code)
      return `${MARK_OPEN}${codeSpans.length - 1}${MARK_CLOSE}`
    })

  let html = escapeHtml(withPlaceholders)

  html = html.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label: string, href: string) => {
    // An unsafe protocol loses its link but keeps its words.
    if (!SAFE_PROTOCOL.test(href)) return label
    return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
  })

  html = html
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
    // A non-space neighbour is required, so "2 * 3 = 6" stays arithmetic.
    .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?![*\w])/g, '$1<em>$2</em>')
    .replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?![_\w])/g, '$1<em>$2</em>')

  return html.replace(
    new RegExp(`${MARK_OPEN}(\\d+)${MARK_CLOSE}`, 'g'),
    (_match, index: string) => `<code>${escapeHtml(codeSpans[Number(index)])}</code>`,
  )
}

interface ListItem {
  content: string
  checked: boolean | null
}

function renderListItems(items: ListItem[], ordered: boolean): string {
  const body = items
    .map((item) => {
      if (item.checked === null) return `<li>${renderInline(item.content)}</li>`
      const checked = item.checked ? ' checked' : ''
      // contenteditable="false" keeps the box out of the text flow while
      // leaving it clickable, so a task can be ticked in the note itself.
      return (
        `<li class="task"><input type="checkbox" contenteditable="false"${checked} />` +
        `<span>${renderInline(item.content)}</span></li>`
      )
    })
    .join('')
  return ordered ? `<ol>${body}</ol>` : `<ul>${body}</ul>`
}

const RULE = /^(-{3,}|\*{3,}|_{3,})\s*$/
const HEADING = /^(#{1,3})\s+(.*)$/
const QUOTE = /^>\s?/
const FENCE = /^```/
const UNORDERED = /^[-*+]\s+(.*)$/
const ORDERED = /^\d+[.)]\s+(.*)$/

function startsBlock(line: string): boolean {
  return (
    FENCE.test(line) ||
    HEADING.test(line) ||
    QUOTE.test(line) ||
    RULE.test(line) ||
    UNORDERED.test(line) ||
    ORDERED.test(line)
  )
}

export function renderMarkdown(source: string): string {
  if (!source || !source.trim()) return ''

  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const out: string[] = []

  let index = 0
  while (index < lines.length) {
    const line = lines[index]

    // Fenced code is consumed verbatim and never formatted.
    if (FENCE.test(line)) {
      const body: string[] = []
      index += 1
      while (index < lines.length && !FENCE.test(lines[index])) {
        body.push(lines[index])
        index += 1
      }
      // Skip the closing fence when there is one. An unterminated fence still
      // renders its content rather than swallowing the rest of the note.
      if (index < lines.length) index += 1
      out.push(`<pre><code>${escapeHtml(body.join('\n'))}</code></pre>`)
      continue
    }

    if (!line.trim()) {
      index += 1
      continue
    }

    if (RULE.test(line)) {
      out.push('<hr />')
      index += 1
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      const level = heading[1].length
      out.push(`<h${level}>${renderInline(heading[2].trim())}</h${level}>`)
      index += 1
      continue
    }

    if (QUOTE.test(line)) {
      const body: string[] = []
      while (index < lines.length && QUOTE.test(lines[index])) {
        body.push(lines[index].replace(QUOTE, ''))
        index += 1
      }
      out.push(`<blockquote>${renderInline(body.join(' '))}</blockquote>`)
      continue
    }

    if (UNORDERED.test(line) || ORDERED.test(line)) {
      const isOrdered = ORDERED.test(line)
      const pattern = isOrdered ? ORDERED : UNORDERED
      const items: ListItem[] = []
      while (index < lines.length && pattern.test(lines[index])) {
        const content = pattern.exec(lines[index])![1]
        const task = /^\[([ xX])\]\s+(.*)$/.exec(content)
        items.push(
          task
            ? { content: task[2], checked: task[1].toLowerCase() === 'x' }
            : { content, checked: null },
        )
        index += 1
      }
      out.push(renderListItems(items, isOrdered))
      continue
    }

    // Everything else is a paragraph, running to a blank line or the next block.
    const paragraph: string[] = []
    while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) {
      paragraph.push(lines[index])
      index += 1
    }
    out.push(`<p>${paragraph.map(renderInline).join('<br />')}</p>`)
  }

  return out.join('')
}

/** One line of prose for list rows and search, with markup removed. */
export function plainTextPreview(source: string, limit = 140): string {
  if (!source) return ''
  const text = source
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6})\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s{0,3}[-*+]\s+(\[[ xX]\]\s+)?/gm, '')
    .replace(/^\s{0,3}\d+[.)]\s+/gm, '')
    .replace(/^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$/gm, ' ')
    .replace(/(\*\*|~~|__)/g, '')
    .replace(/[*_]/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  if (text.length <= limit) return text
  const cut = text.slice(0, limit)
  const lastSpace = cut.lastIndexOf(' ')
  return `${(lastSpace > limit * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`
}
