export type ScrcpyPreviewPacket = {
  type: 'configuration' | 'data'
  data: Uint8Array
  keyframe: boolean
  pts?: bigint
}

export type ScrcpyPreviewMetadata = {
  codecId: number
  width: number
  height: number
}

export type H264StreamHandlers = {
  data: (chunk: Uint8Array) => void
  packet?: (packet: ScrcpyPreviewPacket) => void
  packetMode?: (metadata: ScrcpyPreviewMetadata) => void
  flush?: () => void
  bootstrapStart?: () => void
  bootstrapEnd?: () => void
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
      }, 75000)
      current.onmessage = e => {
        if (stopped || ws !== current) return
        if (e.data instanceof ArrayBuffer) {
          clearTimeout(firstFrame)
          retryCount = 0
          const bytes = new Uint8Array(e.data)
          if (bytes.length >= 20
            && bytes[0] === 0x53 && bytes[1] === 0x43
            && bytes[2] === 0x50 && bytes[3] === 0x31) {
            const view = new DataView(e.data)
            const kind = view.getUint8(4)
            const flags = view.getUint8(5)
            const pts = view.getBigUint64(8, false)
            const size = view.getUint32(16, false)
            if ((kind !== 0 && kind !== 1) || size !== bytes.length - 20) {
              handlers.fail('Scrcpy 视频数据包格式无效')
              current.close()
              return
            }
            handlers.packet?.({
              type: kind === 0 ? 'configuration' : 'data',
              data: bytes.subarray(20),
              keyframe: (flags & 1) !== 0,
              pts: kind === 0 ? undefined : pts,
            })
          } else {
            handlers.data(bytes)
          }
        } else if (typeof e.data === 'string' && e.data.startsWith('scrcpy_v1:')) {
          const values = e.data.slice('scrcpy_v1:'.length).split(':').map(Number)
          if (values.length !== 3 || values.some(value => !Number.isFinite(value))) {
            handlers.fail('Scrcpy 视频元数据格式无效')
            current.close()
            return
          }
          handlers.packetMode?.({ codecId: values[0], width: values[1], height: values[2] })
        } else if (e.data === 'h264_flush') {
          handlers.flush?.()
        } else if (e.data === 'h264_bootstrap_start') {
          handlers.bootstrapStart?.()
        } else if (e.data === 'h264_bootstrap_end') {
          handlers.bootstrapEnd?.()
        }
      }
      current.onclose = e => {
        clearTimeout(firstFrame)
        if (stopped || ws !== current) return

        // The remote video provider allows one viewer per device. A 4409
        // response is retryable because the previous preview may still be
        // releasing its session.
        const permanent = e.code === 4404 || e.code === 4401 || e.code === 4403
        // 1006 (aborted handshake) and 1012 (backend restart / failed Scrcpy
        // producer) are transient. Retry long enough for the backend to release
        // the old session and start a fresh producer.
        const maxRetries = e.code === 4409 ? 10 : 12
        if (permanent || ++retryCount > maxRetries) {
          handlers.fail(e.code === 4409 ? '视频设备正在被其他连接占用' : '视频流不可用')
          return
        }
        const delay = e.code === 4409
          ? Math.min(500 * retryCount, 3000)
          : Math.min(500 * (2 ** (retryCount - 1)), 5000)
        console.warn('[H264] WebSocket closed; retrying', {
          code: e.code, reason: e.reason, retry: retryCount, delay,
        })
        retry = setTimeout(connect, delay)
      }
      current.onerror = () => {
        // The browser usually follows this with close (often code 1006).
        // Never log the token-bearing URL.
        console.warn('[H264] WebSocket transport error', {
          readyState: current.readyState,
          retry: retryCount,
        })
      }
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
