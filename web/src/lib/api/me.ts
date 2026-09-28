import { useQuery } from '@tanstack/react-query'
import { apiClient } from './client'
import type { components } from './schema'
import { useOrgId, getToken } from './session'

export type Membership = components['schemas']['MembershipView']
export type Me = components['schemas']['MeResponse']

const ME_STORAGE_KEY = 'techcamp.me'

/** docs/04-api.md: `GET /me → User & { memberships: Membership[] }`. */
export async function fetchMe(): Promise<Me> {
  const { data, error } = await apiClient.GET('/api/v1/me', {})
  if (error) throw error
  if (!data) throw new Error('empty response from /me')
  try {
    localStorage.setItem(ME_STORAGE_KEY, JSON.stringify(data))
  } catch {
    /* private browsing / quota */
  }
  return data
}

/**
 * Returns current signed-in user and memberships, with localStorage fallback for offline use.
 */
export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
    initialData: () => {
      try {
        const raw = localStorage.getItem(ME_STORAGE_KEY)
        return raw ? (JSON.parse(raw) as Me) : undefined
      } catch {
        return undefined
      }
    },
    enabled: getToken() !== null,
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * Role of the current user in the active org (D3, D14).
 */
export function useActiveOrgRole(): string | null {
  const orgId = useOrgId()
  const { data: me } = useMe()
  if (!orgId || !me) return null
  return me.memberships.find((m) => m.org_id === orgId)?.role ?? null
}
