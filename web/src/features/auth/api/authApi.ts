import { apiFetch } from '../../../lib/api/client'

/** docs/04-api.md §Solo perfil seminario: `POST /dev/auth/otp { phone } → 204`. */
export function requestOtp(phone: string): Promise<void> {
  return apiFetch<void>('/dev/auth/otp', { method: 'POST', body: { phone } })
}

interface TokenResponse {
  access_token: string
  token_type: string
}

/** `POST /dev/auth/otp/verify` (server/src/techcamp/identity/adapters/api/dev_auth.py; not yet in docs/04-api.md). */
export async function verifyOtp(phone: string, code: string): Promise<string> {
  const response = await apiFetch<TokenResponse>('/dev/auth/otp/verify', {
    method: 'POST',
    body: { phone, code },
  })
  return response.access_token
}

export interface Membership {
  org_id: string
  role: string
}

export interface Me {
  id: string
  phone: string
  email: string | null
  full_name: string | null
  memberships: Membership[]
}

/** docs/04-api.md: `GET /me → User & { memberships: Membership[] }`. */
export function fetchMe(): Promise<Me> {
  return apiFetch<Me>('/me')
}
