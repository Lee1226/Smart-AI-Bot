// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import type { Adb } from '@yume-chan/adb'
import { executeBrowserUsb } from './browserUsb'

vi.mock('./api', () => ({ createDevice: vi.fn(), fetchDevices: vi.fn() }))

function deviceWithOutput(outputs: string[]) {
  const stream = (text: string) => new ReadableStream<Uint8Array>({ start(c) {
    c.enqueue(new TextEncoder().encode(text)); c.close()
  } })
  const spawn = vi.fn(async () => ({ stdout: stream(outputs.shift() || ''), stderr: stream(''), exited: Promise.resolve(0) }))
  return { device: { subprocess: { shellProtocol: { spawn } } } as unknown as Adb, spawn }
}

describe('SAB start_app RPC over browser USB', () => {
  it('keeps USB screenshots at device dimensions for backend-only resizing', async () => {
    const { device } = deviceWithOutput(['png-fixture'])
    const close = vi.fn()
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 1080, height: 2400, close })))
    const drawImage = vi.fn()
    const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D)
    const encode = vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(function (this: HTMLCanvasElement) {
      expect([this.width, this.height]).toEqual([1080, 2400])
      return 'data:image/png;base64,fixture'
    })
    try {
      await expect(executeBrowserUsb(device, 'screenshot', {}, new AbortController().signal)).resolves.toBe('fixture')
      expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 1080, 2400)
      expect(close).toHaveBeenCalledOnce()
    } finally {
      context.mockRestore(); encode.mockRestore(); vi.unstubAllGlobals()
    }
  })
  it('accepts the backend app method and launches the resolved activity', async () => {
    const { device, spawn } = deviceWithOutput(['com.example.fixture/.MainActivity\n', 'Status: ok\n'])
    await expect(executeBrowserUsb(device, 'app', { package: 'com.example.fixture', stopBeforeLaunch: false }, new AbortController().signal)).resolves.toBe('Launched com.example.fixture')
    expect(spawn.mock.calls.map(call => (call as unknown[])[0])).toEqual([
      "cmd package resolve-activity --brief 'com.example.fixture'", "am start -W -n 'com.example.fixture/.MainActivity'",
    ])
  })
  it('preserves the launch alias and validates an explicit activity before executing', async () => {
    const { device, spawn } = deviceWithOutput(['Status: ok\n'])
    await expect(executeBrowserUsb(device, 'app/launch', { package: 'com.example.fixture', activity: '.MainActivity' }, new AbortController().signal)).resolves.toContain('Launched')
    expect(spawn).toHaveBeenCalledOnce()
    await expect(executeBrowserUsb(device, 'app', { package: 'com.example.fixture', activity: 'bad; command' }, new AbortController().signal)).rejects.toThrow('无效应用 Activity')
    expect(spawn).toHaveBeenCalledOnce()
  })
  it('reports a real missing activity instead of claiming a successful launch', async () => {
    const { device, spawn } = deviceWithOutput(['No activity found'])
    await expect(executeBrowserUsb(device, 'app', { package: 'com.example.fixture' }, new AbortController().signal)).rejects.toThrow('找不到应用入口')
    expect(spawn).toHaveBeenCalledOnce()
  })
})
