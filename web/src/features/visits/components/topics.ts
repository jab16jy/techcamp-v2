/**
 * Closed vocabulary of 5 extension visit topics per Ley 1876 art. 25 (docs/03:430-441).
 */
export const VISIT_TOPICS = [
  {
    id: 'human_capacities',
    label:
      'Capacidades humanas integrales: técnico-productivas, administrativas, financieras, informáticas y de comercialización',
  },
  {
    id: 'social_capacities',
    label: 'Capacidades sociales y asociatividad',
  },
  {
    id: 'information_access',
    label: 'Acceso a información, tecnologías y TIC',
  },
  {
    id: 'natural_resources',
    label:
      'Gestión sostenible de los recursos naturales: uso eficiente del agua y el suelo, adaptación al cambio climático',
  },
  {
    id: 'participation',
    label: 'Participación y autogestión',
  },
] as const

export type VisitTopicId = (typeof VISIT_TOPICS)[number]['id']

export const VALID_TOPIC_IDS: ReadonlySet<string> = new Set(
  VISIT_TOPICS.map((topic) => topic.id),
)

/**
 * Returns today's date formatted as YYYY-MM-DD in America/Bogota (invariant checklist).
 */
export function todayInBogota(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Bogota',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  const y = parts.find((p) => p.type === 'year')?.value ?? '1970'
  const m = parts.find((p) => p.type === 'month')?.value ?? '01'
  const d = parts.find((p) => p.type === 'day')?.value ?? '01'
  return `${y}-${m}-${d}`
}
