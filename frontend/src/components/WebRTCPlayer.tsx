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

function describeError(error: unknown): string {
  const value = error as {
    message?: string
    response?: { status?: number; data?: { detail?: string; message?: string } | string }
  }
  const detail = value?.response?.data
  if (typeof detail === 'string' && detail) {
    return `HTTP ${value.response?.status ?? '?'}：${detail}`
  }
  if (detail && typeof detail === 'object' && (detail.detail || detail.message)) {
    return `HTTP ${value.response?.status ?? '?'}：${detail.detail || detail.message}`
  }
  return value?.message || 'WebRTC 启动失败'
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
    let released = false
    let peer: RTCPeerConnection | undefined
    let whepSessionUrl: string | undefined

    const release = () => {
      if (released) return
      released = true
      if (whepSessionUrl) {
        void fetch(whepSessionUrl, { method: 'DELETE' }).catch(() => {})
        whepSessionUrl = undefined
      }
      peer?.close()
      peer = undefined
      const video = videoRef.current
      if (video) video.srcObject = null
      if (acquired) {
        acquired = false
        void api.delete(`/devices/${deviceId}/webrtc`).catch(() => {})
      }
    }

    const fail = (message: string) => {
      if (stopped || released) return
      release()
      onFailRef.current?.(message)
    }

    const start = async () => {
      // One backend reference per tab; all tabs share the device's Scrcpy and
      // FFmpeg publisher rather than receiving duplicate H.264 WebSocket data.
      const { data: info } = await api.post<PlaybackInfo>(`/devices/${deviceId}/webrtc`)
      acquired = true
      if (stopped) {
        release()
        return
      }
      if (!info.enabled || !info.whep_url) throw new Error('后端未返回有效的 WHEP 地址')

      const pc = new RTCPeerConnection({ iceServers: [] })
      peer = pc
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.ontrack = event => {
        const video = videoRef.current
        if (video && event.streams[0]) {
          video.srcObject = event.streams[0]
          void video.play().catch(error => {
            fail(`浏览器拒绝自动播放：${error instanceof Error ? error.message : String(error)}`)
          })
        }
      }
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed') {
          fail('ICE 连接失败：请检查 WebRTC UDP 端口 8189、Windows 防火墙及 SDP candidate')
        } else if (pc.connectionState === 'disconnected') {
          window.setTimeout(() => {
            if (!stopped && !released && pc.connectionState === 'disconnected') {
              fail('WebRTC ICE 连接持续断开')
            }
          }, 2500)
        }
      }

      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      await waitForIceGathering(pc)
      if (stopped) {
        release()
        return
      }

      let response: Response
      try {
        response = await fetch(info.whep_url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/sdp', Accept: 'application/sdp' },
          body: pc.localDescription?.sdp,
        })
      } catch (error) {
        throw new Error(
          `无法访问 WHEP 地址 ${info.whep_url}。请确认浏览器与后端运行在同一台电脑、端口 8889 可访问且未被防火墙拦截；浏览器跨域错误也会导致此请求失败。原始错误：${error instanceof Error ? error.message : String(error)}`,
        )
      }
      if (!response.ok) {
        const detail = (await response.text().catch(() => '')).slice(0, 1200)
        throw new Error(`WHEP 协商失败：HTTP ${response.status}${detail ? `，${detail}` : ''}`)
      }
      const answer = await response.text()
      const locationHeader = response.headers.get('Location')
      whepSessionUrl = locationHeader
        ? new URL(locationHeader, info.whep_url).toString()
        : undefined
      if (!stopped) await pc.setRemoteDescription({ type: 'answer', sdp: answer })
    }

    void start().catch(error => {
      if (!stopped) fail(describeError(error))
      else release()
    })

    return () => {
      stopped = true
      release()
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
