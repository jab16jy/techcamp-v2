import { describe, expect, it } from 'vitest'
import { buildRoutes } from './routes'

describe('buildRoutes', () => {
  it('excludes the dev-only catalog route when none is supplied', () => {
    const routes = buildRoutes(null)

    expect(routes[0].children?.some((route) => route.path === 'dev/ui')).toBe(false)
  })

  it('nests the supplied dev-only catalog route under the app shell', () => {
    const routes = buildRoutes({ path: 'dev/ui', element: null })

    expect(routes[0].children?.some((route) => route.path === 'dev/ui')).toBe(true)
  })

  it('always includes the five tabs at the root', () => {
    const routes = buildRoutes(null)

    expect(routes[0].path).toBe('/')
    expect(routes[0].children?.length).toBe(5)
  })
})
