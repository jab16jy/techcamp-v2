import 'fake-indexeddb/auto'
import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

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
    await act(async () => {
      await router.navigate('/')
    })

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Inicio' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Alertas/ })).toBeInTheDocument()
  })
})
