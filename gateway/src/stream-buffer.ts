/**
 * Buffers streamed answer tokens into platform-sized message segments.
 *
 * Flush rules (locked in map #56): a segment may leave when it holds at
 * least `minChars` AND ends at a sentence/paragraph boundary; `maxChars`
 * hard-flushes regardless of boundary (never let one message grow huge).
 * `finish()` drains the remainder at end of stream.
 *
 * Pacing between sends is the CALLER's job (chunkPacing sleep) — the buffer
 * only decides WHERE to cut.
 */

export interface StreamBufferOptions {
  /** Minimum accumulated chars before a boundary flush (default 400). */
  minChars?: number
  /** Hard flush at this size regardless of boundary (default 1200). */
  maxChars?: number
}

const SENTENCE_END = /[.!?。！？]["'”’)\]]?\s+|\n{2,}/g

export class StreamBuffer {
  private buf = ''
  private readonly minChars: number
  private readonly maxChars: number

  constructor(options: StreamBufferOptions = {}) {
    this.minChars = options.minChars ?? 400
    this.maxChars = options.maxChars ?? 1200
  }

  /** Append tokens; returns the segments ready to send right now. */
  push(text: string): string[] {
    this.buf += text
    return this.drain(false)
  }

  /** End of stream: return whatever is left, even below `minChars`. */
  finish(): string[] {
    return this.drain(true)
  }

  get pending(): number {
    return this.buf.length
  }

  private drain(force: boolean): string[] {
    const segments: string[] = []
    for (;;) {
      if (this.buf.length >= this.maxChars) {
        segments.push(this.buf.slice(0, this.maxChars))
        this.buf = this.buf.slice(this.maxChars)
        continue
      }
      if (force) {
        if (this.buf.length > 0) {
          // The final segment: trim trailing whitespace so the last message
          // doesn't dangle spaces (hard-flushed segments stay byte-exact).
          segments.push(this.buf.trimEnd())
          this.buf = ''
        }
        break
      }
      if (this.buf.length >= this.minChars) {
        const cut = this.boundaryCut()
        if (cut > 0) {
          segments.push(this.buf.slice(0, cut))
          this.buf = this.buf.slice(cut)
          continue
        }
      }
      break
    }
    return segments
  }

  /** Position of the best sentence/paragraph end within the buffer: the
   * LARGEST boundary that is at or after minChars (buffers at/over maxChars
   * are hard-flushed before boundary logic, so every boundary cut sits under
   * the cap). -1 when no clean cut exists yet. */
  private boundaryCut(): number {
    SENTENCE_END.lastIndex = 0
    let cut = -1
    let match: RegExpExecArray | null
    while ((match = SENTENCE_END.exec(this.buf)) !== null) {
      const end = match.index + match[0].length
      if (end >= this.minChars) {
        cut = end
      }
    }
    return cut
  }
}
