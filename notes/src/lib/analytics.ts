/**
 * A thin, privacy-conscious wrapper over GA4.
 *
 * Events are a closed set of names with numeric or enum parameters only. Task
 * titles, notes, category names and every other piece of user content stay on
 * the device — there is no code path that could send them.
 */

export type AnalyticsEvent =
  | 'todo_created'
  | 'todo_completed'
  | 'todo_reopened'
  | 'todo_rescheduled'
  | 'activity_added'
  | 'timer_started'
  | 'timer_completed'
  | 'quick_add_used'
  | 'theme_changed'
  | 'data_exported'
  | 'data_imported'
  | 'data_cleared'
  | 'day_navigated'
  | 'category_created'

/** Only primitives, and only ones the app itself constructs. */
export type AnalyticsParams = Record<string, string | number | boolean>

declare global {
  interface Window {
    dataLayer?: unknown[]
    gtag?: (...args: unknown[]) => void
  }
}

export const GA_MEASUREMENT_ID = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID ?? ''

function honoursDoNotTrack(): boolean {
  if (typeof navigator === 'undefined') return false
  const dnt =
    navigator.doNotTrack ??
    (window as unknown as { doNotTrack?: string }).doNotTrack ??
    (navigator as unknown as { msDoNotTrack?: string }).msDoNotTrack
  return dnt === '1' || dnt === 'yes'
}

export function analyticsEnabled(): boolean {
  return Boolean(GA_MEASUREMENT_ID) && typeof window !== 'undefined' && !honoursDoNotTrack()
}

export function track(event: AnalyticsEvent, params?: AnalyticsParams): void {
  if (!analyticsEnabled()) return
  window.gtag?.('event', event, params ?? {})
}
