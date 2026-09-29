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

// jsdom doesn't implement `Element.scrollIntoView` (Radix's `Select` calls it
// when it highlights the selected/candidate item on open); stubbed so a
// Select whose options load asynchronously (T9: the crop catalog) doesn't
// crash with `scrollIntoView is not a function`.
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {})

// jsdom implements neither `URL.createObjectURL` nor its revoke, and Vitest's
// own replacement assumes a Node `Blob` (it reads `_buffer`), which throws on a
// jsdom one — so a component that previews a Blob (the photo thumbnails)
// cannot run there without this. The stubs hand out one stable fake URL per
// call, which is also what lets a test assert that a URL was revoked.
let objectUrlCounter = 0
URL.createObjectURL = (): string => {
  objectUrlCounter += 1
  return `blob:test/${objectUrlCounter}`
}
URL.revokeObjectURL = (): void => {}
