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

/**
 * Resolve the axios baseURL for cookie sessions (ADR-010).
 *
 * Session cookies cannot travel on credentialed cross-origin requests when
 * the API answers with wildcard CORS (`Allow-Origin: *` without
 * `Allow-Credentials`) — the browser blocks the response and axios reports a
 * "Network Error". That is the default topology in dev and single-container
 * Docker: page on :3000, API on the SAME host at :5055. In that case the
 * browser must go through the same-origin Next.js rewrites proxy (`/api` →
 * INTERNAL_API_URL), which needs no CORS at all. The SSE fetches in
 * search.ts/source-chat.ts already rely on this proxy for the same reason.
 *
 * Only a genuinely cross-host API_URL (e.g. api.example.com vs
 * notebook.example.com) stays absolute — there the operator must scope
 * CORS_ORIGINS to the frontend origin so the API answers with credentials
 * allowed (see api/main.py CORS_ALLOW_CREDENTIALS).
 *
 * @param apiUrl configured API origin (may be '' = use the proxy)
 * @param pageOrigin browser origin; omit on the server (absolute fallback)
 */
export function resolveApiBaseUrl(apiUrl: string, pageOrigin?: string): string {
  if (!pageOrigin) {
    return `${apiUrl}/api`
  }
  if (!apiUrl || isApiSameOrigin(apiUrl, pageOrigin)) {
    return '/api'
  }
  return `${apiUrl}/api`
}

/**
 * True when the configured API lives on the same hostname as the page
 * (any port) — i.e. the browser can reach it through the same-origin
 * Next.js rewrites proxy instead of a credentialed cross-origin request.
 * Shared by the apiClient baseURL and the podcast asset URLs (media
 * elements send no cross-origin credentials at all).
 */
export function isApiSameOrigin(apiUrl: string, pageOrigin?: string): boolean {
  if (!apiUrl || !pageOrigin) {
    return false
  }
  try {
    return new URL(apiUrl).hostname === new URL(pageOrigin).hostname
  } catch (error) {
    console.warn('[api-client] Unparseable API URL or page origin:', error)
    return false
  }
}

// Request interceptor to add base URL, CSRF header, and content types
apiClient.interceptors.request.use(async (config) => {
  // Set the base URL dynamically from runtime config
  if (!config.baseURL) {
    const apiUrl = await getApiUrl()
    const pageOrigin =
      typeof window !== 'undefined' ? window.location.origin : undefined
    config.baseURL = resolveApiBaseUrl(apiUrl, pageOrigin)
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
