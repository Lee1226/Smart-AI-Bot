import { H264AnnexB } from './h264AnnexB'

export type H264DecodeStats = {
  decodedFrames: number
  recoveries: number
  droppedAccessUnits: number
  pendingAccessUnits: number
  pendingBytes: number
  decodeQueueSize: number
  lastQueueAgeMs: number
  lastRenderLatencyMs: number
}

type PendingChunk = { chunk: EncodedVideoChunk; queuedAt: number; bytes: number }

const MAX_IN_FLIGHT_DECODE_REQUESTS = 4
// A latency budget, not a frame-count threshold. H.264 deltas are never
// discarded individually: if we can no longer stay near live, restart at IDR.
const MAX_PENDING_AGE_MS = 500
const MAX_PENDING_BYTES = 8 * 1024 * 1024

export class H264DecodePipeline {
  private decoder: VideoDecoder | null = null
  private parser: H264AnnexB
  private pending: PendingChunk[] = []
  private pendingBytes = 0
  private timestamp = 0
  private awaitingKeyframe = true
  private stopped = false
  private failed = false
  private stats: H264DecodeStats = {
    decodedFrames: 0, recoveries: 0, droppedAccessUnits: 0,
    pendingAccessUnits: 0, pendingBytes: 0, decodeQueueSize: 0,
    lastQueueAgeMs: 0, lastRenderLatencyMs: 0,
  }

  constructor(
    private readonly render: (frame: VideoFrame) => void,
    private readonly onError: (message: string) => void,
    private readonly onStats?: (stats: Readonly<H264DecodeStats>) => void,
  ) {
    this.parser = this.createParser()
  }

  private createParser() {
    return new H264AnnexB((data, key, sps) => this.acceptAccessUnit(data, key, sps))
  }

  private publishStats() {
    this.stats.pendingAccessUnits = this.pending.length
    this.stats.pendingBytes = this.pendingBytes
    this.stats.decodeQueueSize = this.decoder?.decodeQueueSize ?? 0
    this.onStats?.({ ...this.stats })
  }

  private fail(message: string) {
    if (this.stopped || this.failed) return
    this.failed = true
    this.closeDecoder()
    this.pending = []
    this.pendingBytes = 0
    this.onError(message)
  }

  private closeDecoder() {
    const decoder = this.decoder
    this.decoder = null
    if (decoder) {
      decoder.removeEventListener('dequeue', this.pump)
      try { decoder.close() } catch { /* already closed */ }
    }
  }

  private enterRecovery() {
    this.stats.droppedAccessUnits += this.pending.length
    this.pending = []
    this.pendingBytes = 0
    this.closeDecoder()
    this.awaitingKeyframe = true
    this.stats.recoveries += 1
    this.publishStats()
  }

  private ensureDecoder(sps: Uint8Array) {
    if (this.decoder) return
    const hex = (n: number) => n.toString(16).padStart(2, '0')
    const decoder = new VideoDecoder({
      output: frame => {
        if (this.stopped || this.failed) { frame.close(); return }
        const started = performance.now()
        try {
          this.render(frame)
          this.stats.decodedFrames += 1
          this.stats.lastRenderLatencyMs = Math.max(0, performance.now() - started)
        } catch (error) {
          this.fail(error instanceof Error ? error.message : '视频渲染失败')
        } finally {
          frame.close()
          this.publishStats()
        }
      },
      error: error => this.fail(error instanceof Error ? error.message : 'WebCodecs 解码失败'),
    })
    this.decoder = decoder
    decoder.addEventListener('dequeue', this.pump)
    try {
      decoder.configure({
        codec: `avc1.${hex(sps[1])}${hex(sps[2])}${hex(sps[3])}`,
        optimizeForLatency: true,
      })
    } catch (error) {
      this.fail(error instanceof Error ? error.message : '视频解码器配置失败')
    }
  }

  private acceptAccessUnit(data: Uint8Array, key: boolean, sps: Uint8Array) {
    if (this.stopped || this.failed) return
    if (this.awaitingKeyframe && !key) return
    if (key && !this.decoder) {
      this.ensureDecoder(sps)
      if (!this.decoder) return
      this.awaitingKeyframe = false
    }
    if (!this.decoder) return

    const now = performance.now()
    const oldestAge = this.pending.length ? now - this.pending[0].queuedAt : 0
    if (oldestAge >= MAX_PENDING_AGE_MS || this.pendingBytes + data.byteLength > MAX_PENDING_BYTES) {
      this.enterRecovery()
      if (!key) return
      this.ensureDecoder(sps)
      if (!this.decoder) return
      this.awaitingKeyframe = false
    }

    const chunk = new EncodedVideoChunk({ type: key ? 'key' : 'delta', timestamp: this.timestamp, data })
    this.timestamp += Math.round(1e6 / 32)
    this.pending.push({ chunk, queuedAt: now, bytes: data.byteLength })
    this.pendingBytes += data.byteLength
    this.pump()
  }

  private pump = () => {
    const decoder = this.decoder
    if (!decoder || this.stopped || this.failed) return
    while (this.pending.length && decoder.decodeQueueSize < MAX_IN_FLIGHT_DECODE_REQUESTS) {
      const item = this.pending.shift()!
      this.pendingBytes -= item.bytes
      this.stats.lastQueueAgeMs = Math.max(0, performance.now() - item.queuedAt)
      try { decoder.decode(item.chunk) }
      catch (error) {
        this.fail(error instanceof Error ? error.message : '视频解码提交失败')
        return
      }
    }
    this.publishStats()
  }

  feed(chunk: Uint8Array) {
    if (this.stopped || this.failed) return
    try { this.parser.feed(chunk) }
    catch (error) { this.fail(error instanceof Error ? error.message : 'H.264 数据解析失败') }
  }

  flush() {
    if (this.stopped || this.failed) return
    try { this.parser.flushAccessUnit() }
    catch (error) { this.fail(error instanceof Error ? error.message : 'H.264 数据解析失败') }
  }

  reset() {
    if (this.stopped) return
    this.closeDecoder()
    this.pending = []
    this.pendingBytes = 0
    this.timestamp = 0
    this.awaitingKeyframe = true
    this.failed = false
    this.parser = this.createParser()
    this.publishStats()
  }

  dispose() {
    if (this.stopped) return
    this.stopped = true
    this.closeDecoder()
    this.pending = []
    this.pendingBytes = 0
  }

  getStats(): Readonly<H264DecodeStats> {
    return { ...this.stats }
  }
}
