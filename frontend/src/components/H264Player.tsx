import { useEffect, useRef } from 'react'
import JMuxer from 'jmuxer'
import type { H264StreamSource } from '../lib/h264Stream'

// Both WebSocket and browser USB supply raw H.264; jmuxer wraps it in
// fragmented MP4 for MSE. Each recording cycle gets a fresh timeline.
export default function H264Player({ source, onFail }: { source: H264StreamSource; onFail?: (message: string) => void }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const onFailRef = useRef(onFail)
  onFailRef.current = onFail

  useEffect(() => {
    const node = videoRef.current
    if (!node) return

    let closedByUs = false
    let muxer: JMuxer | undefined

    // flushingTime must be a positive number: jmuxer does `flushingTime || 1500`,
    // so 0 silently becomes 1500ms → the video only updated every 1.5s (visible
    // "stutter every 1.5s"). 1ms ≈ flush on every fed chunk → smooth.
    // fps ≈ screenrecord's real capture rate (~32 on most devices). The raw H.264
    // stream carries no timestamps, so jmuxer times frames by this value.
    const reset = () => {
      muxer?.destroy()
      muxer = new JMuxer({ node, mode: 'video', flushingTime: 1, fps: 32, clearBuffer: true, debug: false,
        onError: () => fail('视频播放失败，已回落截图') })
    }

    // Live-edge sync: frames arrive slightly faster than they play, so MSE buffer
    // grows and the video drifts ever further behind the device ("much slower").
    // Keep playback pinned near the newest buffered frame.
    const sync = window.setInterval(() => {
      const b = node.buffered
      if (b.length === 0) return
      const end = b.end(b.length - 1)
      const lag = end - node.currentTime
      if (lag > 1.0) {
        node.currentTime = end - 0.05   // too far behind → jump to live edge
        node.playbackRate = 1.0
      } else if (lag > 0.35) {
        node.playbackRate = 1.4         // mildly behind → gently catch up
      } else {
        node.playbackRate = 1.0
      }
    }, 1000)

    let stopSource: (() => void) | undefined
    let failed = false
    const fail = (message: string) => {
      if (closedByUs || failed) return
      failed = true
      stopSource?.()
      onFailRef.current?.(message)
    }
    const mediaError = () => fail('浏览器不支持此视频格式，已回落截图')
    node.addEventListener('error', mediaError)
    stopSource = source({
      reset,
      data: video => {
        if (closedByUs || failed) return
        try { muxer?.feed({ video }) } catch { fail('视频播放失败，已回落截图') }
      },
      fail,
    })
    if (failed) stopSource()

    return () => {
      closedByUs = true
      node.removeEventListener('error', mediaError)
      clearInterval(sync)
      stopSource?.()
      muxer?.destroy()
    }
  }, [source])

  return (
    <video
      ref={videoRef}
      autoPlay
      muted
      playsInline
      className="absolute inset-0 w-full h-full object-contain"
    />
  )
}
