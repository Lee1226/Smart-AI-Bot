import { useEffect, useRef } from 'react'
import { ScrcpyVideoCodecId, type ScrcpyMediaStreamPacket } from '@yume-chan/scrcpy'
import {
  WebCodecsVideoDecoder,
  WebGLVideoFrameRenderer,
} from '@yume-chan/scrcpy-decoder-webcodecs'
import type { H264StreamSource } from '../lib/h264Stream'
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
    let writer: WritableStreamDefaultWriter<ScrcpyMediaStreamPacket> | null = null
    let parser: H264AnnexB
    let configuration: Uint8Array | null = null
    let timestamp = 0n
    let lastWidth = 0
    let lastHeight = 0

    const fail = (message: string) => {
      if (stopped) return
      stopped = true
      stopSource?.()
      try { writer?.releaseLock() } catch { /* writer already released */ }
      writer = null
      decoder?.dispose()
      decoder = null
      onFailRef.current?.(message)
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

      if (key && !sameBytes(configuration, nextConfiguration)) {
        configuration = nextConfiguration.slice()
        const configPacket: ScrcpyMediaStreamPacket = {
          type: 'configuration',
          data: configuration,
        }
        void writer.write(configPacket).catch(error => {
          fail(error instanceof Error ? error.message : 'H.264 配置数据提交失败')
        })
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
      void writer.write(packet).catch(error => {
        fail(error instanceof Error ? error.message : 'H.264 视频帧提交失败')
      })
    })

    const resetStream = () => {
      disposeDecoder()
      configuration = null
      timestamp = 0n
      parser = createParser()
      createDecoder()
    }

    try {
      parser = createParser()
      stopSource = source({
        reset: resetStream,
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
