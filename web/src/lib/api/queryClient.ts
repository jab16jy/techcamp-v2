import { QueryClient } from '@tanstack/react-query'

/**
 * `retry: false`: this app's own retry UX is the explicit "Reintentar"
 * button (`refetch()`), not TanStack Query's automatic background retries —
 * two competing retry mechanisms would make failure timing non-deterministic
 * for both the user and the tests.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
  },
})
