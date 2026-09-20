export interface MessageResult {
  reply: string
  suggestions: string[]
  conversation_reset: boolean
}

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiClientError'
  }
}

interface ErrorBody {
  detail?: string
}

/**
 * Minimal client for the API's internal-token surface (ADR-018). The gateway
 * is a dumb pipe: this client carries `Authorization: Internal <token>` and
 * surfaces the API's chat-friendly `detail` strings for rendering in chat.
 */
export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
    private readonly fetchImpl: typeof fetch = fetch,
    private readonly timeoutMs = 600_000,
  ) {}

  private async call(path: string, init: RequestInit = {}): Promise<Response> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)
    try {
      return await this.fetchImpl(`${this.baseUrl}/api${path}`, {
        ...init,
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Internal ${this.token}`,
          ...(init.headers || {}),
        },
        signal: controller.signal,
      })
    } finally {
      clearTimeout(timer)
    }
  }

  private static async detailOf(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as ErrorBody
      if (typeof body.detail === 'string' && body.detail) return body.detail
    } catch {
      // non-JSON error body
    }
    return `Request failed with status ${response.status}`
  }

  /** Verify the internal token against the integrations status endpoint. */
  async checkStatus(): Promise<void> {
    const response = await this.call('/integrations/status', { method: 'GET' })
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        await ApiClient.detailOf(response),
      )
    }
  }

  /** Claim a linking code (Telegram /start <code> flow). */
  async claim(
    platform: string,
    externalId: string,
    code: string,
  ): Promise<{ email: string }> {
    const response = await this.call('/integrations/claim', {
      method: 'POST',
      body: JSON.stringify({ platform, external_id: externalId, code }),
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        typeof (body as ErrorBody).detail === 'string'
          ? ((body as ErrorBody).detail as string)
          : `Claim failed with status ${response.status}`,
      )
    }
    return body as { email: string }
  }

  /** Route one inbound message; returns the reply to send back. */
  async message(
    platform: string,
    externalId: string,
    text: string,
  ): Promise<MessageResult> {
    const response = await this.call('/integrations/message', {
      method: 'POST',
      body: JSON.stringify({ platform, external_id: externalId, text }),
    })
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        await ApiClient.detailOf(response),
      )
    }
    return (await response.json()) as MessageResult
  }
}
