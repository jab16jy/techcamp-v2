import { useEffect, useRef, useState } from 'react'
import { FormSheet } from '../../../design-system/patterns/FormSheet'
import { Button } from '../../../design-system/ui/button'
import { Input } from '../../../design-system/ui/input'
import { ApiError } from '../../../lib/api/client'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useClaimNode, type NodeClaimResponse } from '../api/nodesApi'
import { OneTimeSecret } from '../components/OneTimeSecret'

type MqttCredentials = NodeClaimResponse['mqtt']

/** `BarcodeDetector` is not in lib.dom yet, so only the shape this screen uses is declared
 * here — no QR library for a handful of lines of native API. */
interface BarcodeDetectorLike {
  detect(source: HTMLVideoElement): Promise<{ rawValue: string }[]>
}
type BarcodeDetectorFactory = new (options: { formats: string[] }) => BarcodeDetectorLike

/** The native detector, or `undefined` where the browser has none. Feature detection, not
 * user-agent sniffing: typed entry is always there, so a phone without the API can still
 * claim a node. */
function barcodeDetector(): BarcodeDetectorFactory | undefined {
  if (!('BarcodeDetector' in window)) return undefined
  return (window as unknown as { BarcodeDetector: BarcodeDetectorFactory }).BarcodeDetector
}

/** docs/04-api.md:79-80 `POST /nodes:claim`: an unknown code and an already-claimed node are
 * the two failures a technician can actually fix, so they get their own words; everything
 * else goes to the shared copy (same shape as `describeActionError`, without its 422/409
 * passthrough, because those two statuses are spoken for here). */
function describeClaimError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return 'Código no encontrado'
    if (err.status === 409) return 'Este nodo ya está vinculado'
  }
  return describeApiError(err)
}

/** First QR raw value in the current frame, trimmed. A frame the detector cannot read yet
 * (the video is still starting) is a miss, not a failure. docs are silent on the payload
 * format, so the raw value is taken as the claim code. */
async function readQr(
  detector: BarcodeDetectorLike,
  video: HTMLVideoElement,
): Promise<string | null> {
  try {
    const codes = await detector.detect(video)
    const raw = codes[0]?.rawValue?.trim()
    return raw ? raw : null
  } catch {
    return null
  }
}

export interface ClaimNodeSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  plotId: string
}

/**
 * docs/07:128 `nodes --> claim[Alta por QR + calibración]`: claims a node onto this plot
 * and shows the MQTT credentials the node needs. docs/04-api.md:79 — the password is shown
 * once, so it lives in this component's state and nowhere else: no query cache, no storage,
 * discarded when the sheet closes.
 */
export function ClaimNodeSheet({ open, onOpenChange, plotId }: ClaimNodeSheetProps) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [credentials, setCredentials] = useState<MqttCredentials | null>(null)
  const [scanning, setScanning] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  // Bumped on every stop; the scan loop compares it to know it was cancelled.
  const scanTokenRef = useRef(0)
  const claim = useClaimNode()

  // The camera must not outlive the screen, even on an unmount mid-scan.
  useEffect(
    () => () => {
      scanTokenRef.current += 1
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    },
    [],
  )

  function stopScan() {
    scanTokenRef.current += 1
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setScanning(false)
  }

  // Every way out of this sheet (cancel, escape, backdrop, close, "Listo") goes through
  // Radix's `onOpenChange`, and closing the plot sheet unmounts this component outright —
  // so forgetting the credentials and the camera here covers all of them.
  function handleOpenChange(next: boolean) {
    if (!next) {
      stopScan()
      setCode('')
      setError(null)
      setCredentials(null)
    }
    onOpenChange(next)
  }

  async function startScan() {
    setError(null)
    // Invariant: stopScan bumps scanTokenRef, which cancels any in-flight scan loop.
    stopScan()
    const token = scanTokenRef.current
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' },
      })
    } catch {
      if (token === scanTokenRef.current) {
        setError('No se pudo abrir la cámara. Revisa los permisos del navegador.')
      }
      return
    }
    const video = videoRef.current
    const Detector = barcodeDetector()
    if (token !== scanTokenRef.current || !video || !Detector) {
      stream.getTracks().forEach((track) => track.stop())
      return
    }
    const detector = new Detector({ formats: ['qr_code'] })
    streamRef.current = stream
    setScanning(true)
    video.srcObject = stream
    void video.play().catch(() => undefined)

    for (;;) {
      const found = await readQr(detector, video)
      if (token !== scanTokenRef.current) return
      if (found) {
        setCode(found)
        stopScan()
        return
      }
      await new Promise((resolve) => setTimeout(resolve, 300))
    }
  }

  async function handleSubmit() {
    setError(null)
    try {
      const claimed = await claim.mutateAsync({ claim_code: code.trim(), plot_id: plotId })
      setCredentials(claimed.mqtt)
      stopScan()
    } catch (err) {
      setError(describeClaimError(err))
    }
  }


  const detector = barcodeDetector()

  return (
    <FormSheet
      open={open}
      onOpenChange={handleOpenChange}
      title={credentials ? 'Nodo vinculado' : 'Agregar nodo'}
      description={credentials ? undefined : 'Escanea el QR del nodo o escribe su código.'}
      submitLabel={credentials ? 'Listo' : 'Vincular nodo'}
      onSubmit={credentials ? () => handleOpenChange(false) : handleSubmit}
      submitDisabled={credentials ? false : code.trim() === ''}
      submitLoading={claim.isPending}
    >
      {credentials ? (
        <OneTimeSecret
          fields={[
            { label: 'Usuario', value: credentials.username },
            { label: 'Contraseña', value: credentials.password },
          ]}
        />
      ) : (
        <>
          <label className="flex flex-col gap-2 text-base" htmlFor="claim-code">
            Código del nodo
            <Input
              id="claim-code"
              value={code}
              autoComplete="off"
              onChange={(event) => setCode(event.target.value)}
            />
          </label>
          {detector && (
            <div className="flex flex-col gap-2">
              <div>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={scanning ? stopScan : startScan}
                >
                  {scanning ? 'Detener escaneo' : 'Escanear QR'}
                </Button>
              </div>
              <video
                ref={videoRef}
                className={scanning ? 'aspect-video w-full rounded-md bg-text/10' : 'hidden'}
                muted
                playsInline
              />
              {scanning && (
                <p className="text-sm text-text-muted">Apunta la cámara al QR del nodo.</p>
              )}
            </div>
          )}
        </>
      )}
      {error && <p className="text-base text-severity-critical">{error}</p>}
    </FormSheet>
  )
}
