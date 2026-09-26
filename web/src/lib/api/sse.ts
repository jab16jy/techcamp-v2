export interface SseEvent {
  id?: string
  event: string
  data: string
}

export interface SseParser {
  /** Feeds a decoded text chunk and returns every event completed by it. */
  push: (chunk: string) => SseEvent[]
}

function parseBlock(
  block: string,
  lastEventId: string | undefined,
): { event: SseEvent | null; lastEventId: string | undefined } {
  let id = lastEventId
  let event = 'message'
  const data: string[] = []

  for (const line of block.split('\n')) {
    if (line === '' || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'id') {
      id = value === '' ? undefined : value
    } else if (field === 'event') {
      event = value
    } else if (field === 'data') {
      data.push(value)
    }
  }

  // A block with no `data` carries nothing: that is the server's `:keepalive`
  // (docs/04-api.md:187) and must not reach the consumer.
  if (data.length === 0) return { event: null, lastEventId: id }
  const sseEvent: SseEvent = { event, data: data.join('\n') }
  if (id !== undefined) sseEvent.id = id
  return { event: sseEvent, lastEventId: id }
}

/**
 * Incremental SSE parser (the WHATWG framing, not a dependency): text chunks in,
 * whole events out. Chunk boundaries can fall anywhere, CRLF and LF both work, and
 * `data:` lines are joined with newlines as the spec requires.
 */
export function createSseParser(): SseParser {
  let buffer = ''
  let lastEventId: string | undefined

  return {
    push(chunk: string): SseEvent[] {
      buffer = (buffer + chunk).replace(/\r\n/g, '\n')
      const events: SseEvent[] = []
      let end = buffer.indexOf('\n\n')
      while (end !== -1) {
        const block = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        const parsed = parseBlock(block, lastEventId)
        lastEventId = parsed.lastEventId
        if (parsed.event) events.push(parsed.event)
        end = buffer.indexOf('\n\n')
      }
      return events
    },
  }
}
