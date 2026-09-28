/**
 * Push payload and routing, kept out of the service worker on purpose: this
 * module touches no worker global, so the rules that decide what a farmer
 * reads and where a tap lands are ordinary unit-testable functions instead of
 * code that can only be exercised inside a `ServiceWorkerGlobalScope`.
 *
 * T7 owns the outbox payload's real shape; until it is written these read
 * defensively and never trust a field's type (D33).
 */

/** docs/07's screen map puts open alerts on the `Alertas` bottom tab. */
export const ALERTS_ROUTE = '/alertas'

export interface PushPayload {
  title?: string
  body?: string
  /** Replaces an earlier notification of the same tag instead of stacking. */
  tag?: string
  /** Same-origin route a tap should open. Never trusted off-origin. */
  route?: string
}

/**
 * Just the part of `PushEvent['data']` this module reads. Naming it here
 * instead of importing the DOM type is what keeps this file compilable in the
 * app program (no `WebWorker` lib) and honestly unit-testable.
 */
export interface PushEventData {
  json(): Promise<unknown>
}

export interface PushNotificationView {
  title: string
  body: string
  icon: string
  tag: string
  data: { route: string }
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

/**
 * Parses whatever the push carried. Returns `null` for a missing, non-JSON or
 * non-object payload: a push is still worth showing with default copy, so this
 * never throws into the `push` handler.
 */
export async function readPushPayload(data: PushEventData | null): Promise<PushPayload | null> {
  if (!data) return null
  let parsed: unknown
  try {
    parsed = await data.json()
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object') return null
  const record = parsed as Record<string, unknown>
  return {
    title: optionalString(record.title),
    body: optionalString(record.body),
    tag: optionalString(record.tag),
    route: optionalString(record.route),
  }
}

/**
 * The route a tap opens. A payload-supplied route is honored only when it is a
 * same-origin path: a leading `//` is protocol-relative, so without this check
 * a push payload could bounce the user to another site (D33).
 */
export function routeForNotification(payload: PushPayload | null): string {
  const route = payload?.route
  if (route && route.startsWith('/') && !route.startsWith('//')) return route
  return ALERTS_ROUTE
}

/** Copy for `showNotification`, with a readable fallback when the push was bare. */
export function notificationFromPayload(payload: PushPayload | null): PushNotificationView {
  return {
    title: payload?.title || 'TechCamp',
    body: payload?.body || 'Hay una alerta nueva.',
    icon: '/icon-192.png',
    tag: payload?.tag || 'techcamp-alert',
    data: { route: routeForNotification(payload) },
  }
}
