import { useState, type ReactNode } from 'react'
import { Button } from '../design-system/ui/button'
import { Input } from '../design-system/ui/input'
import { Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogClose } from '../design-system/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../design-system/ui/tabs'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '../design-system/ui/select'
import { Toaster, toast } from '../design-system/ui/toast'
import { StatusBand } from '../design-system/components/StatusBand'
import { StatusBadge } from '../design-system/components/StatusBadge'
import { MetricTile } from '../design-system/components/MetricTile'
import { AlertCard } from '../design-system/components/AlertCard'
import { WaterGauge } from '../design-system/components/WaterGauge'
import { SyncIndicator } from '../design-system/components/SyncIndicator'
import { FormSheet } from '../design-system/patterns/FormSheet'
import { EmptyState } from '../design-system/patterns/EmptyState'
import { OfflineBanner } from '../design-system/patterns/OfflineBanner'
import { ListWithFilters } from '../design-system/patterns/ListWithFilters'
import type { StatusState } from '../design-system/components/status'
import type { Severity } from '../design-system/components/severity'

const STATUSES: { status: StatusState; message: string }[] = [
  { status: 'ok', message: 'Hoy no necesita riego.' },
  { status: 'watch', message: 'Vigile la humedad, riego probable mañana.' },
  { status: 'irrigate', message: 'Hoy: regar 12 mm ≈ 40 min.' },
  { status: 'stress', message: 'Al cultivo le faltan 80 mm: está en estrés.' },
]

const SEVERITIES: { severity: Severity; title: string; description: string }[] = [
  { severity: 'info', title: 'Nodo recalibrado', description: 'El sensor de humedad se recalibró correctamente.' },
  { severity: 'warning', title: 'Batería baja', description: 'El nodo de la parcela Norte tiene 15 % de batería.' },
  { severity: 'critical', title: 'Sin lecturas hace 3 días', description: 'Revise la conexión del nodo de la parcela Sur.' },
]

interface DemoPlot {
  id: string
  name: string
  status: StatusState
}

const DEMO_PLOTS: DemoPlot[] = [
  { id: '1', name: 'Parcela Norte · Maíz', status: 'irrigate' },
  { id: '2', name: 'Parcela Sur · Yuca', status: 'ok' },
  { id: '3', name: 'Parcela Este · Frijol', status: 'watch' },
]

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-6 px-4 pt-8">
      <h2 className="font-serif text-xl">{title}</h2>
      <div className="mt-3 flex flex-col gap-3">{children}</div>
    </section>
  )
}

const SECTIONS = [
  { id: 'bandas', label: 'Bandas de estado' },
  { id: 'insignias', label: 'Insignias de estado' },
  { id: 'metricas', label: 'Métricas' },
  { id: 'alertas', label: 'Alertas' },
  { id: 'agua', label: 'Nivel de agua' },
  { id: 'sincronizacion', label: 'Sincronización' },
  { id: 'primitivos', label: 'Primitivos' },
  { id: 'patrones', label: 'Patrones' },
]

/** Dev-only catalog: every design-system component in all its states (docs/07). */
export default function DevUiCatalog() {
  const [sheetOpen, setSheetOpen] = useState(false)
  const [activeFilter, setActiveFilter] = useState('todas')
  const filteredPlots = activeFilter === 'todas' ? DEMO_PLOTS : DEMO_PLOTS.filter((p) => p.status === activeFilter)

  return (
    <div className="pb-12">
      <Toaster />
      <header className="px-4 pt-6">
        <h1 className="font-serif text-2xl">Sistema de diseño</h1>
        <SyncIndicator online={false} pendingCount={3} lastDataMinutesAgo={12} className="mt-1" />
        <p className="mt-1 text-sm text-text-muted">Contenido de ejemplo: datos sintéticos, ninguno es real.</p>
      </header>

      <nav aria-label="Secciones del catálogo" className="mt-4 mx-4 divide-y divide-text/10 overflow-hidden rounded-lg border border-text/10 bg-surface-raised">
        {SECTIONS.map((section) => (
          <a key={section.id} href={`#${section.id}`} className="block min-h-12 px-4 py-3 text-base">
            {section.label}
          </a>
        ))}
      </nav>

      <Section id="bandas" title="Bandas de estado">
        {STATUSES.map(({ status, message }) => (
          <StatusBand key={status} status={status} message={message} />
        ))}
      </Section>

      <Section id="insignias" title="Insignias de estado">
        <div className="flex flex-wrap gap-2">
          {STATUSES.map(({ status }) => (
            <StatusBadge key={status} status={status} />
          ))}
        </div>
      </Section>

      <Section id="metricas" title="Métricas">
        <div className="grid grid-cols-2 gap-3">
          <MetricTile label="Humedad de suelo" value={62} unit="%" lastDataMinutesAgo={12} />
          <MetricTile label="Temperatura" value={28.5} unit="°C" lastDataMinutesAgo={0} />
          <MetricTile label="Lluvia (7 días)" value={14} unit="mm" lastDataMinutesAgo={1} />
          <MetricTile label="Último riego" value="—" lastDataMinutesAgo={null} />
        </div>
      </Section>

      <Section id="alertas" title="Alertas">
        {SEVERITIES.map(({ severity, title, description }) => (
          <AlertCard key={severity} severity={severity} title={title} description={description} timestampLabel="hace 2 h" />
        ))}
      </Section>

      <Section id="agua" title="Nivel de agua">
        <WaterGauge percentage={20} label="Humedad de suelo · seco" valueLabel="20 % de capacidad" />
        <WaterGauge percentage={62} label="Humedad de suelo · adecuado" valueLabel="62 % de capacidad" />
        <WaterGauge percentage={95} label="Humedad de suelo · saturado" valueLabel="95 % de capacidad" />
      </Section>

      <Section id="sincronizacion" title="Sincronización">
        <SyncIndicator online lastDataMinutesAgo={2} pendingCount={0} />
        <SyncIndicator online pendingCount={1} lastDataMinutesAgo={null} />
        <SyncIndicator online={false} pendingCount={3} lastDataMinutesAgo={12} />
      </Section>

      <Section id="primitivos" title="Primitivos">
        <div className="flex flex-wrap gap-2">
          <Button variant="primary">Primario</Button>
          <Button variant="secondary">Secundario</Button>
          <Button variant="ghost">Fantasma</Button>
          <Button variant="primary" disabled>
            Deshabilitado
          </Button>
          <Button variant="primary" loading>
            Cargando
          </Button>
        </div>

        <Input placeholder="Nombre del cultivo" />
        <Input placeholder="Campo deshabilitado" disabled />
        <Input placeholder="Campo con error" aria-invalid defaultValue="valor inválido" />

        <Select defaultValue="maiz">
          <SelectTrigger className="max-w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="maiz">Maíz</SelectItem>
            <SelectItem value="yuca">Yuca</SelectItem>
            <SelectItem value="frijol">Frijol</SelectItem>
          </SelectContent>
        </Select>

        <Tabs defaultValue="hoy">
          <TabsList>
            <TabsTrigger value="hoy">Hoy</TabsTrigger>
            <TabsTrigger value="semana">Semana</TabsTrigger>
          </TabsList>
          <TabsContent value="hoy">Contenido de hoy.</TabsContent>
          <TabsContent value="semana">Contenido de la semana.</TabsContent>
        </Tabs>

        <div className="flex flex-wrap gap-2">
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="secondary">Abrir diálogo</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Confirmar acción</DialogTitle>
                <DialogDescription>Este es un diálogo de ejemplo.</DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="ghost">Cancelar</Button>
                </DialogClose>
                <DialogClose asChild>
                  <Button variant="primary">Confirmar</Button>
                </DialogClose>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <Button variant="secondary" onClick={() => toast('Entrada guardada en la bitácora.')}>
            Mostrar notificación
          </Button>
        </div>
      </Section>

      <Section id="patrones" title="Patrones">
        <Button variant="secondary" onClick={() => setSheetOpen(true)}>
          Abrir hoja de formulario
        </Button>
        <FormSheet
          open={sheetOpen}
          onOpenChange={setSheetOpen}
          title="Nueva entrada"
          description="Registre una labor en la bitácora."
          submitLabel="Guardar"
          onSubmit={() => setSheetOpen(false)}
        >
          <Input placeholder="Descripción de la labor" />
        </FormSheet>

        <OfflineBanner online lastDataMinutesAgo={3} pendingCount={0} />
        <OfflineBanner online={false} lastDataMinutesAgo={12} pendingCount={3} />

        <EmptyState
          title="Sin alertas abiertas"
          description="Cuando un nodo o el clima requieran atención, aparecerán aquí."
        />

        <ListWithFilters
          filters={[
            { key: 'todas', label: 'Todas' },
            { key: 'ok', label: 'Bien' },
            { key: 'watch', label: 'Vigilar' },
            { key: 'irrigate', label: 'Regar' },
          ]}
          activeFilter={activeFilter}
          onFilterChange={setActiveFilter}
          items={filteredPlots}
          getKey={(plot) => plot.id}
          renderItem={(plot) => (
            <div className="flex min-h-12 items-center justify-between gap-2 px-4 py-3">
              <span>{plot.name}</span>
              <StatusBadge status={plot.status} />
            </div>
          )}
          emptyState={<EmptyState title="Sin parcelas en este filtro" />}
        />
      </Section>
    </div>
  )
}
