import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { enableNotifications, type EnableNotificationsResult } from '../push'
import { NotificationsCard } from './NotificationsCard'

vi.mock('../push', () => ({ enableNotifications: vi.fn() }))

const enable = vi.mocked(enableNotifications)

function activate(): void {
  fireEvent.click(screen.getByRole('button', { name: /activar notificaciones/i }))
}

async function activateWith(result: EnableNotificationsResult) {
  enable.mockResolvedValue(result)
  render(<NotificationsCard />)
  activate()
}

describe('NotificationsCard', () => {
  beforeEach(() => {
    enable.mockReset()
  })

  it('offers one plain entry point, reusing the E1 button primitive', () => {
    render(<NotificationsCard />)
    const button = screen.getByRole('button', { name: /activar notificaciones/i })
    expect(button).toHaveAttribute('data-slot', 'button')
    expect(button).not.toBeDisabled()
  })

  it('confirms a new subscription in the copy docs/07 asks for', async () => {
    await activateWith({ status: 'subscribed' })
    expect(await screen.findByText(/vas a recibir avisos aunque la app esté cerrada/i)).toBeVisible()
  })

  it('says so when the browser was already subscribed, without claiming a new one', async () => {
    await activateWith({ status: 'already-active' })
    expect(await screen.findByText(/ya estaban activas/i)).toBeVisible()
  })

  it('points a denied user at the browser settings instead of leaving them stuck', async () => {
    await activateWith({ status: 'denied' })
    expect(await screen.findByText(/ajustes del navegador/i)).toBeVisible()
  })

  it('tells an unsupported browser the truth rather than showing a button that cannot work', async () => {
    await activateWith({ status: 'unsupported' })
    expect(await screen.findByText(/no admite notificaciones/i)).toBeVisible()
  })

  it('stays honest when the build ships no VAPID key', async () => {
    await activateWith({ status: 'not-configured' })
    expect(await screen.findByText(/no están disponibles en esta instalación/i)).toBeVisible()
  })

  it('survives a browser that throws, and leaves the entry point usable', async () => {
    enable.mockRejectedValue(new Error('boom'))
    render(<NotificationsCard />)
    activate()
    expect(await screen.findByText(/no se pudieron activar/i)).toBeVisible()
    expect(screen.getByRole('button', { name: /activar notificaciones/i })).not.toBeDisabled()
  })
})
