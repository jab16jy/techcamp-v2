/**
 * D-T0.7 / docs/07 §Mapa de pantallas, Inicio item 1: "la parcela activa es la
 * última que el usuario abrió en ese dispositivo, guardada por organización".
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import {
  forgetActivePlotId,
  getActivePlotId,
  setActivePlotId,
  useActivePlotId,
} from './activePlot'

describe('active plot store', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('has no active plot on a device that never opened one', () => {
    expect(getActivePlotId('org-1')).toBeNull()
  })

  it('remembers the last plot the user opened', () => {
    setActivePlotId('org-1', 'plot-1')

    expect(getActivePlotId('org-1')).toBe('plot-1')
  })

  it('keeps one plot per organization, so switching orgs does not carry the other one over', () => {
    setActivePlotId('org-1', 'plot-1')
    setActivePlotId('org-2', 'plot-9')

    expect(getActivePlotId('org-1')).toBe('plot-1')
    expect(getActivePlotId('org-2')).toBe('plot-9')
  })

  it('replaces the remembered plot when another one is opened', () => {
    setActivePlotId('org-1', 'plot-1')
    setActivePlotId('org-1', 'plot-2')

    expect(getActivePlotId('org-1')).toBe('plot-2')
  })

  it('never returns a plot for an org the user did not open one in', () => {
    setActivePlotId('org-1', 'plot-1')

    expect(getActivePlotId('org-3')).toBeNull()
    expect(getActivePlotId(null)).toBeNull()
  })

  it('ignores a stored value it cannot read, instead of crashing the home screen', () => {
    localStorage.setItem('techcamp.active_plot', 'not json')

    expect(getActivePlotId('org-1')).toBeNull()
  })

  it('re-renders the hook when the active plot changes', () => {
    const { result } = renderHook(() => useActivePlotId('org-1'))
    expect(result.current).toBeNull()

    act(() => setActivePlotId('org-1', 'plot-1'))

    expect(result.current).toBe('plot-1')
  })

  it('forgets a plot on request, and the hook follows', () => {
    setActivePlotId('org-1', 'plot-1')
    const { result } = renderHook(() => useActivePlotId('org-1'))
    expect(result.current).toBe('plot-1')

    act(() => forgetActivePlotId('org-1'))

    expect(result.current).toBeNull()
  })
})
