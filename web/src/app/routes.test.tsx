import { describe, expect, it } from 'vitest'
import { buildRoutes } from './routes'

describe('buildRoutes', () => {
  it('excludes the dev-only catalog route when none is supplied', () => {
    const routes = buildRoutes(null)

    expect(routes.some((route) => route.path === '/dev/ui')).toBe(false)
  })

  it('includes the supplied dev-only catalog route', () => {
    const routes = buildRoutes({ path: '/dev/ui', element: null })

    expect(routes.some((route) => route.path === '/dev/ui')).toBe(true)
  })

  it('always includes the tabbed app shell at the root', () => {
    const routes = buildRoutes(null)

    expect(routes[0].path).toBe('/')
    expect(routes[0].children?.length).toBe(5)
  })
})
