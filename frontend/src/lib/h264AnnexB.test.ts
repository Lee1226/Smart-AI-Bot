import { describe, expect, it } from 'vitest'
import { H264AnnexB } from './h264AnnexB'

describe('H.264 USB chunk assembly', () => {
  it('handles byte-split start codes and groups continuation slices into one frame', () => {
    const frames: { data: number[]; key: boolean }[] = []
    const parser = new H264AnnexB((data, key) => frames.push({ data: [...data], key }))
    const nals = [[0x67, 0x42, 0, 0x1f], [0x68, 0x80], [0x65, 0x80, 0x11],
      [0x65, 0x40, 0x22], [0x41, 0x80, 0x33], [0x09, 0xf0], [0x09, 0xf0]]
    const bytes = nals.flatMap((nal, i) => [...(i % 2 ? [0, 0, 1] : [0, 0, 0, 1]), ...nal])
    for (const byte of bytes) parser.feed(new Uint8Array([byte]))
    expect(frames).toHaveLength(2)
    expect(frames[0]).toEqual({ key: true, data: [0, 0, 0, 1, ...nals[0], 0, 0, 0, 1, ...nals[1],
      0, 0, 0, 1, ...nals[2], 0, 0, 0, 1, ...nals[3]] })
    expect(frames[1]).toEqual({ key: false, data: [0, 0, 0, 1, ...nals[4]] })
  })
  it('bounds corrupt input rather than buffering forever', () => {
    const parser = new H264AnnexB(() => {})
    expect(() => parser.feed(new Uint8Array(16 * 1024 * 1024 + 1))).toThrow()
  })
})
