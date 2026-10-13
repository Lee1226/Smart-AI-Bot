export type H264StreamHandlers = {
  data: (chunk: Uint8Array) => void
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
      ws = new WebSocket(url)
      ws.binaryType = 'arraybuffer'
      firstFrame = setTimeout(() => { if (!stopped) { handlers.fail('视频流超时，请检查手机录屏授权或连接'); ws?.close() } }, 20000)
      ws.onmessage = e => { if (!stopped && e.data instanceof ArrayBuffer) { clearTimeout(firstFrame); retryCount = 0; handlers.data(new Uint8Array(e.data)) } }
      ws.onclose = e => {
        clearTimeout(firstFrame)
        if (stopped) return
        if (++retryCount > 3 || e.code === 4409 || e.code === 4404 || e.code === 4401 || e.code === 4403) {
          handlers.fail('视频流不可用')
          return
        }
        retry = setTimeout(connect, 1000)
      }
      ws.onerror = () => {}
    }
    connect()
    return () => { stopped = true; clearTimeout(retry); clearTimeout(firstFrame); ws?.close() }
  }
}
