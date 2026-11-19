import { useEffect, useRef } from 'react'
import { ScrcpyVideoCodecId, type ScrcpyMediaStreamPacket } from '@yume-chan/scrcpy'
import {
  WebCodecsVideoDecoder,
  WebGLVideoFrameRenderer,
} from '@yume-chan/scrcpy-decoder-webcodecs'
import type { H264StreamSource, ScrcpyPreviewPacket, ScrcpyPreviewMetadata } from '../lib/h264Stream'
import { H264AnnexB } from '../lib/h264AnnexB'

type RendererMode = 'canvas' | 'webgl'

type FrameRenderer = {
  setSize(width: number, height: number): void
  draw(frame: VideoFrame): void | Promise<void>
}

class Canvas2DFrameRenderer implements FrameRenderer {
  private context: CanvasRenderingContext2D

  constructor(private canvas: HTMLCanvasElement) {
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Canvas 2D 不可用')
    this.context = context
  }

  setSize(width: number, height: number) {
    if (this.canvas.width !== width) this.canvas.width = width
    if (this.canvas.height !== height) this.canvas.height = height
  }

  draw(frame: VideoFrame) {
    this.context.drawImage(frame, 0, 0)
  }
}

/**
 * A stable renderer facade lets the yume-chan decoder keep running while the
 * user switches between WebGL and Canvas 2D. The decoder and Scrcpy stream are
 * not recreated when the output canvas changes.
 */
class SwitchableFrameRenderer implements FrameRenderer {
  private webgl: WebGLVideoFrameRenderer | null = null
  private canvas2d: Canvas2DFrameRenderer
  private active: FrameRenderer
  private width = 0
  private height = 0

  constructor(
    private webglCanvas: HTMLCanvasElement,
    canvas2dCanvas: HTMLCanvasElement,
  ) {
    this.canvas2d = new Canvas2DFrameRenderer(canvas2dCanvas)
    this.active = this.canvas2d
  }

  setMode(mode: RendererMode) {
    if (mode === 'webgl') {
      if (!WebGLVideoFrameRenderer.isSupported) throw new Error('当前浏览器不支持硬件 WebGL 渲染')
      this.webgl ??= new WebGLVideoFrameRenderer(this.webglCanvas)
      this.active = this.webgl
    } else {
      this.active = this.canvas2d
    }
    if (this.width && this.height) this.active.setSize(this.width, this.height)
  }

  setSize(width: number, height: number) {
    this.width = width
    this.height = height
    this.active.setSize(width, height)
  }

  draw(frame: VideoFrame) {
    return this.active.draw(frame)
  }

  dispose() {
    this.webglCanvas.width = 0
    this.webglCanvas.height = 0
    this.canvas2d.setSize(0, 0)
  }
}

function startsWithBytes(data: Uint8Array, prefix: Uint8Array) {
  if (data.length < prefix.length) return false
  for (let i = 0; i < prefix.length; i++) {
    if (data[i] !== prefix[i]) return false
  }
  return true
}

function sameBytes(left: Uint8Array | null, right: Uint8Array) {
  if (!left || left.length !== right.length) return false
  for (let i = 0; i < left.length; i++) {
    if (left[i] !== right[i]) return false
  }
  return true
}

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
  const rendererRef = useRef<SwitchableFrameRenderer | null>(null)

  onFailRef.current = onFail
  onVideoSizeRef.current = onVideoSize

  // Renderer instance is stable for the lifetime of the component. Changing
  // WebGL/Canvas only switches the output target, not the decoder or socket.
  useEffect(() => {
    const webglCanvas = webglCanvasRef.current
    const canvas2d = canvas2dRef.current
    if (!webglCanvas || !canvas2d) return

    let manager: SwitchableFrameRenderer
    try {
      manager = new SwitchableFrameRenderer(webglCanvas, canvas2d)
      rendererRef.current = manager
    } catch (error) {
      onFailRef.current?.(error instanceof Error ? error.message : '视频渲染器初始化失败')
      return
    }

    return () => {
      manager.dispose()
      if (rendererRef.current === manager) rendererRef.current = null
    }
  }, [])

  useEffect(() => {
    try {
      rendererRef.current?.setMode(renderer)
    } catch (error) {
      onFailRef.current?.(error instanceof Error ? error.message : 'WebGL 初始化失败')
    }
  }, [renderer])

  // yume-chan WebCodecs decoder owns H.264 decoding and its latest-frame
  // renderer scheduler. The raw stream adapter only assembles access units and
  // translates them into Scrcpy configuration/data packets.
  useEffect(() => {
    if (!window.isSecureContext || !WebCodecsVideoDecoder.isSupported) {
      onFailRef.current?.('当前环境不支持 WebCodecs 安全上下文')
      return
    }

    const rendererManager = rendererRef.current
    if (!rendererManager) {
      onFailRef.current?.('视频渲染器尚未初始化')
      return
    }

    let stopped = false
    let stopSource: (() => void) | undefined
    let decoder: WebCodecsVideoDecoder | null = null
    let writer: ReturnType<WebCodecsVideoDecoder['writable']['getWriter']> | null = null
    let parser: H264AnnexB
    let configuration: Uint8Array | null = null
    let decoderHasConfiguration = false
    let nativePacketMode = false
    let timestamp = 0n
    let lastWidth = 0
    let lastHeight = 0
    type PendingWrite = { packet?: ScrcpyMediaStreamPacket; reset?: true; bytes: number; queuedAt: number }
    const pendingWrites: PendingWrite[] = []
    let pendingBytes = 0
    let inFlightBytes = 0
    let inFlightQueuedAt = 0
    let inFlightToken = 0
    let writePumpRunning = false
    let decoderGeneration = 0
    let cancelInFlightWrite: (() => void) | null = null
    let waitingForKeyframe = false
    // Diagnostic counters are read-only: they do not alter packet scheduling.
    let diagRxPackets = 0
    let diagRxBytes = 0
    let diagWritePackets = 0
    let diagWriteMsTotal = 0
    let diagWriteMsMax = 0
    const diagTimer = window.setInterval(() => {
      if (!nativePacketMode) return
      console.info('[H264 DIAG][frontend]', {
        rxPacketsPerSec: Math.round(diagRxPackets / 5),
        rxBytesPerSec: Math.round(diagRxBytes / 5),
        pendingPackets: pendingWrites.length + (inFlightBytes > 0 ? 1 : 0),
        pendingBytes: pendingBytes + inFlightBytes,
        writePacketsPerSec: Math.round(diagWritePackets / 5),
        writeAvgMs: diagWritePackets ? Number((diagWriteMsTotal / diagWritePackets).toFixed(2)) : 0,
        writeMaxMs: Number(diagWriteMsMax.toFixed(2)),
        waitingForKeyframe,
      })
      diagRxPackets = 0
      diagRxBytes = 0
      diagWritePackets = 0
      diagWriteMsTotal = 0
      diagWriteMsMax = 0
    }, 5000)
    const MAX_PENDING_PACKETS = 96
    const MAX_PENDING_BYTES = 16 * 1024 * 1024
    const MAX_PENDING_AGE_MS = 1000

    const pumpWrites = async () => {
      if (writePumpRunning || stopped) return
      writePumpRunning = true
      try {
        while (!stopped && pendingWrites.length) {
          const item = pendingWrites.shift()!
          pendingBytes = Math.max(0, pendingBytes - item.bytes)
          const generation = decoderGeneration
          const token = ++inFlightToken
          inFlightBytes = item.bytes
          inFlightQueuedAt = item.queuedAt
          const activeWriter = writer
          if (!activeWriter || !item.packet) {
            inFlightBytes = 0
            inFlightQueuedAt = 0
            continue
          }
          let cancel!: () => void
          const cancelled = new Promise<'cancelled'>(resolve => {
            cancel = () => resolve('cancelled')
          })
          cancelInFlightWrite = cancel
          try {
            // Race each writer operation against generation cancellation. This
            // prevents a stalled old writer from blocking packets for the new
            // decoder after an overload resync or stream reconnect.
            const ready = await Promise.race([
              activeWriter.ready.then(() => 'ready' as const),
              cancelled,
            ])
            if (ready === 'cancelled' || stopped || generation !== decoderGeneration) continue
            const writeStarted = performance.now()
            const written = await Promise.race([
              activeWriter.write(item.packet).then(() => 'written' as const),
              cancelled,
            ])
            if (written === 'written') {
              const writeMs = performance.now() - writeStarted
              diagWritePackets += 1
              diagWriteMsTotal += writeMs
              diagWriteMsMax = Math.max(diagWriteMsMax, writeMs)
            }
            if (written === 'cancelled' || stopped || generation !== decoderGeneration) continue
          } catch (error) {
            if (stopped || generation !== decoderGeneration) continue
            throw error
          } finally {
            if (token === inFlightToken) {
              inFlightBytes = 0
              inFlightQueuedAt = 0
              cancelInFlightWrite = null
            }
          }
        }
      } catch (error) {
        fail(error instanceof Error ? error.message : 'H.264 视频帧提交失败')
      } finally {
        writePumpRunning = false
        if (!stopped && pendingWrites.length) void pumpWrites()
      }
    }

    const queuePacket = (packet: ScrcpyMediaStreamPacket) => {
      const bytes = packet.data.byteLength
      pendingWrites.push({ packet, bytes, queuedAt: performance.now() })
      pendingBytes += bytes
      void pumpWrites()
    }

    const resyncAtNextKeyframe = () => {
      pendingWrites.length = 0
      pendingBytes = 0
      if (!nativePacketMode) configuration = null
      decoderHasConfiguration = false
      timestamp = 0n
      waitingForKeyframe = true
      // Reset immediately instead of queueing reset behind a potentially
      // blocked writer.ready/write. The generation change invalidates that
      // in-flight operation and the next IDR starts on a fresh decoder.
      decoderGeneration += 1
      cancelInFlightWrite?.()
      cancelInFlightWrite = null
      inFlightToken += 1
      inFlightBytes = 0
      inFlightQueuedAt = 0
      disposeDecoder()
      createDecoder()
    }

    const fail = (message: string) => {
      if (stopped) return
      stopped = true
      window.clearInterval(diagTimer)
      decoderGeneration += 1
      cancelInFlightWrite?.()
      cancelInFlightWrite = null
      inFlightToken += 1
      stopSource?.()
      pendingWrites.length = 0
      pendingBytes = 0
      try { writer?.releaseLock() } catch { /* writer already released */ }
      writer = null
      decoder?.dispose()
      decoder = null
      onFailRef.current?.(message)
    }

    const handleNativePacket = (incoming: ScrcpyPreviewPacket) => {
      if (stopped) return
      diagRxPackets += 1
      diagRxBytes += incoming.data.byteLength
      if (incoming.type === 'configuration') {
        configuration = incoming.data.slice()
        decoderHasConfiguration = true
        queuePacket({
          type: 'configuration',
          data: incoming.data,
        })
        return
      }

      const now = performance.now()
      const oldest = pendingWrites[0]
      const oldestQueuedAt = oldest
        ? (inFlightQueuedAt ? Math.min(oldest.queuedAt, inFlightQueuedAt) : oldest.queuedAt)
        : inFlightQueuedAt
      const queueTooOld = !!oldestQueuedAt && now - oldestQueuedAt > MAX_PENDING_AGE_MS
      const queueTooLarge = pendingWrites.length + (inFlightBytes > 0 ? 1 : 0) >= MAX_PENDING_PACKETS
        || pendingBytes + inFlightBytes + incoming.data.byteLength > MAX_PENDING_BYTES

      if (!waitingForKeyframe && (queueTooOld || queueTooLarge)) {
        console.warn('[H264] Decoder fell behind; resynchronizing at next IDR', {
          pendingPackets: pendingWrites.length + (inFlightBytes > 0 ? 1 : 0),
          pendingBytes: pendingBytes + inFlightBytes,
          oldestAgeMs: oldestQueuedAt ? Math.round(now - oldestQueuedAt) : 0,
          transport: 'scrcpy-packet',
        })
        resyncAtNextKeyframe()
      }

      if (waitingForKeyframe && !incoming.keyframe) return
      if (waitingForKeyframe && incoming.keyframe) {
        waitingForKeyframe = false
        if (configuration && !decoderHasConfiguration) {
          queuePacket({ type: 'configuration', data: configuration })
          decoderHasConfiguration = true
        }
      }

      queuePacket({
        type: 'data',
        data: incoming.data,
        keyframe: incoming.keyframe,
        pts: incoming.pts,
      })
    }

    const handlePacketMetadata = (metadata: ScrcpyPreviewMetadata) => {
      nativePacketMode = true
      waitingForKeyframe = true
      if (metadata.codecId !== 0x68323634) {
        fail(`当前 Scrcpy 视频编码不受支持：0x${metadata.codecId.toString(16)}`)
        return
      }
      if (metadata.width > 0 && metadata.height > 0) {
        onVideoSizeRef.current?.(metadata.width, metadata.height)
      }
    }

    const createDecoder = () => {
      decoder = new WebCodecsVideoDecoder({
        codec: ScrcpyVideoCodecId.H264,
        renderer: rendererManager,
      })
      decoder.sizeChanged(({ width, height }) => {
        if (width === lastWidth && height === lastHeight) return
        lastWidth = width
        lastHeight = height
        onVideoSizeRef.current?.(width, height)
      })
      writer = decoder.writable.getWriter()
    }

    const disposeDecoder = () => {
      try { writer?.releaseLock() } catch { /* pending writer may already be closed */ }
      writer = null
      decoder?.dispose()
      decoder = null
    }

    const createParser = () => new H264AnnexB((data, key, _sps, nextConfiguration) => {
      if (stopped || !writer) return

      const now = performance.now()
      const oldest = pendingWrites[0]
      const oldestQueuedAt = oldest
        ? (inFlightQueuedAt ? Math.min(oldest.queuedAt, inFlightQueuedAt) : oldest.queuedAt)
        : inFlightQueuedAt
      const queueTooOld = !!oldestQueuedAt && now - oldestQueuedAt > MAX_PENDING_AGE_MS
      const queueTooLarge = pendingWrites.length + (inFlightBytes > 0 ? 1 : 0) >= MAX_PENDING_PACKETS
        || pendingBytes + inFlightBytes + data.byteLength > MAX_PENDING_BYTES

      if (!waitingForKeyframe && (queueTooOld || queueTooLarge)) {
        console.warn('[H264] Decoder fell behind; resynchronizing at next IDR', {
          pendingPackets: pendingWrites.length + (inFlightBytes > 0 ? 1 : 0),
          pendingBytes: pendingBytes + inFlightBytes,
          oldestAgeMs: oldestQueuedAt ? Math.round(now - oldestQueuedAt) : 0,
        })
        resyncAtNextKeyframe()
      }

      // Never discard arbitrary H.264 reference frames. After overload, wait
      // for an IDR, recreate the decoder, then resume with fresh SPS/PPS.
      if (waitingForKeyframe && !key) return
      if (waitingForKeyframe && key) waitingForKeyframe = false

      if (key && !sameBytes(configuration, nextConfiguration)) {
        configuration = nextConfiguration.slice()
        const configPacket: ScrcpyMediaStreamPacket = {
          type: 'configuration',
          data: configuration,
        }
        queuePacket(configPacket)
      }

      // The decoder prepends the configuration packet to the first keyframe.
      // Strip the duplicate SPS/PPS prefix from this access unit.
      const frameData = key && configuration && startsWithBytes(data, configuration)
        ? data.subarray(configuration.length)
        : data
      const packet: ScrcpyMediaStreamPacket = {
        type: 'data',
        data: frameData,
        keyframe: key,
        pts: timestamp,
      }
      timestamp += 31_250n
      queuePacket(packet)
    })

    const resetStream = () => {
      pendingWrites.length = 0
      pendingBytes = 0
      waitingForKeyframe = false
      nativePacketMode = false
      decoderGeneration += 1
      cancelInFlightWrite?.()
      cancelInFlightWrite = null
      inFlightToken += 1
      inFlightBytes = 0
      inFlightQueuedAt = 0
      disposeDecoder()
      configuration = null
      decoderHasConfiguration = false
      timestamp = 0n
      parser = createParser()
      createDecoder()
    }

    try {
      parser = createParser()
      stopSource = source({
        reset: resetStream,
        packetMode: handlePacketMetadata,
        packet: handleNativePacket,
        flush: () => parser.flushAccessUnit(),
        bootstrapStart: resetStream,
        bootstrapEnd: () => parser.flushAccessUnit(),
        data: chunk => {
          try { parser.feed(chunk) }
          catch (error) { fail(error instanceof Error ? error.message : 'H.264 数据解析失败') }
        },
        fail,
      })
      if (stopped) stopSource()
    } catch (error) {
      fail(error instanceof Error ? error.message : '视频流启动失败')
    }

    return () => {
      stopped = true
      window.clearInterval(diagTimer)
      decoderGeneration += 1
      cancelInFlightWrite?.()
      cancelInFlightWrite = null
      inFlightToken += 1
      pendingWrites.length = 0
      pendingBytes = 0
      inFlightBytes = 0
      inFlightQueuedAt = 0
      stopSource?.()
      disposeDecoder()
    }
  }, [source])

  return (
    <>
      <canvas ref={webglCanvasRef} className={`absolute inset-0 w-full h-full object-contain ${renderer === 'webgl' ? '' : 'hidden'}`} />
      <canvas ref={canvas2dRef} className={`absolute inset-0 w-full h-full object-contain ${renderer === 'canvas' ? '' : 'hidden'}`} />
    </>
  )
}
