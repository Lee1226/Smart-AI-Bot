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
  it('accepts the backend app method and launches the resolved activity', async () => {
    const { device, spawn } = deviceWithOutput(['com.example.app/.MainActivity\n', 'Status: ok\n'])
    await expect(executeBrowserUsb(device, 'app', { package: 'com.example.app', stopBeforeLaunch: false }, new AbortController().signal)).resolves.toBe('Launched com.example.app')
    expect(spawn.mock.calls.map(call => (call as unknown[])[0])).toEqual([
      "cmd package resolve-activity --brief 'com.example.app'", "am start -W -n 'com.example.app/.MainActivity'",
    ])
  })
  it('preserves the launch alias and validates an explicit activity before executing', async () => {
    const { device, spawn } = deviceWithOutput(['Status: ok\n'])
    await expect(executeBrowserUsb(device, 'app/launch', { package: 'com.example.app', activity: '.MainActivity' }, new AbortController().signal)).resolves.toContain('Launched')
    expect(spawn).toHaveBeenCalledOnce()
    await expect(executeBrowserUsb(device, 'app', { package: 'com.example.app', activity: 'bad; command' }, new AbortController().signal)).rejects.toThrow('无效应用 Activity')
    expect(spawn).toHaveBeenCalledOnce()
  })
  it('reports a real missing activity instead of claiming a successful launch', async () => {
    const { device, spawn } = deviceWithOutput(['No activity found'])
    await expect(executeBrowserUsb(device, 'app', { package: 'com.example.app' }, new AbortController().signal)).rejects.toThrow('找不到应用入口')
    expect(spawn).toHaveBeenCalledOnce()
  })
})
