import apiClient from './client'
import { CSRF_HEADER_NAME, getCsrfToken } from '@/lib/csrf'
import { SearchRequest, SearchResponse, AskRequest } from '@/lib/types/search'

export const searchApi = {
  // Standard search (non-streaming)
  search: async (params: SearchRequest) => {
    const response = await apiClient.post<SearchResponse>('/search', params)
    return response.data
  },

  // Ask with streaming (uses relative URL for Docker compatibility)
  askKnowledgeBase: async (params: AskRequest, signal?: AbortSignal) => {
    // Cookie session: credentials + CSRF header, same as apiClient. Relative
    // URL on purpose — same-origin proxy policy: see client.ts
    // (resolveApiBaseUrl); SSE fetch calls can't use the axios interceptor.
    const csrf = getCsrfToken()

    const url = '/api/search/ask'

    // Use fetch with ReadableStream for SSE
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(csrf && { [CSRF_HEADER_NAME]: csrf })
      },
      body: JSON.stringify(params),
      signal
    })

    if (!response.ok) {
      // Try to extract error message from response
      let errorMessage = `HTTP error! status: ${response.status}`
      try {
        const errorData = await response.json()
        errorMessage = errorData.detail || errorData.message || errorMessage
      } catch {
        // If response isn't JSON, use status text
        errorMessage = response.statusText || errorMessage
      }
      throw new Error(errorMessage)
    }

    if (!response.body) {
      throw new Error('No response body received')
    }

    return response.body
  }
}
