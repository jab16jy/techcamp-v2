import { describe, expect, it } from 'vitest'
import { createSseParser } from './sse'

const READING_FRAME = 'id: 7\nevent: reading\ndata: {"plot_id":"plot-1","value":42.5}\n\n'

describe('createSseParser', () => {
  it('parses id, event name and data', () => {
    expect(createSseParser().push(READING_FRAME)).toEqual([
      { id: '7', event: 'reading', data: '{"plot_id":"plot-1","value":42.5}' },
    ])
  })

  it('waits for the blank line when a frame arrives in two chunks', () => {
    const parser = createSseParser()

    expect(parser.push('id: 7\nevent: reading\ndata: {"value":')).toEqual([])
    expect(parser.push('42.5}\n')).toEqual([])
    expect(parser.push('\n')).toEqual([
      { id: '7', event: 'reading', data: '{"value":42.5}' },
    ])
  })

  it('parses several frames from one chunk', () => {
    const chunk = `${READING_FRAME}:keepalive\n\nid: 8\nevent: node.status\ndata: {"node_id":"node-1"}\n\n`

    expect(createSseParser().push(chunk)).toEqual([
      { id: '7', event: 'reading', data: '{"plot_id":"plot-1","value":42.5}' },
      { id: '8', event: 'node.status', data: '{"node_id":"node-1"}' },
    ])
  })

  it('ignores a keepalive comment (docs/04:187, one colon, no data)', () => {
    expect(createSseParser().push(':keepalive\n\n')).toEqual([])
  })

  it('joins multi-line data with a newline, stripping one space per value', () => {
    // The second value starts with two spaces, so one survives: the spec removes exactly one.
    const chunk = 'event: alert.opened\ndata: {"title":"Sequía",\ndata:  "level":"critical"}\n\n'

    expect(createSseParser().push(chunk)).toEqual([
      { event: 'alert.opened', data: '{"title":"Sequía",\n "level":"critical"}' },
    ])
  })

  it('handles CRLF line endings', () => {
    const chunk = 'id: 3\r\nevent: reading\r\ndata: {"value":1}\r\n\r\n'

    expect(createSseParser().push(chunk)).toEqual([
      { id: '3', event: 'reading', data: '{"value":1}' },
    ])
  })

  it('splits a CRLF frame across chunks without losing the boundary', () => {
    const parser = createSseParser()

    expect(parser.push('id: 3\r\nevent: reading\r\ndata: {"value":1}\r')).toEqual([])
    expect(parser.push('\n\r\n')).toEqual([{ id: '3', event: 'reading', data: '{"value":1}' }])
  })

  it('defaults the event name to message and keeps a data-less block out', () => {
    expect(createSseParser().push('data: bare\n\n')).toEqual([
      { event: 'message', data: 'bare' },
    ])
    expect(createSseParser().push('event: ping\n\n')).toEqual([])
  })

  it('retains the last event id when a subsequent frame omits id', () => {
    const parser = createSseParser()
    const chunk =
      'id: 42\nevent: reading\ndata: {"value":1}\n\n' +
      'event: reading\ndata: {"value":2}\n\n'

    expect(parser.push(chunk)).toEqual([
      { id: '42', event: 'reading', data: '{"value":1}' },
      { id: '42', event: 'reading', data: '{"value":2}' },
    ])
  })

  it('resets the last event id when an empty id field is sent', () => {
    const parser = createSseParser()
    const chunk = 'id: 42\ndata: first\n\nid:\ndata: second\n\n'

    expect(parser.push(chunk)).toEqual([
      { id: '42', event: 'message', data: 'first' },
      { event: 'message', data: 'second' },
    ])
  })

  it('notifies onBlock on keepalive comments and data frames', () => {
    const blocks: number[] = []
    const parser = createSseParser({ onBlock: () => blocks.push(1) })

    parser.push(':keepalive\n\n')
    expect(blocks).toHaveLength(1)

    parser.push('event: reading\ndata: 1\n\n')
    expect(blocks).toHaveLength(2)
  })

  it('notifies onEventId when id changes or is reset', () => {
    const ids: (string | null)[] = []
    const parser = createSseParser({ onEventId: (id) => ids.push(id) })

    parser.push('id: 42\ndata: first\n\nid:\ndata: second\n\n')
    expect(ids).toEqual(['42', null])
  })
})
