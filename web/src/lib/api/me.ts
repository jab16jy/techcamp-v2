import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './schema'
import { ME_KEY, getToken, useOrgId, useToken } from './session'

export type Membership = components['schemas']['MembershipView']
export type Me = components['schemas']['MeResponse']

export type Role = 'owner' | 'technician' | 'producer' | 'viewer'
export const KNOWN_ROLES: ReadonlySet<string> = new Set<Role>([
  'owner',
  'technician',
  'producer',
  'viewer',
])

export interface CachedMeSeed {
  token: string
  user_id: string
  memberships: Membership[]
  stored_at: number
}

export function readMeSeed(token: string | null): CachedMeSeed | null {
  if (!token) return null
  try {
    const raw = localStorage.getItem(ME_KEY)
    if (!raw) return null
    const seed = JSON.parse(raw) as Partial<CachedMeSeed>
    if (seed.token !== token || !seed.user_id || !Array.isArray(seed.memberships)) {
      return null
    }
    return seed as CachedMeSeed
  } catch {
    return null
  }
}

export function saveMeSeed(token: string, me: Me): void {
  try {
    const seed: CachedMeSeed = {
      token,
      user_id: me.id,
      memberships: me.memberships,
      stored_at: Date.now(),
    }
    localStorage.setItem(ME_KEY, JSON.stringify(seed))
  } catch {
    /* private browsing / quota */
  }
}

/** docs/04-api.md: `GET /me → User & { memberships: Membership[] }`. */
export async function fetchMe(): Promise<Me> {
  const { data, error } = await apiClient.GET('/api/v1/me', {})
  if (error) throw error
  if (!data) throw new Error('empty response from /me')
  const token = getToken()
  if (token) {
    saveMeSeed(token, data)
  }
  return data
}

/**
 * Returns current signed-in user and memberships, with localStorage fallback for offline use.
 * Keyed by the active session token so switching users never reads the prior user.
 * Initial data reflects when it was stored (initialDataUpdatedAt) so stale seeds refetch.
 */
export function useMe() {
  const token = useToken()
  const seed = token ? readMeSeed(token) : null

  return useQuery({
    queryKey: ['me', token],
    queryFn: fetchMe,
    initialData: seed
      ? {
          id: seed.user_id,
          phone: '',
          email: null,
          full_name: null,
          memberships: seed.memberships,
        }
      : undefined,
    initialDataUpdatedAt: seed?.stored_at,
    enabled: token !== null,
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * Role of the current user in the active org (D3, D14).
 * Returns null when signed out, when the org has no membership, or when the role is unknown.
 */
export function useActiveOrgRole(): Role | null {
  const orgId = useOrgId()
  const token = useToken()
  const { data: me } = useMe()
  if (!token || !orgId || !me || !me.memberships) return null
  const membership = me.memberships.find((m) => m.org_id === orgId)
  if (!membership || !KNOWN_ROLES.has(membership.role)) return null
  return membership.role as Role
}
