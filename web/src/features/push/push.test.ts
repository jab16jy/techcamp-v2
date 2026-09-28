import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableNotifications, urlBase64ToUint8Array } from './push'
import { registerPushSubscription } from './api/pushApi'

vi.mock('./api/pushApi', () => ({ registerPushSubscription: vi.fn() }))

const VAPID = 'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U'
const ENDPOINT = 'https://push.example.com/sub/abc'

const register = vi.mocked(registerPushSubscription)

function fakeSubscription(endpoint = ENDPOINT) {
  const json = { endpoint, expirationTime: null, keys: { p256dh: 'p256dh-value', auth: 'auth-value' } }
  const unsubscribe = vi.fn().mockResolvedValue(true)
  return {
    endpoint,
    keys: json.keys,
    unsubscribe,
    toJSON: () => json,
  } as unknown as PushSubscription & { unsubscribe: ReturnType<typeof vi.fn> }
}

const VAPID_STORAGE_KEY = 'techcamp.push.vapid-key'

function rememberVapidKey(value: string) {
  window.localStorage.setItem(VAPID_STORAGE_KEY, value)
}

interface BrowserOptions {
  supported?: boolean
  permission?: NotificationPermission
  existing?: PushSubscription | null
  created?: PushSubscription
}

function stubBrowser({ supported = true, permission = 'granted', existing = null, created }: BrowserOptions) {
  const pushManager = {
    getSubscription: vi.fn().mockResolvedValue(existing),
    subscribe: vi.fn().mockResolvedValue(created ?? fakeSubscription()),
  }
  const registration = { pushManager }
  if (supported) {
    Object.defineProperty(navigator, 'serviceWorker', {
      value: { ready: Promise.resolve(registration) },
      configurable: true,
      writable: true,
    })
    vi.stubGlobal('PushManager', class {})
    vi.stubGlobal('Notification', { requestPermission: vi.fn().mockResolvedValue(permission) })
  } else {
    Reflect.deleteProperty(navigator, 'serviceWorker')
    vi.stubGlobal('PushManager', undefined)
    vi.stubGlobal('Notification', undefined)
  }
  return { pushManager }
}

describe('urlBase64ToUint8Array', () => {
  it('decodes base64url into the byte array the Push API expects', () => {
    const bytes = urlBase64ToUint8Array('AQAB')
    expect(Array.from(bytes)).toEqual([1, 0, 1])
  })

  it('uses the URL-safe alphabet, so a real 65-byte VAPID key decodes', () => {
    // A VAPID P-256 public key is 65 raw bytes = 87 base64url chars, whose
    // length is not a multiple of 4, so the padding path is the one that runs.
    const bytes = urlBase64ToUint8Array(VAPID)
    expect(bytes).toBeInstanceOf(Uint8Array)
    expect(bytes.length).toBe(65)
  })
})

describe('enableNotifications', () => {
  beforeEach(() => {
    register.mockReset()
    register.mockResolvedValue('sub-1')
    vi.stubEnv('VITE_VAPID_PUBLIC_KEY', VAPID)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
    vi.restoreAllMocks()
    window.localStorage.clear()
    Reflect.deleteProperty(navigator, 'serviceWorker')
  })

  it('subscribes and registers the new subscription against the API (docs/04)', async () => {
    const created = fakeSubscription()
    const { pushManager } = stubBrowser({ created })

    const result = await enableNotifications()

    expect(result).toEqual({ status: 'subscribed' })
    expect(pushManager.subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID),
    })
    expect(register).toHaveBeenCalledWith(created)
  })

  it('reuses an existing subscription instead of subscribing again, and re-registers it', async () => {
    const existing = fakeSubscription()
    rememberVapidKey(VAPID)
    const { pushManager } = stubBrowser({ existing })

    const result = await enableNotifications()

    expect(result).toEqual({ status: 'already-active' })
    expect(pushManager.subscribe).not.toHaveBeenCalled()
    expect(register).toHaveBeenCalledWith(existing)
  })

  it('never prompts for permission when a subscription already exists', async () => {
    rememberVapidKey(VAPID)
    stubBrowser({ existing: fakeSubscription() })
    await enableNotifications()
    expect(Notification.requestPermission).not.toHaveBeenCalled()
  })

  it('replaces a subscription this build cannot prove was created with its VAPID key', async () => {
    // The platform never exposes a subscription's applicationServerKey, so a
    // key that was rotated after the subscription was made is undetectable from
    // the subscription alone. Without a remembered key, the only safe reading is
    // that the subscription may be stale.
    const existing = fakeSubscription()
    const { pushManager } = stubBrowser({ existing })

    const result = await enableNotifications()

    expect(result).toEqual({ status: 'subscribed' })
    expect(existing.unsubscribe).toHaveBeenCalledOnce()
    expect(pushManager.subscribe).toHaveBeenCalledOnce()
    expect(register).toHaveBeenCalledTimes(1)
  })

  it('replaces a subscription whose remembered key is not this build’s key', async () => {
    rememberVapidKey('a-previous-rotation-of-the-key')
    const existing = fakeSubscription()
    const { pushManager } = stubBrowser({ existing })

    const result = await enableNotifications()

    expect(result).toEqual({ status: 'subscribed' })
    expect(existing.unsubscribe).toHaveBeenCalledOnce()
    expect(pushManager.subscribe).toHaveBeenCalledOnce()
  })

  it('remembers the key it subscribed with, so the next tap is the fast path', async () => {
    stubBrowser({})
    await enableNotifications()
    expect(window.localStorage.getItem(VAPID_STORAGE_KEY)).toBe(VAPID)
  })

  it('still replaces a stale subscription when permission must not be re-asked', async () => {
    rememberVapidKey('rotated-away')
    stubBrowser({ existing: fakeSubscription() })
    await enableNotifications()
    expect(Notification.requestPermission).not.toHaveBeenCalled()
  })

  it('re-registers without churning when storage is unavailable', async () => {
    // Private browsing can make localStorage throw. Resubscribing on every tap
    // would be worse than trusting the subscription, so storage failure falls
    // back to the old behavior. jsdom's Storage is a Proxy, so the property
    // itself is replaced rather than a method on its prototype.
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage')
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: () => {
          throw new Error('storage disabled')
        },
        setItem: () => {
          throw new Error('storage disabled')
        },
        clear: () => undefined,
      },
    })
    try {
      const existing = fakeSubscription()
      const { pushManager } = stubBrowser({ existing })

      const result = await enableNotifications()

      expect(result).toEqual({ status: 'already-active' })
      expect(existing.unsubscribe).not.toHaveBeenCalled()
      expect(pushManager.subscribe).not.toHaveBeenCalled()
      expect(register).toHaveBeenCalledWith(existing)
    } finally {
      if (original) Object.defineProperty(window, 'localStorage', original)
    }
  })

  it('reports a denied permission and never subscribes', async () => {
    const { pushManager } = stubBrowser({ permission: 'denied' })

    const result = await enableNotifications()

    expect(result).toEqual({ status: 'denied' })
    expect(pushManager.subscribe).not.toHaveBeenCalled()
    expect(register).not.toHaveBeenCalled()
  })

  it('reports an unsupported browser before touching the permission prompt', async () => {
    stubBrowser({ supported: false })

    const result = await enableNotifications()

    expect(result).toEqual({ status: 'unsupported' })
    expect(register).not.toHaveBeenCalled()
  })

  it('reports a build with no VAPID key as not configured, and never prompts', async () => {
    vi.stubEnv('VITE_VAPID_PUBLIC_KEY', '')
    stubBrowser({})

    const result = await enableNotifications()

    expect(result).toEqual({ status: 'not-configured' })
    expect(Notification.requestPermission).not.toHaveBeenCalled()
    expect(register).not.toHaveBeenCalled()
  })

  it('waits for the service worker registration to be ready before subscribing', async () => {
    const { pushManager } = stubBrowser({})
    let releaseReady = () => {}
    const ready = new Promise<ServiceWorkerRegistration>((resolve) => {
      releaseReady = () => resolve({ pushManager } as unknown as ServiceWorkerRegistration)
    })
    Object.defineProperty(navigator, 'serviceWorker', {
      value: { ready },
      configurable: true,
      writable: true,
    })

    const pending = enableNotifications()
    await Promise.resolve()
    expect(pushManager.subscribe).not.toHaveBeenCalled()

    releaseReady()
    expect(await pending).toEqual({ status: 'subscribed' })
    expect(pushManager.subscribe).toHaveBeenCalledOnce()
  })
})
