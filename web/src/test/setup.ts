import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Vitest's `globals` option is off (tests import `describe`/`it`/`expect` explicitly),
// so Testing Library's own auto-cleanup registration — which looks for a global
// `afterEach` — never fires. Register it explicitly or DOM leaks across tests
// in the same file.
afterEach(() => {
  cleanup()
})
