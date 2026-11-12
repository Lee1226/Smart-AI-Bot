import { useEffect, useRef } from 'react'
import type { H264StreamSource } from '../lib/h264Stream'
import { H264AnnexB } from '../lib/h264AnnexB'

// Low-latency live screen, scrcpy / Android-Studio style: decode each H.264
// access unit with WebCodecs and blit the VideoFrame straight to a <canvas>.
// No MSE, no media timeline, no playback buffer → "frame in, frame out", so it
// never drifts behind the device the way the <video>/jmuxer path did.
//
// onFail fires if WebCodecs is unavailable or the backend reports no ADB (4404),
// so the panel can fall back to screenshot polling.
export default function H264Canvas({ source, onFail }: { source: H264StreamSource; onFail?: (message: string) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const onFailRef = useRef(onFail)
  onFailRef.current = onFail

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    if (typeof (window as unknown as { VideoDecoder?: unknown }).VideoDecoder === 'undefined') {
      onFailRef.current?.('浏览器不支持 WebCodecs')  // no WebCodecs → screenshot fallback
      return
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) { onFailRef.current?.('Canvas 不可用'); return }

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
              try {
                if (stopped || failed) return
                if (liveStartTimestamp !== null && frame.timestamp < liveStartTimestamp) return
                if (canvas.width !== frame.displayWidth) canvas.width = frame.displayWidth
                if (canvas.height !== frame.displayHeight) canvas.height = frame.displayHeight
                ctx.drawImage(frame, 0, 0)
              } finally { frame.close() }
            },
            error: () => fail('视频解码失败，已回落截图'),
          })
          const hex = (n: number) => n.toString(16).padStart(2, '0')
          decoder.addEventListener('dequeue', pumpDecode)
          decoder.configure({ codec: `avc1.${hex(sps[1])}${hex(sps[2])}${hex(sps[3])}`, optimizeForLatency: true })
        }
        if (pendingDecode.length >= 1800) throw new Error('视频解码输入缓存超过上限，已回落截图')
        pendingDecode.push(new EncodedVideoChunk({ type: key ? 'key' : 'delta', timestamp, data }))
        timestamp += Math.round(1e6 / 32)
        hasKey ||= key
        pumpDecode()
      })
    }
    reset()
    stopSource = source({
      reset,
      flush: () => { if (!stopped && !failed) parser.flushAccessUnit() },
      bootstrapEnd: () => {
        if (stopped || failed) return
        parser.flushAccessUnit()
        // Decode the cached GOP to establish reference frames, but display only
        // decoded frames at or after the live-stream boundary.
        liveStartTimestamp = timestamp
      },
      data: chunk => {
        if (stopped || failed) return
        try { parser.feed(chunk) } catch (e) { fail(e instanceof Error ? e.message : '视频解析失败') }
      },
      fail,
    })
    if (failed) stopSource()

    return () => {
      stopped = true
      stopSource?.()
      try { decoder?.close() } catch { /* already closed */ }
    }
  }, [source])

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full object-contain" />
}
