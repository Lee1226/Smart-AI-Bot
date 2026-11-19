import { useEffect, useRef } from 'react'
import type { H264StreamSource } from '../lib/h264Stream'
import { H264DecodePipeline } from '../lib/h264DecodePipeline'
import { WebGLRenderer } from '../lib/video/WebGLRenderer'

type RendererMode = 'canvas' | 'webgl'

/**
 * Owns the H.264 source and decoder independently from the selected renderer.
 * Switching WebGL/Canvas only swaps the output sink; the socket and decoder stay alive.
 */
export default function H264LivePlayer({
  source, renderer, onFail, onVideoSize,
}: {
  source: H264StreamSource
  renderer: RendererMode
  onFail?: (message: string) => void
  onVideoSize?: (width: number, height: number) => void
}) {
  const webglCanvasRef = useRef<HTMLCanvasElement>(null)
  const canvas2dRef = useRef<HTMLCanvasElement>(null)
  const onFailRef = useRef(onFail)
  const onVideoSizeRef = useRef(onVideoSize)
  const outputRef = useRef<(frame: VideoFrame) => void>(() => {})
  const pipelineRef = useRef<H264DecodePipeline | null>(null)
  const failedRef = useRef(false)

  onFailRef.current = onFail
  onVideoSizeRef.current = onVideoSize

  // Source/decoder lifecycle: deliberately does not depend on renderer.
  useEffect(() => {
    if (!window.isSecureContext || typeof VideoDecoder === 'undefined') {
      onFailRef.current?.('当前环境不支持 WebCodecs 安全上下文')
      return
    }

    failedRef.current = false
    let stopped = false
    let stopSource: (() => void) | undefined
    const fail = (message: string) => {
      if (stopped || failedRef.current) return
      failedRef.current = true
      stopSource?.()
      onFailRef.current?.(message)
    }
    const pipeline = new H264DecodePipeline(frame => {
      if (stopped || failedRef.current) return
      try { outputRef.current(frame) }
      catch (error) { fail(error instanceof Error ? error.message : '视频渲染失败') }
    }, fail)
    pipelineRef.current = pipeline

    try {
      stopSource = source({
        reset: () => pipeline.reset(),
        flush: () => pipeline.flush(),
        bootstrapStart: () => {},
        bootstrapEnd: () => pipeline.flush(),
        data: chunk => pipeline.feed(chunk),
        fail,
      })
      if (failedRef.current) stopSource()
    } catch (error) {
      fail(error instanceof Error ? error.message : '视频流启动失败')
    }

    return () => {
      stopped = true
      stopSource?.()
      pipeline.dispose()
      if (pipelineRef.current === pipeline) pipelineRef.current = null
    }
  }, [source])

  // Renderer lifecycle: only replace the output sink, never reconnect or reset decode.
  useEffect(() => {
    let disposed = false
    let bitmapConversionPending = false
    let rendererInstance: WebGLRenderer | null = null
    let context: CanvasRenderingContext2D | null = null
    const webglCanvas = webglCanvasRef.current
    const canvas2d = canvas2dRef.current

    if (renderer === 'webgl') {
      if (!webglCanvas) return
      try { rendererInstance = new WebGLRenderer(webglCanvas) }
      catch (error) {
        onFailRef.current?.(error instanceof Error ? error.message : 'WebGL 初始化失败')
        return
      }
    } else {
      if (!canvas2d) return
      context = canvas2d.getContext('2d')
      if (!context) {
        onFailRef.current?.('Canvas 不可用')
        return
      }
    }

    outputRef.current = frame => {
      if (disposed) return
      if (renderer === 'canvas') {
        if (canvas2d!.width !== frame.displayWidth) canvas2d!.width = frame.displayWidth
        if (canvas2d!.height !== frame.displayHeight) canvas2d!.height = frame.displayHeight
        onVideoSizeRef.current?.(frame.displayWidth, frame.displayHeight)
        context!.drawImage(frame, 0, 0)
        return
      }

      if (rendererInstance!.renderVideoFrame(frame)) return
      // Avoid building an unbounded ImageBitmap conversion queue. One conversion
      // at a time is enough for the fallback path; WebGL texture upload is preferred.
      if (bitmapConversionPending) return
      bitmapConversionPending = true
      const ownedFrame = frame.clone()
      void createImageBitmap(ownedFrame).then(bitmap => {
        try {
          if (!disposed) rendererInstance?.render(bitmap, ownedFrame.displayWidth, ownedFrame.displayHeight)
        } catch (error) {
          if (!disposed) onFailRef.current?.(error instanceof Error ? error.message : 'WebGL 视频渲染失败')
        } finally { bitmap.close() }
      }).catch(error => {
        if (!disposed) onFailRef.current?.(error instanceof Error ? `VideoFrame 转 ImageBitmap 失败：${error.message}` : 'VideoFrame 转 ImageBitmap 失败')
      }).finally(() => {
        ownedFrame.close()
        bitmapConversionPending = false
      })
    }

    return () => {
      disposed = true
      outputRef.current = () => {}
      rendererInstance?.dispose()
    }
  }, [renderer])

  return (
    <>
      <canvas ref={webglCanvasRef} className={`absolute inset-0 w-full h-full object-contain ${renderer === 'webgl' ? '' : 'hidden'}`} />
      <canvas ref={canvas2dRef} className={`absolute inset-0 w-full h-full object-contain ${renderer === 'canvas' ? '' : 'hidden'}`} />
    </>
  )
}
