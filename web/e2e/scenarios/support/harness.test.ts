import { describe, expect, it } from 'vitest'

import { bogotaDateAt, parseOtpLine, parseSimRunOutput } from './harness'

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
