import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearSession, setSession } from '../../../lib/api/session'
import { ClaimNodeSheet } from './ClaimNodeSheet'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function requestOf(input: Request | string | URL): Request {
  return input as Request
}

const CLAIMED_NODE = {
  id: 'node-1',
  org_id: 'org-1',
  plot_id: 'plot-1',
  transport: 'wifi',
  dev_eui: '70B3D5A3B0000001',
  firmware: null,
  interval_s: 300,
  claimed_at: '2026-09-25T12:00:00Z',
  last_seen_at: null,
  status: 'provisioned',
  mqtt: { username: 'node-1', password: 'mqtt-secret' },
}

function renderSheet(onOpenChange: (open: boolean) => void = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ClaimNodeSheet open onOpenChange={onOpenChange} plotId="plot-1" />
    </QueryClientProvider>,
  )
  return { onOpenChange, rerender: (open: boolean) => view.rerender(
    <QueryClientProvider client={queryClient}>
      <ClaimNodeSheet open={open} onOpenChange={onOpenChange} plotId="plot-1" />
    </QueryClientProvider>,
  ) }
}

function mockFetch(byUrl: Record<string, () => Response>) {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = requestOf(input as Request)
    const match = Object.entries(byUrl).find(([substr]) => request.url.includes(substr))
    if (!match) throw new Error(`unexpected request: ${request.url}`)
    return match[1]()
  })
}

/** Fills the code field and submits, as a technician typing on the keypad would. */
async function claimWithCode(code: string) {
  fireEvent.change(screen.getByLabelText(/Código del nodo/), { target: { value: code } })
  fireEvent.click(screen.getByRole('button', { name: 'Vincular nodo' }))
}

function stubCameraStream() {
  const stop = vi.fn()
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }) },
  })
  Object.defineProperty(HTMLMediaElement.prototype, 'play', {
    configurable: true,
    value: vi.fn().mockResolvedValue(undefined),
  })
  return stop
}

class FakeBarcodeDetector {
  static formats: string[] = []
  detect = vi.fn(async () => [{ rawValue: '  QR-ABC-123  ' }])
  constructor(options: { formats: string[] }) {
    FakeBarcodeDetector.formats = options.formats
  }
}

describe('ClaimNodeSheet', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setSession('token-abc', 'org-1')
  })

  afterEach(() => {
    clearSession()
    vi.unstubAllGlobals()
  })

  it('claims a node from a typed code and shows the MQTT credentials once', async () => {
    mockFetch({ 'nodes:claim': () => jsonResponse(CLAIMED_NODE, 201) })
    renderSheet()

    await claimWithCode('ABC-123')

    expect(await screen.findByText('mqtt-secret')).toBeInTheDocument()
    expect(screen.getByText('node-1')).toBeInTheDocument()
    expect(screen.getByText(/no se volverá a mostrar/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Código del nodo/)).not.toBeInTheDocument()

    const [input] = vi.mocked(fetch).mock.calls[0]
    expect(await requestOf(input as Request).json()).toEqual({
      claim_code: 'ABC-123',
      plot_id: 'plot-1',
    })
  })

  it('copies the password to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    mockFetch({ 'nodes:claim': () => jsonResponse(CLAIMED_NODE, 201) })
    renderSheet()

    await claimWithCode('ABC-123')
    await screen.findByText('mqtt-secret')
    fireEvent.click(screen.getByRole('button', { name: 'Copiar contraseña' }))

    expect(writeText).toHaveBeenCalledWith('mqtt-secret')
    expect(await screen.findByRole('button', { name: /Contraseña copiada/ })).toBeInTheDocument()
  })

  it('shows dedicated copy when the claim code is not found', async () => {
    mockFetch({
      'nodes:claim': () =>
        jsonResponse({ type: 'about:blank', title: 'Node not found', status: 404 }, 404),
    })
    renderSheet()

    await claimWithCode('NOPE')

    expect(await screen.findByText('Código no encontrado')).toBeInTheDocument()
  })

  it('shows dedicated copy when the node is already claimed', async () => {
    mockFetch({
      'nodes:claim': () =>
        jsonResponse({ type: 'about:blank', title: 'Node already claimed', status: 409 }, 409),
    })
    renderSheet()

    await claimWithCode('ABC-123')

    expect(await screen.findByText('Este nodo ya está vinculado')).toBeInTheDocument()
  })

  it('shows general error copy for any other failure', async () => {
    mockFetch({
      'nodes:claim': () => jsonResponse({ type: 'about:blank', title: 'Server error', status: 500 }, 500),
    })
    renderSheet()

    await claimWithCode('ABC-123')

    expect(await screen.findByText('Ocurrió un error. Intenta de nuevo.')).toBeInTheDocument()
  })

  it('keeps the typed code after a failed claim so it can be corrected', async () => {
    mockFetch({
      'nodes:claim': () =>
        jsonResponse({ type: 'about:blank', title: 'Node not found', status: 404 }, 404),
    })
    renderSheet()

    await claimWithCode('NOPE')

    await screen.findByText('Código no encontrado')
    expect(screen.getByLabelText(/Código del nodo/)).toHaveValue('NOPE')
  })

  it('hides the scan action when the browser has no native barcode detector', () => {
    mockFetch({ 'nodes:claim': () => jsonResponse(CLAIMED_NODE, 201) })
    renderSheet()

    expect(screen.queryByRole('button', { name: /Escanear/ })).not.toBeInTheDocument()
  })

  it('scans a QR code with the native detector and stops the camera stream', async () => {
    const stopTrack = stubCameraStream()
    vi.stubGlobal('BarcodeDetector', FakeBarcodeDetector)
    mockFetch({ 'nodes:claim': () => jsonResponse(CLAIMED_NODE, 201) })
    renderSheet()

    fireEvent.click(screen.getByRole('button', { name: 'Escanear QR' }))

    await waitFor(() =>
      expect(screen.getByLabelText(/Código del nodo/)).toHaveValue('QR-ABC-123'),
    )
    expect(FakeBarcodeDetector.formats).toEqual(['qr_code'])
    expect(stopTrack).toHaveBeenCalled()
  })

  it('shows permission copy when the camera cannot be opened', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockRejectedValue(new Error('NotAllowedError')) },
    })
    vi.stubGlobal('BarcodeDetector', FakeBarcodeDetector)
    mockFetch({ 'nodes:claim': () => jsonResponse(CLAIMED_NODE, 201) })
    renderSheet()

    fireEvent.click(screen.getByRole('button', { name: 'Escanear QR' }))

    expect(
      await screen.findByText('No se pudo abrir la cámara. Revisa los permisos del navegador.'),
    ).toBeInTheDocument()
  })

  it('stops the camera stream when the sheet is closed', async () => {
    const stopTrack = stubCameraStream()
    // A detector that never finds a code, so the scan is still running when the sheet closes.
    class SilentDetector {
      detect = vi.fn(async () => [] as { rawValue: string }[])
    }
    vi.stubGlobal('BarcodeDetector', SilentDetector)
    mockFetch({ 'nodes:claim': () => jsonResponse(CLAIMED_NODE, 201) })
    const { rerender } = renderSheet()

    fireEvent.click(screen.getByRole('button', { name: 'Escanear QR' }))
    await screen.findByText(/Apunta la cámara/)
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    rerender(false)

    expect(stopTrack).toHaveBeenCalled()
  })

  it('discards the credentials when the sheet is closed and reopened', async () => {
    mockFetch({ 'nodes:claim': () => jsonResponse(CLAIMED_NODE, 201) })
    const { rerender } = renderSheet()

    await claimWithCode('ABC-123')
    await screen.findByText('mqtt-secret')
    fireEvent.click(screen.getByRole('button', { name: 'Listo' }))
    rerender(false)
    rerender(true)

    expect(screen.queryByText('mqtt-secret')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Código del nodo/)).toHaveValue('')
  })
})
