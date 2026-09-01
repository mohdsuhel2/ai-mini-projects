'use client'

import { useEffect, useState } from 'react'

/**
 * A ticking clock, running only while something is actually displaying it.
 * `active` exists so the interval stops when no timer is on screen.
 */
export function useNow(intervalMs = 1000, active = true): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!active) return
    // The first tick lands within one interval; elapsed time is clamped at
    // zero until then, so a stale initial value is never rendered as negative.
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs, active])

  return now
}
