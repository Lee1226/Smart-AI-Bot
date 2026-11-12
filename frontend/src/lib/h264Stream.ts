export type H264StreamHandlers = {
  data: (chunk: Uint8Array) => void
  flush?: () => void
  reset: () => void
  fail: (message: string) => void
}
export type H264StreamSource = (handlers: H264StreamHandlers) => () => void

export function websocketH264Source(url: string): H264StreamSource {
  return handlers => {
    let stopped = false
    let retry: ReturnType<typeof setTimeout> | undefined
    let ws: WebSocket | undefined
    let firstFrame: ReturnType<typeof setTimeout> | undefined
    let retryCount = 0
    const connect = () => {
      if (stopped) return
      handlers.reset()
      const current = new WebSocket(url)
      ws = current
      current.binaryType = 'arraybuffer'
      firstFrame = setTimeout(() => {
        if (!stopped && ws === current) {
          handlers.fail('视频流超时，请检查手机录屏授权或连接')
          current.close()
        }
      }, 20000)
      current.onmessage = e => {
        if (stopped || ws !== current) return
        if (e.data instanceof ArrayBuffer) {
          clearTimeout(firstFrame)
          retryCount = 0
          handlers.data(new Uint8Array(e.data))
        } else if (e.data === 'h264_flush') {
          handlers.flush?.()
        }
      }
      current.onclose = e => {
        clearTimeout(firstFrame)
        if (stopped || ws !== current) return

        // The backend allows only one viewer per device. During React StrictMode
        // development remounts, the previous socket can still be releasing its
        // Scrcpy session when the replacement socket arrives (close code 4409).
        // Treat that short-lived lock as retryable instead of immediately
        // falling back to screenshots.
        const permanent = e.code === 4404 || e.code === 4401 || e.code === 4403 || e.code === 4410
        // 1006 also covers a WebSocket handshake aborted by a transient proxy,
        // backend restart, or an overlapping viewer lifecycle transition.
        const maxRetries = e.code === 4409 ? 6 : 8
        if (permanent || ++retryCount > maxRetries) {
          handlers.fail(e.code === 4410 ? '视频预览已在其他连接中接管' : e.code === 4409 ? '视频设备正在被其他连接占用' : '视频流不可用')
          return
        }
        const delay = e.code === 4409
          ? Math.min(500 * retryCount, 2500)
          : Math.min(500 * (2 ** (retryCount - 1)), 4000)
        console.warn('[H264] WebSocket closed; retrying', {
          code: e.code, reason: e.reason, retry: retryCount, delay,
        })
        retry = setTimeout(connect, delay)
      }
      current.onerror = () => {}
    }
    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      clearTimeout(firstFrame)
      const current = ws
      ws = undefined
      if (current && current.readyState < WebSocket.CLOSING) current.close(1000, 'viewer disposed')
    }
  }
}
