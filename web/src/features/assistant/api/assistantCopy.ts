/**
 * The words the assistant screen speaks, in one place.
 *
 * Labels and notices come from docs/07-frontend-design-system.md §Asistente: the
 * conversation screen has to let a producer with low digital literacy tell the four
 * states apart "cada uno con su texto y no con un código" — never `429`, never
 * `stream: 503`, never an English error name.
 *
 * The register is the voseo the mandated copy itself uses ("Mañana podés seguir
 * preguntando", docs/07 §Asistente → Límite alcanzado), so every line addressed to
 * the producer speaks in the same second person. Spanish only here: identifiers
 * stay English (AGENTS.md).
 */
export const ASSISTANT_COPY = {
  composerLabel: 'Escribí tu pregunta',
  askPlaceholder: 'Preguntá sobre tu parcela',
  sendLabel: 'Preguntar',
  answering: 'Respondiendo…',
  sourcesTitle: 'Fuentes',
  noSources: 'Sin fuentes citadas.',
  /**
   * T6 seam, recorded rather than worked around: docs/07 §Asistente asks for the
   * "preguntas usadas" beside "Mañana podés seguir preguntando", and the client
   * deliberately never reads the body of a `429` (D-T7.1: the outcome is decided
   * from the status alone). The count becomes showable only if T6's `429`
   * problem+json carries it; this lane does not invent a number, so the notice
   * quotes the doc's own line verbatim and shows nothing else.
   */
  limitedTitle: 'Límite alcanzado',
  limitedBody: 'Mañana podés seguir preguntando.',
  offlineTitle: 'Sin conexión',
  offlineBody: 'El asistente necesita conexión para responder.',
  /**
   * Neutral on purpose, and the same line `lib/api/errorCopy.ts:15` uses for a
   * failure that is not a network one: the offline state above owns the connection
   * words, so a failed answer never claims the connection is the cause. A `503`
   * from `get_llm_client` in the production profile is a provider-config gap on
   * the server, and docs/07 gives it no state of its own to speak from.
   */
  failedTitle: 'No se pudo responder',
  failedBody: 'Ocurrió un error. Intenta de nuevo.',
  degradedNote:
    'Esta respuesta se armó con los datos de tu parcela y los documentos del cultivo. El detalle no está disponible: vuelve a preguntar mañana.',
} as const
