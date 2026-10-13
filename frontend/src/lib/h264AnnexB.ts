// ADB chunks are arbitrary byte ranges, not frames. Assemble complete Annex B
// access units, including encoders that split one frame into several slices.
export class H264AnnexB {
  private buffer = new Uint8Array(0)
  private slices: Uint8Array[] = []
  private key = false
  private sps?: Uint8Array
  private pps?: Uint8Array
  constructor(private output: (data: Uint8Array, key: boolean, sps: Uint8Array) => void) {}

  private emit() {
    if (!this.slices.length) return
    if (this.sps && this.pps) {
      const nals = this.key ? [this.sps, this.pps, ...this.slices] : this.slices
      const data = new Uint8Array(nals.reduce((n, nal) => n + 4 + nal.length, 0))
      let offset = 0
      for (const nal of nals) { data.set([0, 0, 0, 1], offset); data.set(nal, offset + 4); offset += 4 + nal.length }
      this.output(data, this.key, this.sps)
    }
    this.slices = []
    this.key = false
  }

  private nal(nal: Uint8Array) {
    if (!nal.length) return
    const type = nal[0] & 31
    if (type === 7 || type === 8 || type === 9) this.emit()
    if (type === 7) { if (nal.length < 4) throw new Error('无效 H.264 SPS'); this.sps = nal.slice(); return }
    if (type === 8) { this.pps = nal.slice(); return }
    if (type !== 1 && type !== 5) return
    // first_mb_in_slice is the first Exp-Golomb value in the slice header.
    // Value zero starts with a 1 bit; continuation slices start with zero.
    if (nal.length < 2) throw new Error('无效 H.264 slice')
    if (nal[1] & 0x80) this.emit()
    this.slices.push(nal.slice())
    this.key ||= type === 5
  }

  feed(chunk: Uint8Array) {
    if (this.buffer.length + chunk.length > 16 * 1024 * 1024) throw new Error('H.264 数据过大或格式不支持')
    const bytes = new Uint8Array(this.buffer.length + chunk.length)
    bytes.set(this.buffer); bytes.set(chunk, this.buffer.length)
    const marks: { pos: number; length: number }[] = []
    for (let i = 0; i + 2 < bytes.length; i++) {
      if (bytes[i] !== 0 || bytes[i + 1] !== 0) continue
      const length = bytes[i + 2] === 1 ? 3 : bytes[i + 2] === 0 && bytes[i + 3] === 1 ? 4 : 0
      if (length) { marks.push({ pos: i, length }); i += length - 1 }
    }
    for (let i = 0; i + 1 < marks.length; i++) {
      this.nal(bytes.subarray(marks[i].pos + marks[i].length, marks[i + 1].pos))
    }
    this.buffer = marks.length ? bytes.slice(marks[marks.length - 1].pos) : bytes
  }
}
