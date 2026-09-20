import { describe, expect, it } from 'vitest'

import { chunkMessage } from '../src/chunk.js'

describe('chunkMessage', () => {
  it('returns short text as a single chunk', () => {
    expect(chunkMessage('short answer')).toEqual(['short answer'])
  })

  it('splits on paragraph boundaries under the limit', () => {
    const paragraph = 'x'.repeat(100)
    const text = Array.from({ length: 10 }, () => paragraph).join('\n\n')
    const chunks = chunkMessage(text, 250)
    expect(chunks.length).toBeGreaterThan(1)
    expect(chunks.every((c) => c.length <= 250)).toBe(true)
    expect(chunks.join('\n\n')).toBe(text)
  })

  it('hard-splits a paragraph longer than the limit', () => {
    const long = 'y'.repeat(9500)
    const chunks = chunkMessage(long, 4000)
    expect(chunks.length).toBe(3)
    expect(chunks.every((c) => c.length <= 4000)).toBe(true)
    expect(chunks.join('')).toBe(long)
  })

  it('handles empty text', () => {
    expect(chunkMessage('')).toEqual([])
    expect(chunkMessage('   ')).toEqual([])
  })
})
