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
    const connect = () => {
      if (stopped) return
      handlers.reset()
      ws = new WebSocket(url)
      ws.binaryType = 'arraybuffer'
      ws.onmessage = e => { if (!stopped) handlers.data(new Uint8Array(e.data as ArrayBuffer)) }
      ws.onclose = e => {
        if (stopped) return
        if (e.code === 4404 || e.code === 4401 || e.code === 4403) {
          handlers.fail('视频流不可用')
          return
        }
        retry = setTimeout(connect, 1000)
      }
      ws.onerror = () => {}
    }
    connect()
    return () => { stopped = true; clearTimeout(retry); ws?.close() }
  }
}
