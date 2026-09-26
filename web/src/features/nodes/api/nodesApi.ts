import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import { useOrgId } from '../../../lib/api/session'
import type { components } from '../../../lib/api/schema'

export type NodeView = components['schemas']['NodeView']
export type NodeStatus = components['schemas']['NodeStatus']
export type NodeClaimResponse = components['schemas']['NodeClaimResponse']
export type NodeHealthView = components['schemas']['NodeHealthView']
export type SensorView = components['schemas']['SensorView']
export type CalibrationView = components['schemas']['CalibrationView']
export type CalibrationCreateRequest = components['schemas']['CalibrationCreateRequest']

/** Prefix of every nodes-list cache key, so a mutation can invalidate the whole list
 * without knowing which plot is on screen. */
const NODES_KEY = 'nodes'

/** `sensors` scoped by node, so a calibration only refetches its own node's sensors. */
function sensorsQueryKey(nodeId: string) {
  return ['sensors', nodeId] as const
}

/** docs/04-api.md:81 `GET /nodes?plot_id=&status= → Page<Node>`. `org_id` is required by
 * the server on every repository read (docs/09 §Seguridad) and is not in the doc's
 * signature, so it is read from the session here. Follows `next_cursor` until exhausted
 * so callers get the full list (feature doc decision #41; a plot has few nodes). */
async function fetchNodes(orgId: string, plotId: string | null): Promise<NodeView[]> {
  const items: NodeView[] = []
  const seenCursors = new Set<string>()
  let cursor: string | null | undefined = undefined
  do {
    const query: { org_id: string; plot_id?: string; cursor?: string } = {
      org_id: orgId,
      plot_id: plotId ?? undefined,
      cursor: cursor ?? undefined,
    }
    const res = await apiClient.GET('/api/v1/nodes', { params: { query } })
    if (res.error) throw res.error
    if (!res.data) throw new Error('empty response from /nodes')
    items.push(...res.data.items)
    cursor = res.data.next_cursor
    if (cursor) {
      if (seenCursors.has(cursor)) {
        throw new Error(`repeated cursor in /nodes: ${cursor}`)
      }
      seenCursors.add(cursor)
    }
  } while (cursor)
  return items
}

/** The org's nodes, optionally narrowed to one plot. `orgId === null` (no org chosen
 * yet) never fetches. */
export function useNodes(plotId: string | null) {
  const orgId = useOrgId()
  return useQuery({
    queryKey: [NODES_KEY, orgId, plotId],
    queryFn: () => fetchNodes(orgId as string, plotId),
    enabled: orgId !== null,
  })
}

export interface ClaimNodeInput {
  claim_code: string
  plot_id: string
}

/** docs/04-api.md:80 `POST /nodes:claim`. The response carries the MQTT credentials the
 * node needs, shown to the producer once (never re-readable), so it is returned as-is and
 * the list is invalidated — the new node must appear (T9a). */
export function useClaimNode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: ClaimNodeInput) => claimNode(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [NODES_KEY] })
    },
  })
}

async function claimNode(input: ClaimNodeInput): Promise<NodeClaimResponse> {
  const { data, error } = await apiClient.POST('/api/v1/nodes:claim', { body: input })
  if (error) throw error
  if (!data) throw new Error('empty response from POST /nodes:claim')
  return data
}

export interface PatchNodeInput {
  nodeId: string
  changes: components['schemas']['NodePatchRequest']
}

/** docs/04-api.md:82 `PATCH /nodes/{node_id} { plot_id?, status? } → Node`. Invalidates
 * the nodes list on success (T9a). */
export function usePatchNode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ nodeId, changes }: PatchNodeInput) => patchNode(nodeId, changes),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [NODES_KEY] })
    },
  })
}

async function patchNode(
  nodeId: string,
  changes: components['schemas']['NodePatchRequest'],
): Promise<NodeView> {
  const { data, error } = await apiClient.PATCH('/api/v1/nodes/{node_id}', {
    params: { path: { node_id: nodeId } },
    body: changes,
  })
  if (error) throw error
  if (!data) throw new Error('empty response from PATCH /nodes/{node_id}')
  return data
}

export interface RotateCredentialsInput {
  nodeId: string
}

/** docs/04-api.md:83 `POST /nodes/{node_id}/credentials:rotate → { password }`. The new
 * password is shown once and changes nothing in the node list, so nothing is invalidated. */
export function useRotateCredentials() {
  return useMutation({
    mutationFn: ({ nodeId }: RotateCredentialsInput) => rotateCredentials(nodeId),
  })
}

async function rotateCredentials(nodeId: string): Promise<components['schemas']['PasswordResponse']> {
  const { data, error } = await apiClient.POST('/api/v1/nodes/{node_id}/credentials:rotate', {
    params: { path: { node_id: nodeId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from POST /nodes/{node_id}/credentials:rotate')
  return data
}

/** docs/04-api.md:84 `GET /nodes/{node_id}/health → { last_seen_at, battery_v, rssi,
 * completeness_24h }` — what the producer's node card shows (T9c). */
export function useNodeHealth(nodeId: string) {
  return useQuery({
    queryKey: ['nodeHealth', nodeId],
    queryFn: () => fetchNodeHealth(nodeId),
    enabled: Boolean(nodeId),
  })
}

async function fetchNodeHealth(nodeId: string): Promise<NodeHealthView> {
  const { data, error } = await apiClient.GET('/api/v1/nodes/{node_id}/health', {
    params: { path: { node_id: nodeId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /nodes/{node_id}/health')
  return data
}

/** docs/04-api.md:85 `GET /nodes/{node_id}/sensors → Sensor[]`. */
export function useNodeSensors(nodeId: string) {
  return useQuery({
    queryKey: sensorsQueryKey(nodeId),
    queryFn: () => fetchNodeSensors(nodeId),
    enabled: Boolean(nodeId),
  })
}

async function fetchNodeSensors(nodeId: string): Promise<SensorView[]> {
  const { data, error } = await apiClient.GET('/api/v1/nodes/{node_id}/sensors', {
    params: { path: { node_id: nodeId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /nodes/{node_id}/sensors')
  return data
}

export interface CreateCalibrationInput {
  sensorId: number
  calibration: CalibrationCreateRequest
}

/** docs/04-api.md:86 `POST /sensors/{sensor_id}/calibrations` — creates a new calibration
 * version, it never edits the current one. `nodeId` is not in the path (the endpoint is
 * sensor-scoped) and only scopes the sensors cache this invalidates. */
export function useCreateCalibration(nodeId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ sensorId, calibration }: CreateCalibrationInput) =>
      createCalibration(sensorId, calibration),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: sensorsQueryKey(nodeId) })
    },
  })
}

async function createCalibration(
  sensorId: number,
  calibration: CalibrationCreateRequest,
): Promise<CalibrationView> {
  const { data, error } = await apiClient.POST('/api/v1/sensors/{sensor_id}/calibrations', {
    params: { path: { sensor_id: sensorId } },
    body: calibration,
  })
  if (error) throw error
  if (!data) throw new Error('empty response from POST /sensors/{sensor_id}/calibrations')
  return data
}
