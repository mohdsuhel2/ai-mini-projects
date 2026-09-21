'use client'

import { linkifyText } from '@/lib/text/linkify'
import { cn } from '@/lib/utils/cn'

export function LinkifiedText({
  text,
  className,
}: {
  text: string
  className?: string
}) {
  const segments = linkifyText(text)

  return (
    <span className={cn('whitespace-pre-wrap', className)}>
      {segments.map((segment, index) =>
        segment.kind === 'url' ? (
          <a
            key={`url-${index}`}
            href={segment.href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(event) => event.stopPropagation()}
            className="cursor-pointer text-accent underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent-hover hover:decoration-accent"
          >
            {segment.value}
          </a>
        ) : (
          <span key={`text-${index}`}>{segment.value}</span>
        ),
      )}
    </span>
  )
}
