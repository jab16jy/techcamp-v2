/// <reference lib="webworker" />
import { clientsClaim } from 'workbox-core'
import { createHandlerBoundToURL, precacheAndRoute } from 'workbox-precaching'
import { NavigationRoute, registerRoute } from 'workbox-routing'
import { notificationFromPayload, readPushPayload } from './pushPayload'

declare let self: ServiceWorkerGlobalScope & { __WB_MANIFEST: Parameters<typeof precacheAndRoute>[0] }

// The shell still precaches exactly what it did under `generateSW` (T9 changed
// the strategy to `injectManifest` so this file could exist, not the caching
// behavior): the same glob patterns, plus the SPA navigation fallback that
// `generateSW` supplied by default.
precacheAndRoute(self.__WB_MANIFEST)
registerRoute(new NavigationRoute(createHandlerBoundToURL('index.html')))

// E1 chose `registerType: 'autoUpdate'`; keep that promise now that the worker
// is hand-written instead of generated.
self.skipWaiting()
clientsClaim()

// docs/06 §4: the outbox sends a push per warning/critical alert.
self.addEventListener('push', (event: PushEvent) => {
  event.waitUntil(
    (async () => {
      const view = notificationFromPayload(await readPushPayload(event.data))
      await self.registration.showNotification(view.title, {
        body: view.body,
        icon: view.icon,
        tag: view.tag,
        data: view.data,
      })
    })(),
  )
})

// D26: a tap opens or focuses the alerts tab, which is a tab inside the app
// root shell — so "open the app" and "open the alerts" are one navigation.
self.addEventListener('notificationclick', (event: NotificationEvent) => {
  event.notification.close()
  event.waitUntil(openRoute(routeOf(event.notification)))
})

function routeOf(notification: Notification): string {
  const route = (notification.data as { route?: unknown } | null)?.route
  return typeof route === 'string' && route.startsWith('/') && !route.startsWith('//')
    ? route
    : '/alertas'
}

async function openRoute(route: string): Promise<void> {
  const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  // A client already showing the route wins, so the tap never opens a second tab.
  const match = clients.find((client) => client.url.includes(route)) ?? clients[0]
  if (match) {
    await openIn(match, route)
    return
  }
  await self.clients.openWindow(route)
}

async function openIn(client: WindowClient, route: string): Promise<void> {
  await client.focus()
  if ('navigate' in client && client.url !== new URL(route, self.location.origin).href) {
    await client.navigate(route).catch(() => undefined)
  }
}
