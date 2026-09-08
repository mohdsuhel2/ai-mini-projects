import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'
import { SiteContent } from '@/components/layout/site-content'
import { SITE } from '@/lib/site'

export const metadata: Metadata = {
  title: 'About',
  description: SITE.description,
  alternates: { canonical: '/about' },
}

/**
 * The prose that used to sit under the app.
 *
 * It was there so crawlers met real HTML on the first response, but the app is
 * a tool you open every day and the pitch is something you read once — stacking
 * them meant scrolling past the whole FAQ to reach the bottom of your own day.
 * A page of its own keeps the content indexable (it is in the sitemap and
 * linked from Settings) and gives the app its screen back.
 */
export default function AboutPage() {
  return (
    <main>
      <div className="mx-auto max-w-[1220px] px-4 pt-10 sm:px-6">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-[13px] text-fg-muted transition-colors hover:text-fg"
        >
          <ArrowLeft className="size-3.5" strokeWidth={2} aria-hidden="true" />
          Back to {SITE.name}
        </Link>
        <h1 className="mt-8 text-[28px] font-semibold tracking-[-0.025em] text-fg">
          About {SITE.name}
        </h1>
        <p className="mt-2 max-w-[60ch] text-[14.5px] leading-[1.7] text-fg-muted">
          {SITE.tagline}
        </p>
      </div>

      <SiteContent />
    </main>
  )
}
