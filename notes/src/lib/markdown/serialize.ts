/**
 * Turns the editor's DOM back into markdown.
 *
 * The note editor is a single live surface rather than a source view with a
 * preview, but what gets stored is still markdown: it survives an export, a
 * diff and any editor the user ever moves to, and it keeps the rendering path
 * unchanged. This function is the other half of `renderMarkdown`, and the two
 * are kept deliberately narrow so a note can round-trip without drift.
 *
 * Anything outside the supported set degrades to its text content rather than
 * being dropped — a note must never lose words on a save.
 */

const BLOCK_SEPARATOR = '\n\n'

function inlineFrom(node: Node): string {
  if (node.nodeType === 3) return node.textContent ?? ''
  if (node.nodeType !== 1) return ''

  const element = node as HTMLElement
  const tag = element.tagName.toLowerCase()
  const inner = childrenInline(element)

  switch (tag) {
    case 'br':
      return '\n'
    case 'strong':
    case 'b':
      return inner.trim() ? `**${inner}**` : inner
    case 'em':
    case 'i':
      return inner.trim() ? `*${inner}*` : inner
    case 'del':
    case 's':
      return inner.trim() ? `~~${inner}~~` : inner
    case 'code':
      return inner.trim() ? `\`${inner}\`` : inner
    case 'a': {
      const href = element.getAttribute('href') ?? ''
      return href ? `[${inner}](${href})` : inner
    }
    // A checkbox is serialised by its list item, not by itself.
    case 'input':
      return ''
    default:
      return inner
  }
}

function childrenInline(element: Node): string {
  let out = ''
  element.childNodes.forEach((child) => {
    out += inlineFrom(child)
  })
  return out
}

function listItemMarkdown(item: HTMLElement, prefix: string): string {
  const checkbox = item.querySelector('input[type="checkbox"]')
  const text = childrenInline(item).replace(/\s+/g, ' ').trim()
  if (!checkbox) return `${prefix}${text}`
  const checked = (checkbox as HTMLInputElement).checked
  return `${prefix}[${checked ? 'x' : ' '}] ${text}`
}

function blockFrom(element: HTMLElement): string | null {
  const tag = element.tagName.toLowerCase()

  switch (tag) {
    case 'h1':
      return `# ${childrenInline(element).trim()}`
    case 'h2':
      return `## ${childrenInline(element).trim()}`
    case 'h3':
    case 'h4':
    case 'h5':
    case 'h6':
      return `### ${childrenInline(element).trim()}`
    case 'blockquote':
      return childrenInline(element)
        .split('\n')
        .map((line) => `> ${line.trim()}`)
        .join('\n')
    case 'pre':
      return `\`\`\`\n${element.textContent ?? ''}\n\`\`\``
    case 'hr':
      return '---'
    case 'ul':
    case 'ol': {
      // Nested lists are flattened to one level: the toolbar offers no
      // indentation, and a shape the renderer cannot reproduce would silently
      // change the note on its next load.
      const items = Array.from(element.querySelectorAll('li'))
      const ordered = tag === 'ol'
      return items
        .map((item, index) => listItemMarkdown(item, ordered ? `${index + 1}. ` : '- '))
        .filter((line) => line.trim() !== '-' && line.trim() !== `${items.length}.`)
        .join('\n')
    }
    default: {
      const text = childrenInline(element)
      return text.trim() ? text.replace(/\n{2,}/g, '\n') : null
    }
  }
}

/** Serialises the children of an editor root into markdown. */
export function htmlToMarkdown(root: HTMLElement): string {
  const blocks: string[] = []

  root.childNodes.forEach((node) => {
    if (node.nodeType === 3) {
      const text = node.textContent ?? ''
      if (text.trim()) blocks.push(text.trim())
      return
    }
    if (node.nodeType !== 1) return

    const block = blockFrom(node as HTMLElement)
    if (block !== null && block.trim() !== '') blocks.push(block)
  })

  return blocks.join(BLOCK_SEPARATOR).replace(/\n{3,}/g, BLOCK_SEPARATOR).trim()
}

/**
 * The first line of a note, used as its title when the user has not set one.
 * Falls back to an empty string so the caller decides what "untitled" means.
 */
export function firstLine(markdown: string): string {
  const line = markdown
    .split('\n')
    .map((l) => l.replace(/^\s{0,3}(#{1,6}\s+|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+|>\s?)/, '').trim())
    .find((l) => l.length > 0)
  return line ?? ''
}
