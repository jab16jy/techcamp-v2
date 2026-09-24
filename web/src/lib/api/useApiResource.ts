import { useEffect, useState } from 'react'

/**
 * Loading/error/success state for one async load. docs/05 names TanStack
 * Query as the server-state layer, but nothing in this repo installs it yet
 * and T7's own scope is "a small fetch wrapper" — a hand-rolled hook covers
 * one-shot GETs with no caching/invalidation need today. Flagged for the
 * owner: adopt TanStack Query when a later task (T8/T9) needs its cache
 * invalidation or offline persistence, and replace this hook then.
 */
export type ApiResourceState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: unknown }
  | { status: 'success'; data: T }

function sameDeps(a: readonly unknown[], b: readonly unknown[]): boolean {
  return a.length === b.length && a.every((value, index) => Object.is(value, b[index]))
}

export function useApiResource<T>(load: () => Promise<T>, deps: readonly unknown[]): ApiResourceState<T> {
  const [state, setState] = useState<ApiResourceState<T>>({ status: 'loading' })
  const [trackedDeps, setTrackedDeps] = useState(deps)

  // React's own "adjusting state when a prop changes" pattern: reset to loading
  // during render, not inside the effect below — calling setState synchronously
  // at the top of an effect body trips `react-hooks/set-state-in-effect`.
  if (!sameDeps(trackedDeps, deps)) {
    setTrackedDeps(deps)
    setState({ status: 'loading' })
  }

  useEffect(() => {
    let cancelled = false
    load()
      .then((data) => {
        if (!cancelled) setState({ status: 'success', data })
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ status: 'error', error })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps is the caller's explicit dependency list
  }, deps)

  return state
}
