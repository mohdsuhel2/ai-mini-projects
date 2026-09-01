'use client'

import Script from 'next/script'
import { GA_MEASUREMENT_ID, analyticsEnabled } from '@/lib/analytics'

/**
 * Loaded only when a measurement ID is configured and Do Not Track is off, and
 * only after the page is interactive so it cannot affect LCP. Page content is
 * never passed to it — the app sends a fixed set of event names and counts.
 */
export function Analytics() {
  if (!GA_MEASUREMENT_ID || !analyticsEnabled()) return null

  return (
    <>
      <Script
        src={`https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`}
        strategy="afterInteractive"
      />
      <Script id="ga-init" strategy="afterInteractive">
        {`
          window.dataLayer = window.dataLayer || [];
          function gtag(){dataLayer.push(arguments);}
          window.gtag = gtag;
          gtag('js', new Date());
          gtag('config', '${GA_MEASUREMENT_ID}', {
            anonymize_ip: true,
            send_page_view: true
          });
        `}
      </Script>
    </>
  )
}
