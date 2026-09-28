import { apiClient } from '../../../lib/api/client'

/**
 * docs/04-api.md §Alertas y notificaciones: `POST /push-subscriptions` registers
 * the browser's own subscription and answers `201 { id }`. The `endpoint` is
 * UNIQUE on the server, so calling this again for a browser that already has a
 * subscription rebinds that same row instead of failing or duplicating it
 * (D15) — which is exactly why an existing subscription is re-posted rather
 * than skipped: its `keys` may have been rotated.
 *
 * T4's endpoint, unchanged.
 */
export async function registerPushSubscription(subscription: PushSubscription): Promise<string> {
  const { endpoint, keys } = subscription.toJSON()
  // A browser can hand back a subscription with no `endpoint` or no `keys`
  // (older engines, and any push service that omits them). The server requires
  // both, so failing here beats posting a record it will reject with a 422.
  if (!endpoint) {
    throw new Error('The browser returned a push subscription without an endpoint')
  }
  if (!keys?.p256dh || !keys?.auth) {
    throw new Error('The browser returned a push subscription without keys')
  }
  const { data, error } = await apiClient.POST('/api/v1/push-subscriptions', {
    body: { endpoint, keys: { p256dh: keys.p256dh, auth: keys.auth } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from POST /push-subscriptions')
  return data.id
}
