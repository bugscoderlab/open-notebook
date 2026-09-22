/**
 * Buffers streamed answer tokens into platform-sized message segments.
 *
 * Flush rules:
 * - ONE PARAGRAPH, ONE MESSAGE: the first `\n\n` break that has accumulated
 *   at least `paraChars` (default 150) is a cut point — each empty line
 *   starts a new message, like a person sending several messages.
 * - Sentence fallback: inside a single long paragraph (no qualifying empty
 *   line), cut at the largest sentence end ≥ `minChars` (default 400).
 * - `maxChars` (default 1200) hard-flushes regardless of boundary — a
 *   paragraph longer than the cap is split, never one huge message.
 * - `finish()` drains the remainder at end of stream (last segment trimmed).
 *
 * Pacing between sends is the CALLER's job (chunkPacing sleep) — the buffer
 * only decides WHERE to cut.
 */

export interface StreamBufferOptions {
  /** Largest sentence end inside one paragraph before a flush (default 400). */
  minChars?: number
  /** Hard flush at this size regardless of boundary (default 1200). */
  maxChars?: number
  /** A paragraph (\n\n chunk) may go out once it holds at least this many
   * chars (default 150) — paragraphs flush early; fragments below the floor
   * merge with the next paragraph. */
  paraChars?: number
}

const PARA_BREAK = /\n{2,}/g
const SENTENCE_END = /[.!?。！？]["'”’)\]]?\s+/g

export class StreamBuffer {
  private buf = ''
  private readonly minChars: number
  private readonly maxChars: number
  private readonly paraChars: number

  constructor(options: StreamBufferOptions = {}) {
    this.minChars = options.minChars ?? 400
    this.maxChars = options.maxChars ?? 1200
    this.paraChars = options.paraChars ?? 150
  }

  /** Append tokens; returns the segments ready to send right now. */
  push(text: string): string[] {
    this.buf += text
    return this.drain(false)
  }

  /** End of stream: return whatever is left (trimmed). */
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
      // One paragraph, one message: the FIRST empty line that has
      // accumulated at least paraChars wins.
      const para = this.paragraphCut()
      if (para !== null) {
        segments.push(this.buf.slice(0, para))
        this.buf = this.buf.slice(para)
        continue
      }
      // Long single paragraph: fall back to sentence boundaries.
      if (this.buf.length >= this.minChars) {
        const cut = this.sentenceCut()
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

  /** First \n\n end at or after paraChars, or null when no paragraph break
   * qualifies yet (fragments below the floor merge forward). */
  private paragraphCut(): number | null {
    PARA_BREAK.lastIndex = 0
    let match: RegExpExecArray | null
    while ((match = PARA_BREAK.exec(this.buf)) !== null) {
      const end = match.index + match[0].length
      if (end >= this.paraChars) {
        return end
      }
    }
    return null
  }

  /** Largest sentence end at or after minChars, or -1 (buffers at/over
   * maxChars are hard-flushed before this runs, so every cut sits under
   * the cap). */
  private sentenceCut(): number {
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
