import { useEffect, useRef, useState } from 'react'
import type { H264StreamSource } from '../lib/h264Stream'
import { H264AnnexB } from '../lib/h264AnnexB'
import { WebGLRenderer } from '../lib/video/WebGLRenderer'

type Metrics = {
  submitFps: number
  renderFps: number
  queueSize: number
  decodedFrames: number
  renderedFrames: number
  droppedFrames: number
  width: number
  height: number
}

const EMPTY_METRICS: Metrics = {
  submitFps: 0, renderFps: 0, queueSize: 0,
  decodedFrames: 0, renderedFrames: 0, droppedFrames: 0,
  width: 0, height: 0,
}

export default function H264WebGLPlayer({
  source,
  onFail,
}: {
  source: H264StreamSource
  onFail?: (message: string) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const onFailRef = useRef(onFail)
  const [metrics, setMetrics] = useState<Metrics>(EMPTY_METRICS)
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
    let hasKey = false
    let pendingDecode: EncodedVideoChunk[] = []
    let waitingForKeyframe = false
    let codec: string | undefined
    let stopSource: (() => void) | undefined
    let decodedFrames = 0
    let renderedFrames = 0
    let droppedFrames = 0
    let previousDecoded = 0
    let previousRendered = 0
    let previousAt = performance.now()

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
      waitingForKeyframe = false
      codec = undefined
      timestamp = 0
      hasKey = false
      parser = new H264AnnexB((data, key, sps) => {
        if (stopped || failed) return
        if (!decoder) {
          decoder = new VideoDecoder({
            output: frame => {
              if (stopped || failed) {
                frame.close()
                return
              }
              // WebGL texImage2D implementations do not consistently accept
              // VideoFrame directly. ImageBitmap is a broadly supported source.
              void createImageBitmap(frame).then(bitmap => {
                try {
                  if (stopped || failed) return
                  const size = renderer.render(bitmap, frame.displayWidth, frame.displayHeight)
                  renderedFrames++
                  setMetrics(current => ({ ...current, width: size.width, height: size.height }))
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
          codec = `avc1.${hex(sps[1])}${hex(sps[2])}${hex(sps[3])}`
          decoder.addEventListener('dequeue', pumpDecode)
          decoder.configure({ codec, optimizeForLatency: true })
        }
        // Prefer the newest decodable picture over replaying stale frames.
        // Never discard arbitrary H.264 P frames: reset and resume at IDR.
        if (!waitingForKeyframe && decoder.decodeQueueSize + pendingDecode.length >= 16) {
          pendingDecode = []
          decoder.reset()
          waitingForKeyframe = true
          hasKey = false
          droppedFrames++
        }
        if (waitingForKeyframe) {
          if (!key) return
          decoder.reset()
          decoder.configure({ codec: codec!, optimizeForLatency: true })
          waitingForKeyframe = false
        }
        pendingDecode.push(new EncodedVideoChunk({
          type: key ? 'key' : 'delta',
          timestamp,
          data,
        }))
        timestamp += Math.round(1e6 / 32)
        hasKey ||= key
        decodedFrames++
        pumpDecode()
      })
      setMetrics(EMPTY_METRICS)
    }

    try {
      reset()
      stopSource = source({
        reset,
        flush: () => { if (!stopped && !failed) parser.flushAccessUnit() },
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

    const monitor = window.setInterval(() => {
      const now = performance.now()
      const elapsed = Math.max(1, now - previousAt)
      const submitFps = Math.round((decodedFrames - previousDecoded) * 1000 / elapsed)
      const renderFps = Math.round((renderedFrames - previousRendered) * 1000 / elapsed)
      setMetrics({
        submitFps,
        renderFps,
        queueSize: decoder?.decodeQueueSize ?? 0,
        decodedFrames,
        renderedFrames,
        droppedFrames,
        width: canvas.width,
        height: canvas.height,
      })
      previousDecoded = decodedFrames
      previousRendered = renderedFrames
      previousAt = now
    }, 1000)

    return () => {
      stopped = true
      window.clearInterval(monitor)
      stopSource?.()
      try { decoder?.close() } catch { /* already closed */ }
      renderer.dispose()
    }
  }, [source])

  return (
    <>
      <canvas ref={canvasRef} className="absolute inset-0 w-full h-full object-contain" />
      <div className="absolute left-2 top-2 z-10 rounded bg-black/70 px-2 py-1 text-[10px] leading-4 text-white font-mono pointer-events-none">
        <div>WebGL · Submit {metrics.submitFps} FPS · Render {metrics.renderFps} FPS</div>
        <div>Queue {metrics.queueSize} · Dropped {metrics.droppedFrames}</div>
        {metrics.width > 0 && <div>{metrics.width} × {metrics.height}</div>}
      </div>
    </>
  )
}
