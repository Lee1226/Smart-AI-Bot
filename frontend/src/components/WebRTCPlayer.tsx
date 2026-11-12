import { useEffect, useRef } from 'react'
import { api } from '../lib/api'
import { websocketH264Source } from '../lib/h264Stream'

type PlaybackInfo = { enabled: boolean; whep_url?: string; reason?: string }

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
    let stopBootstrap: (() => void) | undefined
    let pc: RTCPeerConnection | undefined
    let whepSessionUrl: string | undefined
    const abort = new AbortController()

    const fail = (message: string) => {
      if (!stopped) onFailRef.current?.(message)
    }

    const start = async () => {
      // The existing authenticated local-ADB WebSocket route owns the Scrcpy
      // producer lifecycle. Keep a lightweight subscriber alive to start and
      // retain that producer while the WebRTC receiver is active.
      await new Promise<void>((resolve, reject) => {
        let settled = false
        const timeout = window.setTimeout(() => {
          if (!settled) {
            settled = true
            reject(new Error('Scrcpy 视频源启动超时'))
          }
        }, 20000)
        stopBootstrap = websocketH264Source(
          (() => {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
            const token = localStorage.getItem('sab_token') || ''
            return `${proto}//${location.host}/v1/devices/${deviceId}/live?source=device${token ? `&token=${encodeURIComponent(token)}` : ''}`
          })(),
        )({
          reset: () => {},
          flush: () => {},
          data: () => {
            if (!settled) {
              settled = true
              window.clearTimeout(timeout)
              resolve()
            }
          },
          fail: message => {
            if (!settled) {
              settled = true
              window.clearTimeout(timeout)
              reject(new Error(message))
            }
          },
        })
      })

      let info: PlaybackInfo | undefined
      const deadline = Date.now() + 15000
      while (!stopped && Date.now() < deadline) {
        try {
          const response = await api.get<PlaybackInfo>(`/devices/${deviceId}/webrtc`, {
            signal: abort.signal,
          })
          info = response.data
          if (info.enabled && info.whep_url) break
        } catch (error) {
          const status = (error as { response?: { status?: number } }).response?.status
          if (status !== 503) throw error
        }
        await new Promise(resolve => window.setTimeout(resolve, 300))
      }
      if (stopped) return
      if (!info?.whep_url) throw new Error('WebRTC 发布流未就绪，请检查 FFmpeg 和 MediaMTX')

      pc = new RTCPeerConnection({ iceServers: [] })
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.ontrack = event => {
        const video = videoRef.current
        if (video && event.streams[0]) {
          video.srcObject = event.streams[0]
          void video.play().catch(() => {})
        }
      }
      pc.onconnectionstatechange = () => {
        if (pc?.connectionState === 'failed') fail('WebRTC 连接失败，请检查 ICE/UDP 端口配置')
        if (pc?.connectionState === 'disconnected') {
          window.setTimeout(() => {
            if (!stopped && pc?.connectionState === 'disconnected') {
              fail('WebRTC 连接已断开')
            }
          }, 2500)
        }
      }

      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      await waitForIceGathering(pc)
      const response = await fetch(info.whep_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp', Accept: 'application/sdp' },
        body: pc.localDescription?.sdp,
        signal: abort.signal,
      })
      if (!response.ok) throw new Error(`WHEP 协商失败：HTTP ${response.status}`)
      const answer = await response.text()
      const locationHeader = response.headers.get('Location')
      whepSessionUrl = locationHeader
        ? new URL(locationHeader, info.whep_url).toString()
        : undefined
      await pc.setRemoteDescription({ type: 'answer', sdp: answer })
    }

    void start().catch(error => {
      if (!stopped && !(error instanceof DOMException && error.name === 'AbortError')) {
        fail(error instanceof Error ? error.message : 'WebRTC 启动失败')
      }
    })

    return () => {
      stopped = true
      abort.abort()
      stopBootstrap?.()
      if (whepSessionUrl) {
        void fetch(whepSessionUrl, { method: 'DELETE' }).catch(() => {})
      }
      pc?.close()
      const video = videoRef.current
      if (video) video.srcObject = null
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
