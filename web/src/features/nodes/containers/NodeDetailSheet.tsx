import { useState } from 'react'
import { FormSheet } from '../../../design-system/patterns/FormSheet'
import { Button } from '../../../design-system/ui/button'
import { describeApiError } from '../../../lib/api/errorCopy'
import {
  useNodeSensors,
  useRotateCredentials,
  type CalibrationView,
  type NodeView,
  type SensorView,
} from '../api/nodesApi'
import { METHOD_LABELS, KIND_LABELS } from '../components/calibrationLabels'
import { OneTimeSecret } from '../components/OneTimeSecret'
import { CalibrationSheet } from './CalibrationSheet'

export interface NodeDetailSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  node: NodeView
}

/** docs/04-api.md:85-88: a node's sensors, their calibration versions, and its MQTT
 * credentials. The rotated password is shown once, like the claim one. */
export function NodeDetailSheet({ open, onOpenChange, node }: NodeDetailSheetProps) {
  const sensorsQuery = useNodeSensors(node.id)
  const rotate = useRotateCredentials()
  const [calibrating, setCalibrating] = useState<SensorView | null>(null)
  const [calibrations, setCalibrations] = useState<Record<number, CalibrationView>>({})
  const [password, setPassword] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleOpenChange(next: boolean) {
    if (!next) {
      setCalibrating(null)
      setPassword(null)
      setConfirming(false)
      setError(null)
    }
    onOpenChange(next)
  }

  async function handleRotate() {
    setError(null)
    try {
      const rotated = await rotate.mutateAsync({ nodeId: node.id })
      setPassword(rotated.password)
      setConfirming(false)
    } catch (err) {
      setError(describeApiError(err))
      setConfirming(false)
    }
  }

  return (
    <>
      <FormSheet
        open={open}
        onOpenChange={handleOpenChange}
        title={`Nodo ${node.dev_eui ?? node.id}`}
        description="Sensores, calibraciones y credenciales."
        submitLabel="Listo"
        onSubmit={() => handleOpenChange(false)}
      >
        {sensorsQuery.isLoading && (
          <p className="text-base text-text-muted">Cargando sensores…</p>
        )}
        {sensorsQuery.isError && (
          <p className="text-base text-severity-critical">{describeApiError(sensorsQuery.error)}</p>
        )}
        {sensorsQuery.isSuccess && sensorsQuery.data.length === 0 && (
          <p className="text-base text-text-muted">Este nodo todavía no tiene sensores.</p>
        )}
        {sensorsQuery.isSuccess &&
          sensorsQuery.data.map((sensor) => {
            const calibration = calibrations[sensor.id]
            return (
              <article key={sensor.id} className="flex flex-col gap-1">
                <p className="text-base font-semibold">{sensor.channel_key}</p>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-base">
                  <dt className="text-text-muted">Métrica</dt>
                  <dd>{sensor.metric}</dd>
                  <dt className="text-text-muted">Profundidad</dt>
                  <dd>{sensor.depth_cm === null ? '—' : `${sensor.depth_cm} cm`}</dd>
                  <dt className="text-text-muted">Unidad</dt>
                  <dd>{sensor.unit}</dd>
                  <dt className="text-text-muted">Calibración</dt>
                  <dd>
                    {calibration
                      ? `${METHOD_LABELS[calibration.method]} · ${KIND_LABELS[calibration.kind]} · ${calibration.valid_from}`
                      : 'Sin calibración en esta sesión.'}
                  </dd>
                </dl>
                <div>
                  <Button type="button" variant="secondary" onClick={() => setCalibrating(sensor)}>
                    Calibrar
                  </Button>
                </div>
              </article>
            )
          })}

        {password ? (
          <OneTimeSecret fields={[{ label: 'Contraseña', value: password }]} />
        ) : confirming ? (
          <div className="flex flex-col gap-2">
            <p className="text-base">
              ¿Rotar credenciales? La contraseña anterior dejará de funcionar y tendrás que
              escribir la nueva en el nodo.
            </p>
            <div className="flex gap-2">
              <Button type="button" variant="ghost" onClick={() => setConfirming(false)}>
                Volver
              </Button>
              <Button type="button" variant="primary" onClick={handleRotate} loading={rotate.isPending}>
                Sí, rotar
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Button type="button" variant="secondary" onClick={() => setConfirming(true)}>
              Rotar credenciales
            </Button>
          </div>
        )}
        {error && <p className="text-base text-severity-critical">{error}</p>}
      </FormSheet>

      {calibrating && (
        <CalibrationSheet
          open
          onOpenChange={(next) => {
            if (!next) setCalibrating(null)
          }}
          nodeId={node.id}
          sensor={calibrating}
          onCreated={(created) => setCalibrations((prev) => ({ ...prev, [created.sensor_id]: created }))}
        />
      )}
    </>
  )
}
