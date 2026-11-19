import { useEffect, useRef } from 'react'
import type { H264StreamSource } from '../lib/h264Stream'
import { H264DecodePipeline } from '../lib/h264DecodePipeline'
import { WebGLRenderer } from '../lib/video/WebGLRenderer'

export default function H264WebGLPlayer({ source, onFail }: { source: H264StreamSource; onFail?: (message: string) => void }) {
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
    try { renderer = new WebGLRenderer(canvas) }
    catch (error) {
      onFailRef.current?.(error instanceof Error ? error.message : 'WebGL 初始化失败')
      return
    }

    let stopped = false
    let failed = false
    let bitmapConversionPending = false
    let liveStartTimestamp: number | null = null
    let stopSource: (() => void) | undefined
    const fail = (message: string) => {
      if (stopped || failed) return
      failed = true
      stopSource?.()
      onFailRef.current?.(message)
    }
    const pipeline = new H264DecodePipeline(frame => {
      const frameTimestamp = frame.timestamp
      if (liveStartTimestamp !== null && frameTimestamp < liveStartTimestamp) return
      if (renderer.renderVideoFrame(frame)) return
      if (bitmapConversionPending) return
      bitmapConversionPending = true
      // Keep an owned frame alive until the asynchronous bitmap conversion ends.
      const ownedFrame = frame.clone()
      void createImageBitmap(ownedFrame).then(bitmap => {
        try {
          if (!stopped && !failed && (liveStartTimestamp === null || frameTimestamp >= liveStartTimestamp)) {
            renderer.render(bitmap, ownedFrame.displayWidth, ownedFrame.displayHeight)
          }
        } catch (error) {
          fail(error instanceof Error ? error.message : 'WebGL 视频渲染失败')
        } finally { bitmap.close() }
      }).catch(error => {
        fail(error instanceof Error ? `VideoFrame 转 ImageBitmap 失败：${error.message}` : 'VideoFrame 转 ImageBitmap 失败')
      }).finally(() => {
        ownedFrame.close()
        bitmapConversionPending = false
      })
    }, fail)

    try {
      stopSource = source({
        reset: () => { liveStartTimestamp = null; pipeline.reset() },
        flush: () => pipeline.flush(),
        bootstrapStart: () => { liveStartTimestamp = null },
        bootstrapEnd: () => pipeline.flush(),
        data: chunk => pipeline.feed(chunk),
        fail,
      })
      if (failed) stopSource()
    } catch (error) {
      fail(error instanceof Error ? error.message : '视频流启动失败')
    }

    return () => {
      stopped = true
      stopSource?.()
      pipeline.dispose()
      renderer.dispose()
    }
  }, [source])

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full object-contain" />
}
