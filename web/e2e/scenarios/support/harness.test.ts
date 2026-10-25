import { describe, expect, it } from 'vitest'

import { bogotaDateAt, liveLogbookEntries, parseOtpLine, parseSimRunOutput } from './harness'
import type { SyncPullChange } from './harness'

describe('bogotaDateAt', () => {
  it('reads the Bogota calendar day of an instant', () => {
    // 2026-10-24 04:00 UTC is 2026-10-23 23:00 in Bogota (UTC-5, no DST).
    expect(bogotaDateAt(new Date('2026-10-24T04:00:00Z'))).toBe('2026-10-23')
    expect(bogotaDateAt(new Date('2026-10-24T09:00:00Z'))).toBe('2026-10-24')
  })
})

describe('parseOtpLine', () => {
  it('reads the code the api printed for the phone', () => {
    const log = '[dev] OTP for +573101234567: 482913\n'
    expect(parseOtpLine(log, '+573101234567')).toBe('482913')
  })

  it('ignores codes printed for other phones', () => {
    const log = '[dev] OTP for +573109999999: 111111\n[dev] OTP for +573101234567: 482913\n'
    expect(parseOtpLine(log, '+573109999999')).toBe('111111')
  })

  it('returns the newest code when the phone was issued several', () => {
    // The store's `issue` overwrites the previous code, so the last printed
    // line is the one that still verifies.
    const log = [
      '[dev] OTP for +573101234567: 111111',
      '[dev] OTP for +573101234567: 222222',
      '',
    ].join('\n')
    expect(parseOtpLine(log, '+573101234567')).toBe('222222')
  })

  it('answers null when the phone has no code yet', () => {
    expect(parseOtpLine('no codes here\n', '+573101234567')).toBeNull()
    expect(parseOtpLine('', '+573101234567')).toBeNull()
  })

  it('does not treat a phone as a prefix of a longer one', () => {
    const log = '[dev] OTP for +5731012345678: 999999\n'
    expect(parseOtpLine(log, '+573101234567')).toBeNull()
  })
})

describe('parseSimRunOutput', () => {
  it('reads the member phone and the plot id sim run printed', () => {
    const output = [
      '[sim] scenario dry-spell-rainfed: member +573101234567, plot 019337d2-7c1a-7cc2-9f0e-8d3f21a4b5c6',
      '[sim] node-less scenario: plot and cycle provisioned, nothing to publish',
    ].join('\n')
    expect(parseSimRunOutput(output)).toEqual({
      phone: '+573101234567',
      plotId: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b5c6',
    })
  })

  it('answers null when the provisioning line is not there', () => {
    expect(parseSimRunOutput('unknown scenario foo; available: el-nino\n')).toBeNull()
    expect(parseSimRunOutput('')).toBeNull()
  })
})

const PLOT_ID = '019337d2-7c1a-7cc2-9f0e-8d3f21a4b5c6'
const OTHER_PLOT_ID = '019337d2-7c1a-7cc2-9f0e-8d3f21a4b5c7'

function entryChange(overrides: Partial<SyncPullChange['data']> = {}): SyncPullChange {
  return {
    id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b600',
    entity: 'logbook_entry',
    op: 'upsert',
    server_version: 7,
    data: {
      id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b600',
      org_id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b5c8',
      plot_id: PLOT_ID,
      kind: 'observation',
      notes: 'bitácora offline del escenario D',
      created_offline: true,
      client_updated_at: '2026-10-25T12:00:00Z',
      deleted_at: null,
      ...overrides,
    },
  }
}

describe('liveLogbookEntries', () => {
  it('reads the entry the sync pushed for the plot (docs/06:478, row D)', () => {
    const page = { changes: [entryChange()], next_since: 7, has_more: false }
    const entries = liveLogbookEntries(page, PLOT_ID)
    expect(entries).toHaveLength(1)
    expect(entries[0]).toMatchObject({
      id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b600',
      plot_id: PLOT_ID,
      kind: 'observation',
      notes: 'bitácora offline del escenario D',
      created_offline: true,
      deleted_at: null,
    })
  })

  it('counts one row when the change repeats across pages, and two when the server really holds two', () => {
    // The "no duplicates" assert is a count over the server's own pull: two
    // changes for two ids is exactly the duplication the scenario must fail on.
    const duplicate = entryChange({ id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b601' })
    duplicate.data = { ...duplicate.data, id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b601' }
    expect(liveLogbookEntries({ changes: [entryChange(), duplicate], next_since: 8, has_more: false }, PLOT_ID)).toHaveLength(2)
    expect(liveLogbookEntries({ changes: [entryChange()], next_since: 7, has_more: false }, PLOT_ID)).toHaveLength(1)
  })

  it('leaves out another plot’s entries and tombstoned ones', () => {
    const otherPlot = entryChange()
    otherPlot.data = { ...otherPlot.data, plot_id: OTHER_PLOT_ID }
    const tombstone = entryChange({ deleted_at: '2026-10-25T13:00:00Z' })
    const visit: SyncPullChange = {
      id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b602',
      entity: 'extension_visit',
      op: 'upsert',
      server_version: 9,
      data: {
        id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b602',
        org_id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b5c8',
        farm_id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b5c9',
        plot_id: PLOT_ID,
        technician_id: '019337d2-7c1a-7cc2-9f0e-8d3f21a4b5ca',
        visited_on: '2026-10-25',
        topics: ['natural_resources'],
        recommendations: null,
        commitments: null,
        notes: null,
        client_updated_at: '2026-10-25T12:00:00Z',
        deleted_at: null,
      },
    }
    const page = { changes: [entryChange(), otherPlot, tombstone, visit], next_since: 9, has_more: false }
    const entries = liveLogbookEntries(page, PLOT_ID)
    expect(entries).toHaveLength(1)
    expect(entries[0]?.notes).toBe('bitácora offline del escenario D')
  })
})
