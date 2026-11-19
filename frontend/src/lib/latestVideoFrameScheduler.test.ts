import { afterEach, describe, expect, it, vi } from 'vitest'
import { LatestVideoFrameScheduler } from './latestVideoFrameScheduler'

function frame() {
  return { close: vi.fn(), clone: vi.fn() } as unknown as VideoFrame
}

describe('LatestVideoFrameScheduler', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders only the newest frame waiting for the next animation frame', () => {
    let callback: FrameRequestCallback | undefined
    vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => {
      callback = next
      return 1
    })
    vi.stubGlobal('cancelAnimationFrame', vi.fn())

    const sourceA = frame()
    const sourceB = frame()
    const cloneA = frame()
    const cloneB = frame()
    vi.mocked(sourceA.clone).mockReturnValue(cloneA)
    vi.mocked(sourceB.clone).mockReturnValue(cloneB)
    const render = vi.fn()
    const scheduler = new LatestVideoFrameScheduler(render, vi.fn())

    scheduler.push(sourceA)
    scheduler.push(sourceB)

    expect(cloneA.close).toHaveBeenCalledTimes(1)
    expect(render).not.toHaveBeenCalled()
    callback?.(0)
    expect(render).toHaveBeenCalledWith(cloneB)
    expect(cloneB.close).toHaveBeenCalledTimes(1)
    scheduler.dispose()
  })

  it('closes a pending frame when disposed before presentation', () => {
    let callback: FrameRequestCallback | undefined
    vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => {
      callback = next
      return 1
    })
    const cancel = vi.fn()
    vi.stubGlobal('cancelAnimationFrame', cancel)

    const source = frame()
    const pending = frame()
    vi.mocked(source.clone).mockReturnValue(pending)
    const render = vi.fn()
    const scheduler = new LatestVideoFrameScheduler(render, vi.fn())
    scheduler.push(source)
    scheduler.dispose()

    expect(cancel).toHaveBeenCalledWith(1)
    expect(pending.close).toHaveBeenCalledTimes(1)
    callback?.(0)
    expect(render).not.toHaveBeenCalled()
  })
})
