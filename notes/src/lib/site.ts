export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://notes.noobius.in'

export const SITE = {
  name: 'Simply Notes',
  tagline: 'Plan less. Remember more.',
  title: 'Simply Notes – A Simple Way to Plan and Track Your Day',
  description:
    'Simply Notes is a private, local-first productivity app to manage tasks, track daily activities, and understand how you spend your time. No login required.',
  url: SITE_URL,
  brand: 'Noobius',
  brandUrl: 'https://noobius.in',
} as const

export const FAQ: Array<{ question: string; answer: string }> = [
  {
    question: 'Does Simply Notes require an account?',
    answer:
      'No. There is no sign-up, no login and no password. Open the page and start using it.',
  },
  {
    question: 'Where is my data stored?',
    answer:
      'In your own browser, using IndexedDB on the device you are using. Your tasks, activities and notes are never uploaded, because Simply Notes has no server to upload them to.',
  },
  {
    question: 'Does Simply Notes work offline?',
    answer:
      'Yes, where your browser supports it. Simply Notes installs as a progressive web app and keeps working without a connection, since all of its data is already on your device.',
  },
  {
    question: 'Can I move my data to another device?',
    answer:
      'Yes. Export a JSON backup from Settings and import it in the browser you want to move to. Because data is per-device, this is also how you keep a copy before clearing your browser storage.',
  },
  {
    question: 'What is the difference between a task and an activity?',
    answer:
      'A task is something you intend to do. An activity is something you actually did. Completing a task records it as an activity automatically, so your timeline reflects the day as it happened.',
  },
]
