import type { Adb } from '@yume-chan/adb'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { usbH264Source } from './browserUsbVideo'

function processFixture() {
  let stdout!: ReadableStreamDefaultController<Uint8Array>
  let stderr!: ReadableStreamDefaultController<Uint8Array>
  let exit!: (code: number) => void
  const process = {
    stdout: new ReadableStream<Uint8Array>({ start(c) { stdout = c } }),
    stderr: new ReadableStream<Uint8Array>({ start(c) { stderr = c } }),
    exited: new Promise<number>(resolve => { exit = resolve }),
    kill: vi.fn(() => { stdout.close(); stderr.close(); exit(0) }),
  }
  return { process, stdout, stderr, finish: (code: number) => { stdout.close(); stderr.close(); exit(code) } }
}
const ticks = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }
const handlers = () => ({ data: vi.fn(), reset: vi.fn(), fail: vi.fn() })
afterEach(() => { vi.useRealTimers() })

describe('browser USB video lifecycle', () => {
  it('streams bytes without stderr corruption, restarts clean cycles and closes on stop', async () => {
    const first = processFixture(), second = processFixture()
    const spawn = vi.fn().mockResolvedValueOnce(first.process).mockResolvedValueOnce(second.process)
    const h = handlers()
    const stop = usbH264Source({ subprocess: { shellProtocol: { spawn } } } as unknown as Adb)(h)
    await ticks()
    first.stdout.enqueue(new Uint8Array([0, 0, 1, 0x67]))
    first.stderr.enqueue(new TextEncoder().encode('diagnostic'))
    await ticks()
    expect(h.data).toHaveBeenCalledExactlyOnceWith(new Uint8Array([0, 0, 1, 0x67]))
    first.finish(0)
    await ticks()
    expect(spawn).toHaveBeenCalledTimes(2)
    expect(h.reset).toHaveBeenCalledTimes(2)
    stop()
    await ticks()
    expect(second.process.kill).toHaveBeenCalledOnce()
    expect(h.fail).not.toHaveBeenCalled()
    expect(spawn).toHaveBeenCalledTimes(2)
  })
  it('reports denied recording and a silent startup, allowing screenshot fallback', async () => {
    vi.useFakeTimers()
    const denied = processFixture()
    const spawn = vi.fn().mockResolvedValue(denied.process)
    const h = handlers()
    usbH264Source({ subprocess: { shellProtocol: { spawn } } } as unknown as Adb)(h)
    await ticks()
    denied.stderr.enqueue(new TextEncoder().encode('permission denied'))
    denied.finish(1)
    await ticks()
    expect(h.fail).toHaveBeenCalledWith('permission denied')

    const silent = processFixture(), next = handlers()
    usbH264Source({ subprocess: { shellProtocol: { spawn: async () => silent.process } } } as unknown as Adb)(next)
    await ticks()
    await vi.advanceTimersByTimeAsync(15000)
    expect(silent.process.kill).toHaveBeenCalledOnce()
    expect(next.fail).toHaveBeenCalledOnce()
  })
  it('closes a process that finishes spawning after the view has already unmounted', async () => {
    const late = processFixture()
    let resolve!: (value: typeof late.process) => void
    const spawn = () => new Promise<typeof late.process>(r => { resolve = r })
    const h = handlers()
    const stop = usbH264Source({ subprocess: { shellProtocol: { spawn } } } as unknown as Adb)(h)
    stop()
    resolve(late.process)
    await ticks()
    expect(late.process.kill).toHaveBeenCalledOnce()
    expect(h.fail).not.toHaveBeenCalled()
  })
})
