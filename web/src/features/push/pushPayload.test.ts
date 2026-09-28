import { describe, expect, it } from 'vitest'
import {
  ALERTS_ROUTE,
  notificationFromPayload,
  readPushPayload,
  routeForNotification,
  type PushEventData,
} from './pushPayload'

/** The one method of `PushEvent['data']` the module reads. */
function pushData(json: unknown): PushEventData {
  return { json: async () => json }
}

/** A push whose payload is not JSON: `json()` rejects, as the platform does. */
function unparseablePushData(): PushEventData {
  return {
    json: async () => {
      throw new SyntaxError('Unexpected token')
    },
  }
}

describe('readPushPayload', () => {
  it('reads the documented string fields of a JSON payload', async () => {
    const payload = await readPushPayload(
      pushData({ title: 'Alerta crítica', body: 'El suelo está saturado', tag: 'plot-7' }),
    )
    expect(payload).toEqual({
      title: 'Alerta crítica',
      body: 'El suelo está saturado',
      tag: 'plot-7',
      route: undefined,
    })
  })

  it('ignores fields of the wrong type instead of trusting the sender', async () => {
    const payload = await readPushPayload(pushData({ title: 42, body: { deep: true }, tag: 'x' }))
    expect(payload).toEqual({ title: undefined, body: undefined, tag: 'x', route: undefined })
  })

  it('returns null for a payload that is not JSON, so a bare ping still notifies', async () => {
    expect(await readPushPayload(unparseablePushData())).toBeNull()
  })

  it('returns null when the push carried no data at all', async () => {
    expect(await readPushPayload(null)).toBeNull()
  })
})

describe('routeForNotification', () => {
  it('falls back to the alerts tab docs/07 puts open alerts on', () => {
    expect(routeForNotification(null)).toBe(ALERTS_ROUTE)
  })

  it('keeps a same-origin route the payload asked for', () => {
    expect(routeForNotification({ route: '/parcelas' })).toBe('/parcelas')
  })

  it('refuses an off-origin route, so a payload cannot bounce the user to another site', () => {
    expect(routeForNotification({ route: 'https://evil.example/steal' })).toBe(ALERTS_ROUTE)
    expect(routeForNotification({ route: '//evil.example/steal' })).toBe(ALERTS_ROUTE)
  })
})

describe('notificationFromPayload', () => {
  it('shows the payload copy and tags the notification so repeats replace it', () => {
    expect(
      notificationFromPayload({ title: 'Alerta crítica', body: 'El suelo está saturado', tag: 'plot-7' }),
    ).toEqual({
      title: 'Alerta crítica',
      body: 'El suelo está saturado',
      icon: '/icon-192.png',
      tag: 'plot-7',
      data: { route: ALERTS_ROUTE },
    })
  })

  it('falls back to readable copy when the payload carried none', () => {
    expect(notificationFromPayload(null)).toEqual({
      title: 'TechCamp',
      body: 'Hay una alerta nueva.',
      icon: '/icon-192.png',
      tag: 'techcamp-alert',
      data: { route: ALERTS_ROUTE },
    })
  })
})
