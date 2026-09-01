import Link from 'next/link'
import { CalendarCheck, Clock3, Lock, WifiOff } from 'lucide-react'
import { BrandMark } from './brand'
import { FAQ, SITE } from '@/lib/site'

const FEATURES = [
  {
    icon: CalendarCheck,
    title: 'Plan your day',
    body: 'Add a task in one line, give it a day, a category and a time if it needs one. Everything else stays out of the way until you want it.',
  },
  {
    icon: Clock3,
    title: 'Track what you did',
    body: 'Log anything you actually did — a run, a meeting, an hour lost to a feed. Completed tasks record themselves, so the timeline is the day as it happened.',
  },
  {
    icon: Lock,
    title: 'Private by construction',
    body: 'Your data is stored in your own browser. There is no account and no server to send it to, so there is nothing to breach and nothing to sell.',
  },
  {
    icon: WifiOff,
    title: 'Works offline',
    body: 'Install it and it keeps working on a plane, on the underground, or on a bad connection. The data was never remote to begin with.',
  },
]

/**
 * Server-rendered content below the application. Crawlers get real HTML on the
 * first response; readers only meet it if they scroll past the tool.
 */
export function SiteContent() {
  return (
    <div className="border-t border-line bg-bg-sunk">
      <div className="mx-auto max-w-[1220px] px-4 py-16 sm:px-6 sm:py-20">
        <section className="max-w-[62ch]">
          <h2 className="text-[20px] font-semibold tracking-[-0.02em] text-fg">
            What is Simply Notes?
          </h2>
          <p className="mt-3 text-[14.5px] leading-[1.7] text-fg-muted">
            Simply Notes is a daily planner and activity tracker that runs entirely in your browser.
            It holds two ideas next to each other: the things you mean to do, and the things you
            actually did. Most task apps only track the first, which is why so few of them can tell
            you where your day went.
          </p>
          <p className="mt-3 text-[14.5px] leading-[1.7] text-fg-muted">
            There is no account to create and no subscription to cancel. Open the page and start
            typing.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-[20px] font-semibold tracking-[-0.02em] text-fg">Features</h2>
          <ul className="mt-5 grid gap-x-10 gap-y-7 sm:grid-cols-2">
            {FEATURES.map(({ icon: Icon, title, body }) => (
              <li key={title} className="max-w-[46ch]">
                <div className="flex items-center gap-2">
                  <Icon className="size-4 text-accent" strokeWidth={2} aria-hidden="true" />
                  <h3 className="text-[14px] font-medium text-fg">{title}</h3>
                </div>
                <p className="mt-1.5 text-[13.5px] leading-[1.65] text-fg-muted">{body}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-14 max-w-[62ch]">
          <h2 className="text-[20px] font-semibold tracking-[-0.02em] text-fg">Privacy</h2>
          <p className="mt-3 text-[14.5px] leading-[1.7] text-fg-muted">
            Your tasks, activities and notes stay on your device, in your browser&rsquo;s own
            storage. They are not transmitted anywhere, because Simply Notes has no backend.
          </p>
          <p className="mt-3 text-[13.5px] leading-[1.7] text-fg-subtle">
            That also means the data is only as durable as your browser storage: clearing site data
            removes it. Export a backup from Settings if it matters to you. Read the full{' '}
            <Link
              href="/privacy"
              className="text-accent underline decoration-accent-line underline-offset-2"
            >
              privacy statement
            </Link>
            .
          </p>
        </section>

        <section className="mt-14 max-w-[68ch]">
          <h2 className="text-[20px] font-semibold tracking-[-0.02em] text-fg">
            Frequently asked questions
          </h2>
          <dl className="mt-5 divide-y divide-line border-y border-line">
            {FAQ.map((item) => (
              <div key={item.question} className="py-4">
                <dt className="text-[14px] font-medium text-fg">{item.question}</dt>
                <dd className="mt-1.5 max-w-[60ch] text-[13.5px] leading-[1.65] text-fg-muted">
                  {item.answer}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        <footer className="mt-14 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line pt-7 text-[12.5px] text-fg-subtle">
          <span className="inline-flex items-center gap-2 text-fg-muted">
            <BrandMark className="size-4" />
            {SITE.name}
          </span>
          <span className="text-fg-faint">{SITE.tagline}</span>
          <span className="ml-auto flex items-center gap-4">
            <Link href="/privacy" className="transition-colors hover:text-fg">
              Privacy
            </Link>
            <a
              href={SITE.brandUrl}
              className="transition-colors hover:text-fg"
              rel="noopener noreferrer"
            >
              {SITE.brand}
            </a>
          </span>
        </footer>
      </div>
    </div>
  )
}
