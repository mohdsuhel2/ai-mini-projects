import type { Metadata } from 'next'
import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'
import { SITE } from '@/lib/site'

export const metadata: Metadata = {
  title: 'Privacy',
  description:
    'How Simply Notes handles your data: everything you write stays in your own browser, and analytics never receives your content.',
  alternates: { canonical: '/privacy' },
}

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-[68ch] px-5 py-14 sm:py-20">
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-[13px] text-fg-muted transition-colors hover:text-fg"
      >
        <ArrowLeft className="size-3.5" strokeWidth={2} aria-hidden="true" />
        Back to {SITE.name}
      </Link>

      <h1 className="mt-8 text-[28px] font-semibold tracking-[-0.025em] text-fg">Privacy</h1>
      <p className="mt-2 text-[13px] text-fg-subtle">Last updated 1 September 2026</p>

      <div className="mt-8 space-y-7 text-[14.5px] leading-[1.7] text-fg-muted">
        <section>
          <h2 className="mb-2 text-[16px] font-semibold text-fg">What we store, and where</h2>
          <p>
            Every task, activity, note, category and preference you create in Simply Notes is
            written to IndexedDB in the browser you are using. It stays on that device. There is no
            account system and no application server that receives your content, so there is no
            copy of it anywhere else.
          </p>
          <p className="mt-3">
            Because the data is local, it is bound to that browser profile. Clearing your browsing
            data, using private browsing, or switching device or browser means starting from an
            empty app. Settings has an export that writes everything to a JSON file you control.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-[16px] font-semibold text-fg">Analytics</h2>
          <p>
            This deployment may include Google Analytics 4 to count how the app is used. It records
            anonymous interaction events — that a task was created, that the theme was changed —
            along with the standard information a browser sends any website it visits, such as
            approximate location, device type and referring page.
          </p>
          <p className="mt-3">
            It never receives your content. Task titles, activity descriptions, notes and category
            names are not included in any event, and no code path exists that would send them. If
            your browser has Do Not Track enabled, analytics does not load at all.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-[16px] font-semibold text-fg">What we do not do</h2>
          <p>
            No advertising, no profiling, no selling or sharing of data with third parties, no email
            collection, and no tracking of you across other websites.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-[16px] font-semibold text-fg">An honest limitation</h2>
          <p>
            Local-first is a strong privacy property, but it is not a guarantee about your device.
            Anyone with access to your unlocked browser profile can read what is stored there. If
            that matters for what you are writing down, use your operating system&rsquo;s own
            protections — a login password, disk encryption, a separate browser profile.
          </p>
        </section>

        <section>
          <h2 className="mb-2 text-[16px] font-semibold text-fg">Contact</h2>
          <p>
            Simply Notes is published by{' '}
            <a
              href={SITE.brandUrl}
              rel="noopener noreferrer"
              className="text-accent underline decoration-accent-line underline-offset-2"
            >
              {SITE.brand}
            </a>
            .
          </p>
        </section>
      </div>
    </main>
  )
}
