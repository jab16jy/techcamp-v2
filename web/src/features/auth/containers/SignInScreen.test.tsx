import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, getOrgId, getToken } from '../../../lib/api/session'
import { orgLabel, SignInScreen } from './SignInScreen'

function renderSignIn() {
  const router = createMemoryRouter(
    [
      { path: '/ingreso', Component: SignInScreen },
      { path: '/', element: <p>Home</p> },
    ],
    { initialEntries: ['/ingreso'] },
  )
  render(<RouterProvider router={router} />)
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestUrl(input: Request | string | URL): string {
  return input instanceof Request ? input.url : String(input)
}

describe('orgLabel', () => {
  it('shows a shortened org id and the Spanish role label', () => {
    expect(orgLabel({ org_id: '3f9a2c1b-aaaa-bbbb-cccc-000000000000', role: 'owner' })).toBe(
      'Organización 3f9a2c1b · Propietario',
    )
  })

  it('falls back to the raw role when it is not a known one', () => {
    expect(orgLabel({ org_id: '3f9a2c1b-aaaa-bbbb-cccc-000000000000', role: 'auditor' })).toBe(
      'Organización 3f9a2c1b · auditor',
    )
  })
})

describe('SignInScreen', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('requests an OTP and moves to the code step', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }))
    renderSignIn()

    fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '3001234567' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar código' }))

    await waitFor(() => expect(screen.getByLabelText('Código')).toBeInTheDocument())
    const [request] = vi.mocked(fetch).mock.calls[0]
    expect(requestUrl(request as Request)).toContain('/api/v1/dev/auth/otp')
    expect((request as Request).method).toBe('POST')
  })

  it('verifies the code, stores the session and navigates home for a single membership', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/otp/verify')) {
        return jsonResponse({ access_token: 'token-abc', token_type: 'bearer' })
      }
      if (url.includes('/dev/auth/otp')) {
        return new Response(null, { status: 204 })
      }
      if (url.includes('/me')) {
        return jsonResponse({
          id: 'u1',
          phone: '3001234567',
          email: null,
          full_name: null,
          memberships: [{ org_id: 'org-1', role: 'owner' }],
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    renderSignIn()

    fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '3001234567' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar código' }))
    await waitFor(() => expect(screen.getByLabelText('Código')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Código'), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))

    await waitFor(() => expect(screen.getByText('Home')).toBeInTheDocument())
    expect(getToken()).toBe('token-abc')
    expect(getOrgId()).toBe('org-1')
  })

  it('shows an error and does not navigate when the code is invalid', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/otp/verify')) {
        return jsonResponse(
          { type: 'about:blank', title: 'Invalid or expired code', status: 401 },
          401,
        )
      }
      return new Response(null, { status: 204 })
    })
    renderSignIn()

    fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '3001234567' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar código' }))
    await waitFor(() => expect(screen.getByLabelText('Código')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Código'), { target: { value: '000000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('El código es incorrecto o venció.'),
    )
    expect(screen.queryByText('Home')).not.toBeInTheDocument()
    expect(getToken()).toBeNull()
  })

  it('shows an org picker when the user has more than one membership', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/otp/verify')) {
        return jsonResponse({ access_token: 'token-abc', token_type: 'bearer' })
      }
      if (url.includes('/dev/auth/otp')) {
        return new Response(null, { status: 204 })
      }
      if (url.includes('/me')) {
        return jsonResponse({
          id: 'u1',
          phone: '3001234567',
          email: null,
          full_name: null,
          memberships: [
            { org_id: 'org-1', role: 'owner' },
            { org_id: 'org-2', role: 'technician' },
          ],
        })
      }
      throw new Error(`unexpected request: ${url}`)
    })
    renderSignIn()

    fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '3001234567' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar código' }))
    await waitFor(() => expect(screen.getByLabelText('Código')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Código'), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))

    await waitFor(() => expect(screen.getByText('Elige tu organización.')).toBeInTheDocument())
    expect(screen.getByRole('combobox', { name: 'Organización' })).toBeInTheDocument()
    expect(getOrgId()).toBeNull()
  })

  it('clears the error and the code when changing the phone number', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = requestUrl(input as Request)
      if (url.includes('/otp/verify')) {
        return jsonResponse({ type: 'about:blank', title: 'Invalid or expired code', status: 401 }, 401)
      }
      return new Response(null, { status: 204 })
    })
    renderSignIn()

    fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '3001234567' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar código' }))
    await waitFor(() => expect(screen.getByLabelText('Código')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Código'), { target: { value: '000000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'Cambiar número' }))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '3001234567' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar código' }))
    await waitFor(() => expect(screen.getByLabelText('Código')).toBeInTheDocument())
    expect(screen.getByLabelText('Código')).toHaveValue('')
  })
})
