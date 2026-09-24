import { useSyncExternalStore } from 'react'

/**
 * Client-side session storage: bearer token + selected org id.
 *
 * ADR-0014 and docs/05-arquitectura.md are silent on *where* the PWA keeps
 * the token (they only cover issuance/validation), so this is T7's own
 * decision (owner fallback instruction): `localStorage`, so a refresh keeps
 * the session, following the existing pattern of small `lib/` modules that
 * cross features (docs/07 §Arquitectura del frontend: "lo compartido baja a
 * design-system/ o lib/").
 */
const TOKEN_KEY = 'techcamp.token'
const ORG_KEY = 'techcamp.org_id'

type Listener = () => void
const listeners = new Set<Listener>()

function emit(): void {
  for (const listener of listeners) listener()
}

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    // Private browsing / storage disabled: session doesn't persist, but the
    // app still works for the current load.
    return null
  }
}

function writeStorage(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* see readStorage */
  }
}

function removeStorage(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    /* see readStorage */
  }
}

export function getToken(): string | null {
  return readStorage(TOKEN_KEY)
}

export function getOrgId(): string | null {
  return readStorage(ORG_KEY)
}

/** Sets the token after a successful OTP verify; `orgId` when already known (a single membership). */
export function setSession(token: string, orgId: string | null): void {
  writeStorage(TOKEN_KEY, token)
  if (orgId) writeStorage(ORG_KEY, orgId)
  emit()
}

/** Sets the active org once the user picks one from several memberships. */
export function setOrgId(orgId: string): void {
  writeStorage(ORG_KEY, orgId)
  emit()
}

export function clearSession(): void {
  removeStorage(TOKEN_KEY)
  removeStorage(ORG_KEY)
  emit()
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useToken(): string | null {
  return useSyncExternalStore(subscribe, getToken, () => null)
}

export function useOrgId(): string | null {
  return useSyncExternalStore(subscribe, getOrgId, () => null)
}
