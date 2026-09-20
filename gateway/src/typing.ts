export interface TypingLoop {
  stop: () => void
}

/**
 * Re-send a typing indicator on an interval (Telegram expires it after ~5s;
 * WhatsApp presence after ~10s — callers pick the interval). Errors are
 * swallowed: a failed typing indicator must never fail the answer.
 */
export function startTypingLoop(
  send: () => Promise<unknown>,
  intervalMs: number,
): TypingLoop {
  void send().catch(() => {})
  const timer = setInterval(() => {
    void send().catch(() => {})
  }, intervalMs)
  timer.unref?.()
  return {
    stop: () => clearInterval(timer),
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
