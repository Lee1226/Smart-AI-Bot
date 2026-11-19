import { afterEach, describe, expect, it, vi } from 'vitest'
import { H264DecodePipeline } from './h264DecodePipeline'

class FakeVideoDecoder extends EventTarget {
  static instances: FakeVideoDecoder[] = []
  decodeQueueSize = 0
  closed = false
  output: (frame: VideoFrame) => void
  constructor(init: VideoDecoderInit) {
    super()
    this.output = init.output
    FakeVideoDecoder.instances.push(this)
  }
  configure() {}
  decode() { this.decodeQueueSize += 1 }
  close() { this.closed = true }
  releaseOne() {
    this.decodeQueueSize -= 1
    this.dispatchEvent(new Event('dequeue'))
  }
}

const encoded = (nals: number[][]) => new Uint8Array(nals.flatMap(nal => [0, 0, 0, 1, ...nal]))

describe('H264DecodePipeline scheduling', () => {
  afterEach(() => {
    FakeVideoDecoder.instances = []
    vi.unstubAllGlobals()
  })

  it('submits within the decoder in-flight window and resumes on dequeue', () => {
    vi.stubGlobal('VideoDecoder', FakeVideoDecoder)
    const render = vi.fn()
    const fail = vi.fn()
    const pipeline = new H264DecodePipeline(render, fail)
    const sps = [0x67, 0x42, 0, 0x1f]
    const pps = [0x68, 0x80]
    const idr = [0x65, 0x80, 0x11]
    const delta = [0x41, 0x80, 0x22]
    pipeline.feed(encoded([sps, pps, idr, delta, delta, delta, delta, delta]))
    pipeline.flush()
    const decoder = FakeVideoDecoder.instances[0]
    expect(decoder.decodeQueueSize).toBe(4)
    decoder.releaseOne()
    expect(decoder.decodeQueueSize).toBe(4)
    expect(fail).not.toHaveBeenCalled()
    pipeline.dispose()
  })

  it('does not create a decoder before an IDR keyframe', () => {
    vi.stubGlobal('VideoDecoder', FakeVideoDecoder)
    const pipeline = new H264DecodePipeline(() => {}, () => {})
    pipeline.feed(encoded([[0x67, 0x42, 0, 0x1f], [0x68, 0x80], [0x41, 0x80, 0x22]]))
    pipeline.flush()
    expect(FakeVideoDecoder.instances).toHaveLength(0)
    pipeline.dispose()
  })
})
