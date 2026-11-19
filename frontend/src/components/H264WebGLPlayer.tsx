import { useEffect, useRef } from 'react'
import type { H264StreamSource } from '../lib/h264Stream'
import { H264AnnexB } from '../lib/h264AnnexB'
import { WebGLRenderer } from '../lib/video/WebGLRenderer'

export default function H264WebGLPlayer({
  source,
  onFail,
}: {
  source: H264StreamSource
  onFail?: (message: string) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const onFailRef = useRef(onFail)
  onFailRef.current = onFail

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    if (!window.isSecureContext || typeof VideoDecoder === 'undefined') {
      onFailRef.current?.('当前环境不支持 WebCodecs 安全上下文')
      return
    }

    let renderer: WebGLRenderer
    try {
      renderer = new WebGLRenderer(canvas)
    } catch (error) {
      onFailRef.current?.(error instanceof Error ? error.message : 'WebGL 初始化失败')
      return
    }

    let stopped = false
    let failed = false
    let decoder: VideoDecoder | null = null
    let parser: H264AnnexB
    let timestamp = 0
    let liveStartTimestamp: number | null = null
    let hasKey = false
    let bitmapConversionPending = false
    let pendingDecode: EncodedVideoChunk[] = []
    let stopSource: (() => void) | undefined
    const fail = (message: string) => {
      if (stopped || failed) return
      failed = true
      stopSource?.()
      onFailRef.current?.(message)
    }
    const pumpDecode = () => {
      if (!decoder || stopped || failed) return
      while (pendingDecode.length && decoder.decodeQueueSize < 4) {
        const chunk = pendingDecode.shift()!
        try { decoder.decode(chunk) } catch (error) {
          fail(error instanceof Error ? error.message : '视频解码提交失败')
          return
        }
      }
    }
    const reset = () => {
      try { decoder?.close() } catch { /* already closed */ }
      decoder = null
      pendingDecode = []
      timestamp = 0
      liveStartTimestamp = null
      hasKey = false
      parser = new H264AnnexB((data, key, sps) => {
        if (stopped || failed || (!key && !hasKey)) return
        // Bound queued access units so rendering stays near the live edge.
        // On overload, discard stale deltas and resume from the next IDR.
        if (pendingDecode.length >= 8) {
          pendingDecode = []
          try { decoder?.close() } catch { /* already closed */ }
          decoder = null
          hasKey = false
          if (!key) return
        }
        if (!decoder) {
          decoder = new VideoDecoder({
            output: frame => {
              if (stopped || failed) {
                frame.close()
                return
              }
              const frameTimestamp = frame.timestamp
              if (liveStartTimestamp !== null && frameTimestamp < liveStartTimestamp) {
                frame.close()
                return
              }

              // Prefer synchronous VideoFrame -> WebGL upload. This avoids an
              // ImageBitmap allocation and an async hop for every decoded frame.
              if (renderer.renderVideoFrame(frame)) {
                frame.close()
                return
              }

              // Compatibility fallback for browsers whose WebGL implementation
              // cannot upload VideoFrame directly. Keep at most one conversion
              // in flight; dropping a display frame is better than building lag.
              if (bitmapConversionPending) {
                frame.close()
                return
              }
              bitmapConversionPending = true
              void createImageBitmap(frame).then(bitmap => {
                try {
                  if (stopped || failed) return
                  if (liveStartTimestamp !== null && frameTimestamp < liveStartTimestamp) return
                  renderer.render(bitmap, frame.displayWidth, frame.displayHeight)
                } catch (error) {
                  fail(error instanceof Error ? error.message : 'WebGL 视频渲染失败')
                } finally {
                  bitmap.close()
                }
              }).catch(error => {
                fail(error instanceof Error ? `VideoFrame 转 ImageBitmap 失败：${error.message}` : 'VideoFrame 转 ImageBitmap 失败')
              }).finally(() => {
                bitmapConversionPending = false
                frame.close()
              })
            },
            error: () => fail('WebCodecs 解码失败')
          })
          const hex = (n: number) => n.toString(16).padStart(2, '0')
          decoder.addEventListener('dequeue', pumpDecode)
          decoder.configure({
            codec: `avc1.${hex(sps[1])}${hex(sps[2])}${hex(sps[3])}`,
            optimizeForLatency: true,
          })
        }
        pendingDecode.push(new EncodedVideoChunk({
          type: key ? 'key' : 'delta',
          timestamp,
          data,
        }))
        timestamp += Math.round(1e6 / 32)
        hasKey ||= key
        pumpDecode()
      })
    }

    try {
      reset()
      stopSource = source({
        reset,
        flush: () => { if (!stopped && !failed) parser.flushAccessUnit() },
        // Render cached GOP frames immediately instead of hiding them.
        bootstrapStart: () => { liveStartTimestamp = null },
        bootstrapEnd: () => {
          if (stopped || failed) return
          parser.flushAccessUnit()
          // Keep rendering cached GOP frames while the decoder catches up to live packets.
        },
        data: chunk => {
          if (stopped || failed) return
          try { parser.feed(chunk) }
          catch (error) { fail(error instanceof Error ? error.message : 'H.264 数据解析失败') }
        },
        fail,
      })
      if (failed) stopSource()
    } catch (error) {
      fail(error instanceof Error ? error.message : '视频流启动失败')
    }

    return () => {
      stopped = true
      stopSource?.()
      try { decoder?.close() } catch { /* already closed */ }
      renderer.dispose()
    }
  }, [source])

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full object-contain" />
}
