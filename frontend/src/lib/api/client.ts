import axios, { AxiosResponse } from 'axios'
import { getApiUrl } from '@/lib/config'
import { CSRF_HEADER_NAME, getCsrfToken } from '@/lib/csrf'

// API client with runtime-configurable base URL
// The base URL is fetched from the API config endpoint on first request
//
// Session auth is a cookie, not a header (ADR-010): withCredentials lets the
// browser send the HttpOnly session cookie (same-origin always; cross-origin
// only when the operator scopes CORS_ORIGINS to the frontend origin). There
// is deliberately no Authorization handling here — nothing token-shaped is
// readable or writable from JavaScript.
//
// Request timeout defaults to 10 minutes (600000ms) to accommodate slow LLM
// operations (transformations, insights, synchronous chat) on slower hardware
// (Ollama, LM Studio). Configure it via NEXT_PUBLIC_API_TIMEOUT_MS for models
// that can take longer than 10 minutes to respond (#880).
// Note: value is in milliseconds; an explicit 0 disables the timeout entirely.
// An empty or invalid value falls back to the default (so a present-but-empty
// env var doesn't accidentally disable timeouts).
const DEFAULT_API_TIMEOUT_MS = 600000 // 600 seconds = 10 minutes
const rawTimeout = process.env.NEXT_PUBLIC_API_TIMEOUT_MS
const parsedTimeout = rawTimeout && rawTimeout.trim() !== '' ? Number(rawTimeout) : NaN
const apiTimeout = Number.isFinite(parsedTimeout) && parsedTimeout >= 0
  ? parsedTimeout
  : DEFAULT_API_TIMEOUT_MS

// Resolved request budget in milliseconds (0 = disabled). Exported so streaming
// consumers can align their own idle watchdogs to the same configurable budget.
export const API_TIMEOUT_MS = apiTimeout

export const apiClient = axios.create({
  timeout: apiTimeout,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,
})

/**
 * Hook for session-death handling: on any 401 the response interceptor calls
 * this handler (the auth store clears the identity; the dashboard guard then
 * redirects to /login). Registered by `@/lib/stores/auth-store` to avoid a
 * store ↔ client import cycle.
 */
type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

// Request interceptor to add base URL, CSRF header, and content types
apiClient.interceptors.request.use(async (config) => {
  // Set the base URL dynamically from runtime config
  if (!config.baseURL) {
    const apiUrl = await getApiUrl()
    config.baseURL = `${apiUrl}/api`
  }

  // Mutations echo the readable CSRF cookie (double-submit pattern, ADR-010).
  // The backend enforces it on logout today and on every mutation from T5.
  const method = config.method?.toLowerCase()
  if (method && ['post', 'put', 'patch', 'delete'].includes(method)) {
    const csrf = getCsrfToken()
    if (csrf) {
      config.headers[CSRF_HEADER_NAME] = csrf
    }
  }

  // Handle FormData vs JSON content types
  if (config.data instanceof FormData) {
    // Remove any Content-Type header to let browser set multipart boundary
    delete config.headers['Content-Type']
  } else if (method && ['post', 'put', 'patch'].includes(method)) {
    config.headers['Content-Type'] = 'application/json'
  }

  return config
})

// Response interceptor for session-death handling
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error) => {
    if (error.response?.status === 401) {
      unauthorizedHandler?.()
    }
    return Promise.reject(error)
  }
)

export default apiClient
