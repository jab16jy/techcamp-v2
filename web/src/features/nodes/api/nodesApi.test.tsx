import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import {
  useClaimNode,
  useCreateCalibration,
  useNodeHealth,
  useNodeSensors,
  useNodes,
  usePatchNode,
  useRotateCredentials,
} from './nodesApi'

const NODE = {
  id: 'node-1',
  org_id: 'org-1',
  plot_id: 'plot-1',
  transport: 'wifi',
  dev_eui: '70B3D5A3B0000001',
  firmware: '1.0.0',
  interval_s: 300,
  claimed_at: '2026-09-01T12:00:00Z',
  last_seen_at: '2026-09-25T10:00:00Z',
  status: 'online',
}

const SENSOR = {
  id: 1,
  node_id: 'node-1',
  channel_key: 'soil_moisture_20cm',
  metric: 'soil_moisture',
  depth_cm: 20,
  unit: '%',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function nodePage(nodes: unknown[]) {
  return { items: nodes, next_cursor: null }
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

function wrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('nodesApi', () => {
  beforeEach(() => {
    setSession('token-abc', 'org-1')
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it("lists the org's nodes for a plot", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(nodePage([NODE])))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(() => useNodes('plot-1'), { wrapper: wrapper(queryClient) })

    await waitFor(() => expect(result.current.data).toHaveLength(1))
    expect(result.current.data?.[0]?.id).toBe('node-1')
    // The server scopes the list by org (docs/09 security): `org_id` must ride along
    // with the plot filter, otherwise the request is rejected.
    const url = requestUrl(vi.mocked(fetch).mock.calls[0][0] as Request)
    expect(url).toContain('/api/v1/nodes?')
    expect(url).toContain('org_id=org-1')
    expect(url).toContain('plot_id=plot-1')
  })

  it('follows next_cursor until exhausted and returns all items across pages', async () => {
    const node2 = { ...NODE, id: 'node-2' }
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ items: [NODE], next_cursor: 'cursor-2' }))
      .mockResolvedValueOnce(jsonResponse({ items: [node2], next_cursor: null }))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(() => useNodes('plot-1'), { wrapper: wrapper(queryClient) })

    await waitFor(() => expect(result.current.data).toHaveLength(2))
    expect(result.current.data?.map((n) => n.id)).toEqual(['node-1', 'node-2'])
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2)
    const firstUrl = requestUrl(vi.mocked(fetch).mock.calls[0][0] as Request)
    const secondUrl = requestUrl(vi.mocked(fetch).mock.calls[1][0] as Request)
    expect(firstUrl).not.toContain('cursor=')
    expect(secondUrl).toContain('cursor=cursor-2')
  })

  it('stops following next_cursor and fails if a cursor repeats', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ items: [NODE], next_cursor: 'loop-cursor' }))
      .mockResolvedValueOnce(jsonResponse({ items: [NODE], next_cursor: 'loop-cursor' }))
      .mockResolvedValueOnce(jsonResponse({ items: [NODE], next_cursor: null }))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(() => useNodes('plot-1'), { wrapper: wrapper(queryClient) })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error?.message).toContain('repeated cursor in /nodes: loop-cursor')
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2)
  })

  it('does not fetch nodes when no organization is active (orgId === null)', async () => {
    clearSession()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(() => useNodes('plot-1'), { wrapper: wrapper(queryClient) })

    expect(result.current.fetchStatus).toBe('idle')
    expect(result.current.data).toBeUndefined()
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
  })

  it('does not fetch node health when nodeId is empty', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useNodeHealth(''), { wrapper: wrapper(queryClient) })
    expect(result.current.fetchStatus).toBe('idle')
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
  })

  it('does not fetch node sensors when nodeId is empty', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useNodeSensors(''), { wrapper: wrapper(queryClient) })
    expect(result.current.fetchStatus).toBe('idle')
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
  })

  it('refetches the nodes list after a successful claim', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(nodePage([])))
      .mockResolvedValueOnce(
        jsonResponse(
          { ...NODE, mqtt: { username: 'node-1', password: 'mqtt-secret' } },
          201,
        ),
      )
      .mockResolvedValueOnce(jsonResponse(nodePage([NODE])))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(
      () => ({ nodes: useNodes('plot-1'), claim: useClaimNode() }),
      { wrapper: wrapper(queryClient) },
    )

    await waitFor(() => expect(result.current.nodes.data).toHaveLength(0))

    await act(async () => {
      await result.current.claim.mutateAsync({ claim_code: 'ABC-123', plot_id: 'plot-1' })
    })

    await waitFor(() => expect(result.current.nodes.data).toHaveLength(1))
    // 1 initial GET + 1 POST + 1 refetch GET: proves invalidation triggered a real
    // refetch, not just the mutation's own response being reused.
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3)
  })

  it('refetches the nodes list after a successful patch', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(nodePage([NODE])))
      .mockResolvedValueOnce(jsonResponse({ ...NODE, status: 'retired' }))
      .mockResolvedValueOnce(jsonResponse(nodePage([{ ...NODE, status: 'retired' }])))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(
      () => ({ nodes: useNodes('plot-1'), patch: usePatchNode() }),
      { wrapper: wrapper(queryClient) },
    )

    await waitFor(() => expect(result.current.nodes.data).toHaveLength(1))

    await act(async () => {
      await result.current.patch.mutateAsync({ nodeId: 'node-1', changes: { status: 'retired' } })
    })

    await waitFor(() => expect(result.current.nodes.data?.[0]?.status).toBe('retired'))
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3)
  })

  it("returns the rotated node's new password", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ password: 'rotated-secret' }))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(() => useRotateCredentials(), { wrapper: wrapper(queryClient) })

    await act(async () => {
      await result.current.mutateAsync({ nodeId: 'node-1' })
    })

    await waitFor(() => expect(result.current.data?.password).toBe('rotated-secret'))
    // The password is shown once and changes nothing in the node list: no invalidation.
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
  })

  it("reads a node's health", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        last_seen_at: '2026-09-25T10:00:00Z',
        battery_v: 3.9,
        rssi: -78,
        // docs/04-api.md:84: a 0–1 ratio, passed through untouched.
        completeness_24h: 0.198,
      }),
    )
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(() => useNodeHealth('node-1'), { wrapper: wrapper(queryClient) })

    await waitFor(() => expect(result.current.data?.completeness_24h).toBe(0.198))
    expect(result.current.data?.battery_v).toBe(3.9)
    expect(requestUrl(vi.mocked(fetch).mock.calls[0][0] as Request)).toContain(
      '/api/v1/nodes/node-1/health',
    )
  })

  it("reads a node's sensors", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([SENSOR]))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(() => useNodeSensors('node-1'), { wrapper: wrapper(queryClient) })

    await waitFor(() => expect(result.current.data).toHaveLength(1))
    expect(result.current.data?.[0]?.channel_key).toBe('soil_moisture_20cm')
    expect(requestUrl(vi.mocked(fetch).mock.calls[0][0] as Request)).toContain(
      '/api/v1/nodes/node-1/sensors',
    )
  })

  it("refetches a node's sensors after a new calibration version", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            id: 'cal-1',
            sensor_id: 1,
            version: 1,
            method: 'linear',
            kind: 'lab',
            params: { offset: 0.2 },
            rmse_pct: 1.5,
            valid_from: '2026-09-25T00:00:00Z',
          },
          201,
        ),
      )
      .mockResolvedValueOnce(jsonResponse([SENSOR]))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { result } = renderHook(
      () => ({ sensors: useNodeSensors('node-1'), calibrate: useCreateCalibration('node-1') }),
      { wrapper: wrapper(queryClient) },
    )

    await waitFor(() => expect(result.current.sensors.data).toHaveLength(0))

    await act(async () => {
      await result.current.calibrate.mutateAsync({
        sensorId: 1,
        calibration: {
          method: 'linear',
          kind: 'lab',
          params: { offset: 0.2 },
          rmse_pct: 1.5,
          valid_from: '2026-09-25T00:00:00Z',
        },
      })
    })

    await waitFor(() => expect(result.current.sensors.data).toHaveLength(1))
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3)
  })
})
