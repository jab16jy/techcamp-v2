import 'fake-indexeddb/auto'
import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { router } from './app/router'
import { clearSession, setSession } from './lib/api/session'

describe('App', () => {
  afterEach(() => {
    clearSession()
  })

  it('redirects an unauthenticated visitor to sign-in', async () => {
    clearSession()
    await act(async () => {
      await router.navigate('/')
    })

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Ingresar' })).toBeInTheDocument()
  })

  it('renders the home tab and the bottom tab bar once signed in', async () => {
    setSession('token-abc', 'org-1')
    // The home tab loads the org's farms since E9 T5, so it needs a server to
    // answer. A fresh Response per call: one shared instance has its body read
    // once and every later request fails as a network error.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        async () =>
          new Response(JSON.stringify({ items: [], next_cursor: null }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    )
    await act(async () => {
      await router.navigate('/')
    })

    render(<App />)

    expect(await screen.findByText('Sin parcelas')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Alertas/ })).toBeInTheDocument()
    // Negative: Inicio is the real screen now, not the old placeholder.
    expect(screen.queryByRole('heading', { name: 'Inicio' })).not.toBeInTheDocument()
    vi.unstubAllGlobals()
  })
})
