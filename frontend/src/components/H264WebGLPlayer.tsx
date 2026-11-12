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
      while (pendingDecode.length && decoder.decodeQueueSize < 8) {
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
        if (!decoder) {
          decoder = new VideoDecoder({
            output: frame => {
              if (stopped || failed) {
                frame.close()
                return
              }
              // Drop cached-GOP output while still decoding it for H.264 reference
              // state. Check again after the async bitmap conversion to avoid a
              // historical frame rendering after the live boundary was crossed.
              const frameTimestamp = frame.timestamp
              if (liveStartTimestamp !== null && frameTimestamp < liveStartTimestamp) {
                frame.close()
                return
              }
              // WebGL texImage2D implementations do not consistently accept
              // VideoFrame directly. ImageBitmap is a broadly supported source.
              void createImageBitmap(frame).then(bitmap => {
                try {
                  if (stopped || failed) return
                  if (liveStartTimestamp !== null && frameTimestamp < liveStartTimestamp) return
                  const size = renderer.render(bitmap, frame.displayWidth, frame.displayHeight)
                  renderer.render(bitmap, frame.displayWidth, frame.displayHeight)
                } catch (error) {
                  fail(error instanceof Error ? error.message : 'WebGL 视频渲染失败')
                } finally {
                  bitmap.close()
                }
              }).catch(error => {
                fail(error instanceof Error ? `VideoFrame 转 ImageBitmap 失败：${error.message}` : 'VideoFrame 转 ImageBitmap 失败')
              }).finally(() => frame.close())
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
        if (pendingDecode.length >= 1800) {
          throw new Error('视频解码输入缓存超过上限，已回落旧播放器')
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
        bootstrapStart: () => { liveStartTimestamp = Number.POSITIVE_INFINITY },
        bootstrapEnd: () => {
          if (stopped || failed) return
          parser.flushAccessUnit()
          liveStartTimestamp = timestamp
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
