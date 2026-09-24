import { redirect } from 'react-router'
import { getToken } from '../../lib/api/session'

/** Route loader: redirects an unauthenticated visitor to sign-in (task T7). */
export function requireAuthLoader(): null {
  if (!getToken()) {
    throw redirect('/ingreso')
  }
  return null
}
