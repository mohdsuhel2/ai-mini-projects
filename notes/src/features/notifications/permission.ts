export type NotificationAccess = 'unsupported' | 'granted' | 'denied' | 'default'

/** What the browser will currently allow, without asking it anything. */
export function notificationAccess(): NotificationAccess {
  if (typeof window === 'undefined' || typeof Notification === 'undefined') return 'unsupported'
  return Notification.permission as NotificationAccess
}

/**
 * Asks for permission, and reports what came back.
 *
 * Only ever called from a control the user just operated. A permission prompt
 * on page load is the one everybody dismisses, and a dismissal is permanent.
 */
export async function requestNotificationAccess(): Promise<NotificationAccess> {
  if (notificationAccess() === 'unsupported') return 'unsupported'
  try {
    return (await Notification.requestPermission()) as NotificationAccess
  } catch {
    return 'denied'
  }
}

/**
 * Shows one reminder.
 *
 * Routed through the service worker where there is one, because a notification
 * raised by the page dies with the page — and the whole point is to reach you
 * when the app is in the background.
 */
export async function showReminder(title: string, body: string, tag: string): Promise<void> {
  if (notificationAccess() !== 'granted') return

  const options: NotificationOptions = {
    body,
    tag,
    icon: '/icon-192.png',
    badge: '/icon-192.png',
  }

  try {
    const registration = await navigator.serviceWorker?.getRegistration()
    if (registration) {
      await registration.showNotification(title, options)
      return
    }
    new Notification(title, options)
  } catch {
    // A blocked or unavailable notification is not worth breaking a render for.
  }
}
