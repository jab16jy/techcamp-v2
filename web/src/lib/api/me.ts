import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './schema'
import { ME_KEY, useOrgId, useToken } from './session'

export type Membership = components['schemas']['MembershipView']
export type Me = components['schemas']['MeResponse']

export type Role = 'owner' | 'technician' | 'producer' | 'viewer'
export const KNOWN_ROLES: ReadonlySet<string> = new Set<Role>([
  'owner',
  'technician',
  'producer',
  'viewer',
])

/** docs/04-api.md: `GET /me → User & { memberships: Membership[] }`. */
export async function fetchMe(): Promise<Me> {
  const { data, error } = await apiClient.GET('/api/v1/me', {})
  if (error) throw error
  if (!data) throw new Error('empty response from /me')
  try {
    localStorage.setItem(ME_KEY, JSON.stringify(data))
  } catch {
    /* private browsing / quota */
  }
  return data
}

/**
 * Returns current signed-in user and memberships, with localStorage fallback for offline use.
 * Keyed by the active session token so switching users never reads the prior user.
 */
export function useMe() {
  const token = useToken()
  return useQuery({
    queryKey: ['me', token],
    queryFn: fetchMe,
    initialData: () => {
      if (!token) return undefined
      try {
        const raw = localStorage.getItem(ME_KEY)
        return raw ? (JSON.parse(raw) as Me) : undefined
      } catch {
        return undefined
      }
    },
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
