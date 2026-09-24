import { apiClient } from '../../../lib/api/client'
import type { components } from '../../../lib/api/schema'

export type Membership = components['schemas']['MembershipView']
export type Me = components['schemas']['MeResponse']

/** docs/04-api.md §Solo perfil seminario: `POST /dev/auth/otp { phone } → 204`. */
export async function requestOtp(phone: string): Promise<void> {
  const { error } = await apiClient.POST('/api/v1/dev/auth/otp', { body: { phone } })
  if (error) throw error
}

/** docs/04-api.md §Solo perfil seminario: `POST /dev/auth/otp/verify { phone, code } → { access_token, token_type }`. */
export async function verifyOtp(phone: string, code: string): Promise<string> {
  const { data, error } = await apiClient.POST('/api/v1/dev/auth/otp/verify', { body: { phone, code } })
  if (error) throw error
  // Unreachable when there's no error: `client.ts`'s middleware throws on any
  // non-2xx response before openapi-fetch would ever return `{data: undefined}`.
  if (!data) throw new Error('empty response from /dev/auth/otp/verify')
  return data.access_token
}

/** docs/04-api.md: `GET /me → User & { memberships: Membership[] }`. */
export async function fetchMe(): Promise<Me> {
  const { data, error } = await apiClient.GET('/api/v1/me', {})
  if (error) throw error
  if (!data) throw new Error('empty response from /me')
  return data
}
