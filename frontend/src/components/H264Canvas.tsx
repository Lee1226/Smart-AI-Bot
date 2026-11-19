import { useEffect, useRef } from 'react'
import type { H264StreamSource } from '../lib/h264Stream'
import { H264DecodePipeline } from '../lib/h264DecodePipeline'

// Low-latency live screen: shared WebCodecs decode pipeline, Canvas 2D output.
export default function H264Canvas({ source, onFail, onVideoSize }: { source: H264StreamSource; onFail?: (message: string) => void; onVideoSize?: (width: number, height: number) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const onFailRef = useRef(onFail)
  const onVideoSizeRef = useRef(onVideoSize)
  onFailRef.current = onFail
  onVideoSizeRef.current = onVideoSize

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    if (typeof (window as unknown as { VideoDecoder?: unknown }).VideoDecoder === 'undefined') {
      onFailRef.current?.('浏览器不支持 WebCodecs')
      return
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) { onFailRef.current?.('Canvas 不可用'); return }

    let stopped = false
    let stopSource: (() => void) | undefined
    const fail = (message: string) => {
      if (stopped) return
      stopSource?.()
      onFailRef.current?.(message)
    }
    const pipeline = new H264DecodePipeline(frame => {
      if (canvas.width !== frame.displayWidth) canvas.width = frame.displayWidth
      if (canvas.height !== frame.displayHeight) canvas.height = frame.displayHeight
      onVideoSizeRef.current?.(frame.displayWidth, frame.displayHeight)
      ctx.drawImage(frame, 0, 0)
    }, fail)

    stopSource = source({
      reset: () => pipeline.reset(),
      flush: () => pipeline.flush(),
      bootstrapStart: () => {},
      bootstrapEnd: () => pipeline.flush(),
      data: chunk => pipeline.feed(chunk),
      fail,
    })

    return () => {
      stopped = true
      stopSource?.()
      pipeline.dispose()
    }
  }, [source])

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full object-contain" />
}
