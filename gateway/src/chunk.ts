const PARAGRAPH_BREAK = /\n{2,}/

/**
 * Split a reply into platform-sized chunks (Telegram/sendMessage: 4096
 * chars). Prefers paragraph boundaries; paragraphs longer than the limit are
 * hard-split. Chunks re-join to the original text.
 */
export function chunkMessage(text: string, limit = 4000): string[] {
  const trimmed = text.trim()
  if (!trimmed) return []
  if (trimmed.length <= limit) return [trimmed]

  const paragraphs = trimmed.split(PARAGRAPH_BREAK)
  const chunks: string[] = []
  let current = ''

  const flush = () => {
    if (current) chunks.push(current)
    current = ''
  }

  for (const paragraph of paragraphs) {
    const piece = paragraph.length > limit ? hardSplit(paragraph, limit) : [paragraph]
    for (const part of piece) {
      const candidate = current ? `${current}\n\n${part}` : part
      if (candidate.length <= limit) {
        current = candidate
      } else {
        flush()
        current = part
      }
    }
  }
  flush()
  return chunks
}

function hardSplit(paragraph: string, limit: number): string[] {
  const parts: string[] = []
  for (let i = 0; i < paragraph.length; i += limit) {
    parts.push(paragraph.slice(i, i + limit))
  }
  return parts
}
