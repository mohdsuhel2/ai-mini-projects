import type { Metadata, Viewport } from 'next'
import { Inter } from 'next/font/google'
import { SITE, SITE_URL, FAQ } from '@/lib/site'
import { Analytics } from '@/components/system/analytics'
import { ServiceWorker } from '@/components/system/service-worker'
import { ThemeScript } from '@/components/system/theme-script'
import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
})

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: SITE.title,
    template: '%s · Simply Notes',
  },
  description: SITE.description,
  applicationName: SITE.name,
  keywords: [
    'todo app',
    'daily planner',
    'activity tracker',
    'time tracking',
    'local-first',
    'no login todo app',
    'private productivity app',
    'offline task manager',
  ],
  authors: [{ name: SITE.brand, url: SITE.brandUrl }],
  creator: SITE.brand,
  alternates: { canonical: '/' },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, 'max-image-preview': 'large', 'max-snippet': -1 },
  },
  openGraph: {
    type: 'website',
    url: SITE_URL,
    siteName: SITE.name,
    title: SITE.title,
    description: SITE.description,
    locale: 'en_GB',
  },
  twitter: {
    card: 'summary_large_image',
    title: SITE.title,
    description: SITE.description,
  },
  manifest: '/manifest.webmanifest',
  appleWebApp: { capable: true, title: SITE.name, statusBarStyle: 'default' },
  formatDetection: { telephone: false, date: false, address: false, email: false },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#FBFAF9' },
    { media: '(prefers-color-scheme: dark)', color: '#121215' },
  ],
}

const STRUCTURED_DATA = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'WebApplication',
      '@id': `${SITE_URL}/#app`,
      name: SITE.name,
      url: SITE_URL,
      description: SITE.description,
      applicationCategory: 'ProductivityApplication',
      operatingSystem: 'Web',
      browserRequirements: 'Requires a browser with IndexedDB support',
      isAccessibleForFree: true,
      offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
      featureList: [
        'Plan tasks by day',
        'Track what you actually did',
        'Daily timeline and time breakdown',
        'Works offline',
        'No account required',
      ],
      publisher: { '@type': 'Organization', name: SITE.brand, url: SITE.brandUrl },
    },
    {
      '@type': 'FAQPage',
      '@id': `${SITE_URL}/#faq`,
      mainEntity: FAQ.map((item) => ({
        '@type': 'Question',
        name: item.question,
        acceptedAnswer: { '@type': 'Answer', text: item.answer },
      })),
    },
  ],
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={inter.variable}>
      <head>
        <ThemeScript />
        <script
          type="application/ld+json"
          // Static, author-controlled JSON. No user content reaches this.
          dangerouslySetInnerHTML={{ __html: JSON.stringify(STRUCTURED_DATA) }}
        />
      </head>
      <body className="antialiased">
        {children}
        <ServiceWorker />
        <Analytics />
      </body>
    </html>
  )
}
