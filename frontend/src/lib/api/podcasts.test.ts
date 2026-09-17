import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => 'http://api.test'),
}))

import { getApiUrl } from '@/lib/config'
import { resolvePodcastAssetUrl } from './podcasts'

describe('resolvePodcastAssetUrl (cookie-session routing)', () => {
  beforeEach(() => {
    vi.mocked(getApiUrl).mockReset()
  })

  it('passes through absolute URLs untouched', async () => {
    expect(await resolvePodcastAssetUrl('https://cdn.example.com/ep.mp3')).toBe(
      'https://cdn.example.com/ep.mp3',
    )
  })

  it('returns undefined for empty paths', async () => {
    expect(await resolvePodcastAssetUrl(null)).toBeUndefined()
    expect(await resolvePodcastAssetUrl('')).toBeUndefined()
  })

  it('routes same-host assets through the relative proxy (no cross-origin credentials for media)', async () => {
    // jsdom page origin is http://localhost:3000
    vi.mocked(getApiUrl).mockResolvedValue('http://localhost:5055')
    expect(await resolvePodcastAssetUrl('/podcasts/ep.mp3')).toBe('/podcasts/ep.mp3')
  })

  it('keeps absolute URLs for cross-host APIs', async () => {
    vi.mocked(getApiUrl).mockResolvedValue('http://api.test')
    expect(await resolvePodcastAssetUrl('/podcasts/ep.mp3')).toBe('http://api.test/podcasts/ep.mp3')
  })

  it('keeps paths relative when no API URL is configured', async () => {
    vi.mocked(getApiUrl).mockResolvedValue('')
    expect(await resolvePodcastAssetUrl('/podcasts/ep.mp3')).toBe('/podcasts/ep.mp3')
  })
})
