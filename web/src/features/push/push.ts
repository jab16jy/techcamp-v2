import { registerPushSubscription } from './api/pushApi'

/**
 * Browser half of Web Push (docs/06 §4, docs/04 §Alertas y notificaciones).
 *
 * Every outcome the UI has to tell apart is a value, not an exception: the
 * entry point renders one of five states, and a thrown `AbortError` from a
 * dismissed permission prompt is a normal outcome here, not a failure. The one
 * thing that can still throw is the POST itself, which the card reports as a
 * generic error and leaves retryable.
 */
export type EnableNotificationsResult =
  /** A subscription was just created and registered. */
  | { status: 'subscribed' }
  /** The browser already had one; it was re-registered (the server upserts). */
  | { status: 'already-active' }
  /** The farmer said no, or the browser is blocking notifications. */
  | { status: 'denied' }
  /** No service worker, `PushManager` or `Notification` in this browser. */
  | { status: 'unsupported' }
  /** Built without `VITE_VAPID_PUBLIC_KEY`; nothing can be subscribed (D25). */
  | { status: 'not-configured' }

/**
 * The VAPID public key, read at call time so a test can stub the environment.
 * docs/04's push-subscription section: it reaches the client at build time
 * through this variable and must match the server's configured key pair.
 */
function vapidPublicKey(): string {
  return import.meta.env.VITE_VAPID_PUBLIC_KEY ?? ''
}

function isPushSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

/**
 * VAPID keys travel as base64url; `subscribe()` wants raw bytes. Padding is
 * added back and the alphabet translated, because `atob` rejects `-` and `_`.
 *
 * `Uint8Array<ArrayBuffer>` (not the default `ArrayBufferLike`) because
 * `PushSubscriptionOptions` wants a non-shared buffer, and TypeScript 5.7+
 * made the buffer type generic enough to notice.
 */
export function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(base64)
  const output = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i += 1) {
    output[i] = raw.charCodeAt(i)
  }
  return output
}

/**
 * Requests notification permission, subscribes the service worker and
 * registers the subscription against the API.
 *
 * Order matters: an existing subscription is looked up FIRST, because a
 * subscription can only exist with permission already granted, and re-asking
 * would show a prompt the farmer has already answered.
 */
export async function enableNotifications(): Promise<EnableNotificationsResult> {
  if (!isPushSupported()) return { status: 'unsupported' }

  const applicationServerKey = vapidPublicKey()
  if (!applicationServerKey) return { status: 'not-configured' }

  const registration = await navigator.serviceWorker.ready
  const existing = await registration.pushManager.getSubscription()
  if (existing) {
    await registerPushSubscription(existing)
    return { status: 'already-active' }
  }

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return { status: 'denied' }

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(applicationServerKey),
  })
  await registerPushSubscription(subscription)
  return { status: 'subscribed' }
}
