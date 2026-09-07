/*
 * Simply Notes service worker.
 *
 * The app's data never leaves IndexedDB, so there is nothing to sync here — the
 * only job is making the shell available without a network.
 *
 * Hashed build assets are immutable, so they are cache-first. Navigations are
 * network-first with a short timeout: an online user must never be served a
 * stale shell, and an offline user must always get one.
 */

const VERSION = 'v1'
const SHELL_CACHE = `simply-notes-shell-${VERSION}`
const ASSET_CACHE = `simply-notes-assets-${VERSION}`
const OFFLINE_URL = '/'
const NAVIGATION_TIMEOUT_MS = 3000

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll([OFFLINE_URL, '/manifest.webmanifest']))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== SHELL_CACHE && key !== ASSET_CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  )
})

function isImmutableAsset(url) {
  return url.pathname.startsWith('/_next/static/')
}

function isStaticAsset(url) {
  return /\.(?:png|svg|ico|webp|woff2?|json|webmanifest)$/.test(url.pathname)
}

async function networkFirstNavigation(request) {
  const cache = await caches.open(SHELL_CACHE)
  try {
    const response = await Promise.race([
      fetch(request),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('timeout')), NAVIGATION_TIMEOUT_MS),
      ),
    ])
    if (response && response.ok) cache.put(OFFLINE_URL, response.clone())
    return response
  } catch {
    return (await cache.match(request)) || (await cache.match(OFFLINE_URL)) || Response.error()
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName)
  const cached = await cache.match(request)
  if (cached) return cached
  const response = await fetch(request)
  if (response && response.ok) cache.put(request, response.clone())
  return response
}

/*
 * Tapping a reminder should land you in the app, not in a second copy of it.
 * An already-open window is focused where the browser allows it; otherwise one
 * is opened.
 */
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      const open = clients.find((client) => client.url.includes(self.location.origin))
      if (open && 'focus' in open) return open.focus()
      return self.clients.openWindow('/?tab=todos')
    }),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  if (request.mode === 'navigate') {
    event.respondWith(networkFirstNavigation(request))
    return
  }

  if (isImmutableAsset(url)) {
    event.respondWith(cacheFirst(request, ASSET_CACHE))
    return
  }

  if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request, ASSET_CACHE).catch(() => fetch(request)))
  }
})
