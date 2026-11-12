import { useEffect, useRef } from 'react'
import { api } from '../lib/api'

type PlaybackInfo = { enabled: boolean; whep_url: string; stream_path: string }

function waitForIceGathering(pc: RTCPeerConnection, timeoutMs = 5000) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise<void>(resolve => {
    const timeout = window.setTimeout(done, timeoutMs)
    function done() {
      window.clearTimeout(timeout)
      pc.removeEventListener('icegatheringstatechange', check)
      resolve()
    }
    function check() {
      if (pc.iceGatheringState === 'complete') done()
    }
    pc.addEventListener('icegatheringstatechange', check)
  })
}

export default function WebRTCPlayer({
  deviceId,
  onFail,
}: {
  deviceId: string
  onFail?: (message: string) => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const onFailRef = useRef(onFail)
  onFailRef.current = onFail

  useEffect(() => {
    let stopped = false
    let acquired = false
    let peer: RTCPeerConnection | undefined
    let whepSessionUrl: string | undefined

    const fail = (message: string) => {
      if (!stopped) onFailRef.current?.(message)
    }

    const start = async () => {
      // Acquire one backend-managed reference. Multiple tabs share the same
      // Scrcpy producer and FFmpeg publisher; no duplicate H.264 WebSocket
      // payload is sent to each WebRTC tab.
      const { data: info } = await api.post<PlaybackInfo>(`/devices/${deviceId}/webrtc`)
      acquired = true
      if (stopped) {
        void api.delete(`/devices/${deviceId}/webrtc`).catch(() => {})
        return
      }
      if (!info.enabled || !info.whep_url) throw new Error('WebRTC 发布流未就绪')

      const pc = new RTCPeerConnection({ iceServers: [] })
      peer = pc
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.ontrack = event => {
        const video = videoRef.current
        if (video && event.streams[0]) {
          video.srcObject = event.streams[0]
          void video.play().catch(() => {})
        }
      }
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed') fail('WebRTC 连接失败，请检查 ICE/UDP 端口配置')
        if (pc.connectionState === 'disconnected') {
          window.setTimeout(() => {
            if (!stopped && pc.connectionState === 'disconnected') {
              fail('WebRTC 连接已断开')
            }
          }, 2500)
        }
      }

      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      await waitForIceGathering(pc)
      if (stopped) return
      const response = await fetch(info.whep_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp', Accept: 'application/sdp' },
        body: pc.localDescription?.sdp,
      })
      if (!response.ok) throw new Error(`WHEP 协商失败：HTTP ${response.status}`)
      const answer = await response.text()
      const locationHeader = response.headers.get('Location')
      whepSessionUrl = locationHeader
        ? new URL(locationHeader, info.whep_url).toString()
        : undefined
      if (!stopped) await pc.setRemoteDescription({ type: 'answer', sdp: answer })
    }

    void start().catch(error => {
      if (!stopped) fail(error instanceof Error ? error.message : 'WebRTC 启动失败')
    })

    return () => {
      stopped = true
      if (whepSessionUrl) {
        void fetch(whepSessionUrl, { method: 'DELETE' }).catch(() => {})
      }
      peer?.close()
      const video = videoRef.current
      if (video) video.srcObject = null
      if (acquired) {
        void api.delete(`/devices/${deviceId}/webrtc`).catch(() => {})
      }
    }
  }, [deviceId])

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
