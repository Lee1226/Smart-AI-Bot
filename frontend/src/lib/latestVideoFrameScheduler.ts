/**
 * Keep the live preview at the newest decoded frame.
 *
 * WebCodecs may produce frames faster than the browser can present them.
 * Retaining every decoded frame makes an interactive preview drift behind
 * the device. This scheduler keeps at most one not-yet-presented frame and
 * closes replaced frames immediately.
 */
export class LatestVideoFrameScheduler {
  private pending: VideoFrame | null = null
  private animationFrame: number | null = null
  private disposed = false

  constructor(
    private readonly render: (frame: VideoFrame) => void,
    private readonly onError: (error: unknown) => void,
  ) {}

  push(frame: VideoFrame) {
    if (this.disposed) return
    const next = frame.clone()
    this.pending?.close()
    this.pending = next
    if (this.animationFrame === null) {
      this.animationFrame = requestAnimationFrame(this.flush)
    }
  }

  clear() {
    if (this.animationFrame !== null) {
      cancelAnimationFrame(this.animationFrame)
      this.animationFrame = null
    }
    this.pending?.close()
    this.pending = null
  }

  private flush = () => {
    this.animationFrame = null
    const frame = this.pending
    this.pending = null
    if (frame) {
      try {
        if (!this.disposed) this.render(frame)
      } catch (error) {
        this.onError(error)
      } finally {
        frame.close()
      }
    }
    if (this.pending && this.animationFrame === null) {
      this.animationFrame = requestAnimationFrame(this.flush)
    }
  }

  dispose() {
    if (this.disposed) return
    this.disposed = true
    this.clear()
  }
}
