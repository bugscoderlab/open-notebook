import apiClient from './client'
import { CSRF_HEADER_NAME, getCsrfToken } from '@/lib/csrf'
import {
  HomeChatSession,
  HomeChatSessionWithMessages,
  CreateHomeChatSessionRequest,
  UpdateHomeChatSessionRequest,
  SendHomeMessageRequest
} from '@/lib/types/api'

export const homeChatApi = {
  createSession: async (data: CreateHomeChatSessionRequest) => {
    const response = await apiClient.post<HomeChatSession>('/home-chat/sessions', data)
    return response.data
  },

  listSessions: async () => {
    const response = await apiClient.get<HomeChatSession[]>('/home-chat/sessions')
    return response.data
  },

  getSession: async (sessionId: string) => {
    const response = await apiClient.get<HomeChatSessionWithMessages>(
      `/home-chat/sessions/${sessionId}`
    )
    return response.data
  },

  updateSession: async (sessionId: string, data: UpdateHomeChatSessionRequest) => {
    const response = await apiClient.put<HomeChatSession>(
      `/home-chat/sessions/${sessionId}`,
      data
    )
    return response.data
  },

  deleteSession: async (sessionId: string) => {
    await apiClient.delete(`/home-chat/sessions/${sessionId}`)
  },

  // SSE streaming — same-origin fetch (no axios): the axios interceptor's
  // CSRF echo can't run on a stream, so the header is set manually, exactly
  // like sourceChatApi.sendMessage.
  sendMessage: (sessionId: string, data: SendHomeMessageRequest) => {
    const csrf = getCsrfToken()
    const url = `/api/home-chat/sessions/${sessionId}/messages`

    return fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(csrf && { [CSRF_HEADER_NAME]: csrf })
      },
      body: JSON.stringify(data)
    }).then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      return response.body
    })
  }
}
