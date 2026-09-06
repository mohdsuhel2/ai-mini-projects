'use client'

import { useEffect } from 'react'

export function ServiceWorker() {
  useEffect(() => {
    if (!('serviceWorker' in navigator)) return

    // A worker registered by an earlier production build keeps controlling this
    // origin in development, and it serves `/_next/static/` cache-first. Those
    // paths are only immutable within one build, so a dev server reusing a
    // chunk name is answered from a stale cache — stylesheets silently a build
    // behind. Development tears the worker down rather than leaving it to it.
    if (process.env.NODE_ENV !== 'production') {
      void (async () => {
        const registrations = await navigator.serviceWorker.getRegistrations()
        if (registrations.length === 0) return
        await Promise.all(registrations.map((registration) => registration.unregister()))
        const names = await caches.keys()
        await Promise.all(names.map((name) => caches.delete(name)))
        // The page still has the stale worker's assets; only a reload drops them.
        window.location.reload()
      })()
      return
    }

    const register = () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {
        // An unavailable service worker costs offline support, nothing else.
      })
    }

    if (document.readyState === 'complete') register()
    else window.addEventListener('load', register, { once: true })
  }, [])

  return null
}
