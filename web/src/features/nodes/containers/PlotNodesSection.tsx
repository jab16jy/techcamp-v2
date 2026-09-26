import { useState } from 'react'
import { formatFreshness, formatPercent, minutesSince } from '../../../design-system/components/format'
import { EmptyState } from '../../../design-system/patterns/EmptyState'
import { Button } from '../../../design-system/ui/button'
import { describeApiError } from '../../../lib/api/errorCopy'
import { useNodeHealth, useNodes, type NodeView } from '../api/nodesApi'
import { ClaimNodeSheet } from './ClaimNodeSheet'
import { NodeDetailSheet } from './NodeDetailSheet'

/** `techcamp.telemetry.domain.models.NodeStatus` (server, T3), one plain word per value:
 * never color alone (docs/07). `StatusBadge` carries the plot water-balance states, not
 * node states, so the label travels with the row text instead of a new badge. */
const NODE_STATUS_LABELS: Record<NodeView['status'], string> = {
  provisioned: 'Sin vincular',
  online: 'En línea',
  offline: 'Sin señal',
  retired: 'Retirado',
}

/** A number with its unit, or a bare dash when the node has not reported it: no dangling
 * unit on a missing value. */
function withUnit(value: number | null | undefined, unit: string): string {
  return value === null || value === undefined ? '—' : `${value} ${unit}`
}

function NodeRow({ node, onOpen }: { node: NodeView; onOpen: () => void }) {
  const healthQuery = useNodeHealth(node.id)
  const health = healthQuery.data

  return (
    <article className="flex flex-col gap-1">
      <button
        type="button"
        className="self-start text-left text-base font-semibold underline"
        onClick={onOpen}
      >
        {node.dev_eui ?? node.id}
      </button>
      {healthQuery.isError ? (
        <p className="text-base text-severity-critical">{describeApiError(healthQuery.error)}</p>
      ) : (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-base">
          <dt className="text-text-muted">Estado</dt>
          <dd>{NODE_STATUS_LABELS[node.status]}</dd>
          <dt className="text-text-muted">Última conexión</dt>
          <dd>{health?.last_seen_at ? formatFreshness(minutesSince(health.last_seen_at)) : '—'}</dd>
          <dt className="text-text-muted">Batería</dt>
          <dd>{withUnit(health?.battery_v, 'V')}</dd>
          <dt className="text-text-muted">RSSI</dt>
          <dd>{withUnit(health?.rssi, 'dBm')}</dd>
          <dt className="text-text-muted">Completitud 24 h</dt>
          <dd>{formatPercent(health?.completeness_24h)}</dd>
        </dl>
      )}
    </article>
  )
}

/**
 * "Nodos de la parcela" (docs/07 mapa de pantallas: `plotd --> nodes[…]`), rendered as the
 * third section of the plot detail sheet next to soil and cycle: the list is short and the
 * plot is already on screen, so a second stacked sheet would only hide its own context.
 */
export function PlotNodesSection({ plotId }: { plotId: string }) {
  const [claiming, setClaiming] = useState(false)
  const [detail, setDetail] = useState<NodeView | null>(null)
  const nodesQuery = useNodes(plotId)

  return (
    <>
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Nodos</h2>
          <Button type="button" variant="secondary" onClick={() => setClaiming(true)}>
            Agregar nodo
          </Button>
        </div>
        {nodesQuery.isLoading && <p className="text-base text-text-muted">Cargando nodos…</p>}
        {nodesQuery.isError && (
          <div className="flex flex-col gap-2">
            <p className="text-base text-severity-critical">{describeApiError(nodesQuery.error)}</p>
            <div>
              <Button type="button" variant="secondary" onClick={() => nodesQuery.refetch()}>
                Reintentar
              </Button>
            </div>
          </div>
        )}
        {nodesQuery.isSuccess && nodesQuery.data.length === 0 && (
          <EmptyState
            title="Sin nodos"
            description="Esta parcela todavía no tiene nodos vinculados."
          />
        )}
        {nodesQuery.isSuccess && nodesQuery.data.length > 0 && (
          <div className="flex flex-col gap-4">
            {nodesQuery.data.map((node) => (
              <NodeRow key={node.id} node={node} onOpen={() => setDetail(node)} />
            ))}
          </div>
        )}
      </section>
      <ClaimNodeSheet open={claiming} onOpenChange={setClaiming} plotId={plotId} />
      {detail && (
        <NodeDetailSheet open onOpenChange={(next) => !next && setDetail(null)} node={detail} />
      )}
    </>
  )
}
