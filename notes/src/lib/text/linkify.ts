export type TextSegment =
  | { kind: 'text'; value: string }
  | { kind: 'url'; value: string; href: string }

const URL_PATTERN = /(?:https?:\/\/|www\.)[^\s<>"']+/gi

function stripTrailingPunctuation(url: string): string {
  return url.replace(/[.,;:!?)]+$/g, '')
}

function normalizeHref(raw: string): string | null {
  const trimmed = stripTrailingPunctuation(raw)
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  if (/^www\./i.test(trimmed)) return `https://${trimmed}`
  return null
}

/** Split plain text into literal spans and safe http(s) links. */
export function linkifyText(text: string): TextSegment[] {
  if (!text) return [{ kind: 'text', value: '' }]

  const segments: TextSegment[] = []
  let lastIndex = 0

  for (const match of text.matchAll(URL_PATTERN)) {
    const raw = match[0]
    const index = match.index ?? 0
    if (index > lastIndex) {
      segments.push({ kind: 'text', value: text.slice(lastIndex, index) })
    }
    const value = stripTrailingPunctuation(raw)
    const href = normalizeHref(value)
    if (href) {
      segments.push({ kind: 'url', value, href })
      lastIndex = index + value.length
    } else {
      segments.push({ kind: 'text', value: raw })
      lastIndex = index + raw.length
    }
  }

  if (lastIndex < text.length) {
    segments.push({ kind: 'text', value: text.slice(lastIndex) })
  }

  return segments.length > 0 ? segments : [{ kind: 'text', value: text }]
}
