import { describe, expect, it } from 'vitest'

import { StreamBuffer } from '../src/stream-buffer.js'

describe('StreamBuffer', () => {
  it('holds small text below the minimum', () => {
    const buffer = new StreamBuffer({ minChars: 400, maxChars: 1200 })
    expect(buffer.push('Short. ')).toEqual([])
    expect(buffer.pending).toBe(7)
    expect(buffer.finish()).toEqual(['Short.'])
  })

  it('flushes on a sentence boundary once the minimum is met', () => {
    const buffer = new StreamBuffer({ minChars: 30, maxChars: 1000 })
    const a = 'The quick brown fox jumps. ' // boundary at 27 — under the min
    const b = 'It keeps running far. ' // second boundary at 49
    const c = 'Then it rests.'
    expect(buffer.push(a)).toEqual([])
    expect(buffer.push(b)).toEqual([a + b])
    expect(buffer.push(c)).toEqual([])
    expect(buffer.finish()).toEqual([c])
  })

  it('prefers the largest clean boundary under the cap', () => {
    const buffer = new StreamBuffer({ minChars: 39, maxChars: 1000 })
    const a = 'Alpha part is here. '
    const b = 'Beta part is here. '
    const c = 'Gamma tail.'
    expect(buffer.push(a + b + c)).toEqual([a + b])
    expect(buffer.finish()).toEqual([c])
  })

  it('hard-flushes at the max regardless of boundary', () => {
    const buffer = new StreamBuffer({ minChars: 100, maxChars: 1200 })
    const chunk = 'x'.repeat(2500) // no sentence boundaries at all
    const segments = buffer.push(chunk)
    expect(segments.map((s) => s.length)).toEqual([1200, 1200])
    expect(buffer.finish()).toEqual([chunk.slice(2400)])
  })

  it('splits paragraph breaks as boundaries', () => {
    const buffer = new StreamBuffer({ minChars: 20, maxChars: 1000 })
    const para = 'First paragraph body.\n\n'
    expect(buffer.push(para + 'Second paragraph')).toEqual([para])
    expect(buffer.finish()).toEqual(['Second paragraph'])
  })

  it('one paragraph, one message: each empty line past the floor is a cut', () => {
    const buffer = new StreamBuffer({ paraChars: 20, minChars: 400, maxChars: 1000 })
    const p1 = 'Alpha paragraph here.\n\n'
    const p2 = 'Beta paragraph here.\n\n'
    expect(buffer.push(p1)).toEqual([p1])
    expect(buffer.push(p2)).toEqual([p2])
    expect(buffer.push('Gamma tail.')).toEqual([])
    expect(buffer.finish()).toEqual(['Gamma tail.'])
  })

  it('paragraphs below the floor merge with the next paragraph', () => {
    const buffer = new StreamBuffer({ paraChars: 100, minChars: 30, maxChars: 1000 })
    const tiny = 'Tiny.\n\n'
    const big = 'A much bigger paragraph that carries the tiny one with it.\n\n'
    // The tiny paragraph alone is under the floor; it merges forward.
    expect(buffer.push(tiny + big)).toEqual([tiny + big])
    expect(buffer.finish()).toEqual([])
  })

  it('an over-long paragraph is split at the cap, not one huge message', () => {
    const buffer = new StreamBuffer({ paraChars: 20, minChars: 400, maxChars: 60 })
    const para = 'x'.repeat(130) // one paragraph, no breaks, over the cap
    expect(buffer.push(para)).toEqual(['x'.repeat(60), 'x'.repeat(60)])
    expect(buffer.finish()).toEqual(['x'.repeat(10)])
  })

  it('drains nothing when empty', () => {
    const buffer = new StreamBuffer()
    expect(buffer.finish()).toEqual([])
  })
})
