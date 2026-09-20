import { ReactNode } from 'react'
import { renderHook, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useNotebookSources } from './use-sources'
import { sourcesApi } from '@/lib/api/sources'
import { SourceListResponse } from '@/lib/types/api'

vi.mock('@/lib/api/sources', () => ({
  sourcesApi: {
    list: vi.fn(),
  },
}))

const PAGE_SIZE = 30

function makeSource(id: string): SourceListResponse {
  return {
    id,
    title: `Source ${id}`,
    topics: [],
    asset: null,
    embedded: false,
    embedded_chunks: 0,
    insights_count: 0,
    created: '2024-01-01T00:00:00Z',
    updated: '2024-01-01T00:00:00Z',
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('useNotebookSources', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('dedupes sources that appear on multiple pages (pagination overlap)', async () => {
    const shared = makeSource('source:qa_shared_src')
    // Page 0 must be exactly PAGE_SIZE rows or the hook reports no next page.
    // The shared source is the last item of page 0 AND reappears on page 1.
    const page0 = [
      ...Array.from({ length: PAGE_SIZE - 1 }, (_, i) =>
        makeSource(`source:p0_${i}`)
      ),
      shared,
    ]
    const page1 = [shared, ...Array.from({ length: 5 }, (_, i) =>
      makeSource(`source:p1_${i}`)
    )]

    vi.mocked(sourcesApi.list).mockImplementation(async (params) => {
      const offset = params?.offset ?? 0
      return offset === 0 ? page0 : page1
    })

    const { result } = renderHook(() => useNotebookSources('notebook:1'), { wrapper })

    await waitFor(() => expect(result.current.sources.length).toBeGreaterThan(0))

    await act(async () => {
      await result.current.fetchNextPage()
    })

    await waitFor(() => expect(result.current.isFetchingNextPage).toBe(false))

    const ids = result.current.sources.map(s => s.id)
    expect(ids.filter(id => id === 'source:qa_shared_src')).toHaveLength(1)
    // 30 page-0 rows + 6 page-1 rows - 1 duplicate = 35 unique sources
    expect(ids).toHaveLength(35)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
