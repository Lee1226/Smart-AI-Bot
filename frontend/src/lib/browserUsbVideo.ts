import type { Adb } from '@yume-chan/adb'
import type { H264StreamSource } from './h264Stream'

// Separate ADB shell socket: video never enters the serialized AI RPC queue.
// Shell v2 keeps stderr out of the binary H.264 data. No APK or server ADB.
export function usbH264Source(device: Adb): H264StreamSource {
  return handlers => {
    const controller = new AbortController()
    let stopProcess: (() => void) | undefined
    let watchdog: ReturnType<typeof setTimeout> | undefined
    let cycleDeadline: ReturnType<typeof setTimeout> | undefined
    const fail = (message: string) => {
      if (controller.signal.aborted) return
      controller.abort()
      clearTimeout(watchdog)
      clearTimeout(cycleDeadline)
      stopProcess?.()
      handlers.fail(message)
    }
    const armWatchdog = () => {
      clearTimeout(watchdog)
      watchdog = setTimeout(() => fail('USB 录屏超时，已回落截图'), 15000)
    }
    const run = async () => {
      const service = device.subprocess.shellProtocol
      if (!service) throw new Error('USB 录屏需要 Android shell v2')
      while (!controller.signal.aborted) {
        handlers.reset()
        armWatchdog()
        cycleDeadline = setTimeout(() => fail('USB 录屏未正常结束，已回落截图'), 190000)
        const process = await service.spawn(
          'screenrecord --output-format=h264 --time-limit 175 --bit-rate 4000000 -',
          controller.signal,
        )
        // exited can reject when cancellation races socket creation.
        void process.exited.catch(() => {})
        stopProcess = () => { void Promise.resolve(process.kill()).catch(() => {}) }
        // Cancellation can race the asynchronous socket creation.
        if (controller.signal.aborted) { stopProcess(); return }
        let stderr = ''
        let bytes = 0
        const read = async (stream: typeof process.stdout, video: boolean) => {
          const reader = stream.getReader()
          try {
            while (!controller.signal.aborted) {
              const { value, done } = await reader.read()
              if (done) break
              if (video && value.length) {
                bytes += value.length
                // A static display may legitimately emit no new frames.
                // The short watchdog is for startup, not screen inactivity.
                clearTimeout(watchdog)
                handlers.data(value)
              } else if (!video) {
                stderr = (stderr + new TextDecoder().decode(value)).slice(-2048)
              }
            }
          } finally { reader.releaseLock() }
        }
        const [, , code] = await Promise.all([read(process.stdout, true), read(process.stderr, false), process.exited])
        clearTimeout(watchdog)
        clearTimeout(cycleDeadline)
        stopProcess = undefined
        if (controller.signal.aborted) return
        if (code !== 0 || bytes === 0) throw new Error(stderr.trim() || `USB 录屏失败 (${code})`)
        // screenrecord has a 3 minute limit: start a fresh codec session.
      }
    }
    void run().catch(e => fail(e instanceof Error ? e.message : String(e)))
    return () => {
      controller.abort()
      clearTimeout(watchdog)
      clearTimeout(cycleDeadline)
      stopProcess?.()
    }
  }
}
