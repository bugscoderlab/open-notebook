import { render, screen, fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import HomePage from './page'
import { useHomeChat } from '@/lib/hooks/use-home-chat'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/home',
}))

vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock('@/components/search/NotebookScopeSelector', () => ({
  NotebookScopeSelector: () => null,
}))

vi.mock('@/components/sources/SessionManager', () => ({
  SessionManager: () => null,
}))

vi.mock('@/components/sources/MessageActions', () => ({
  MessageActions: () => null,
}))

vi.mock('@/components/ui/markdown-renderer', () => ({
  MarkdownRenderer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock('@/lib/utils/source-references', () => ({
  convertReferencesToCompactMarkdown: (content: string) => content,
  createCompactReferenceLinkComponent: () => () => null,
}))

vi.mock('@/lib/hooks/use-home-chat')

vi.mock('@/lib/hooks/use-models', () => ({
  useModelDefaults: () => ({ data: { default_chat_model: 'model:1' }, isLoading: false }),
}))

vi.mock('@/lib/hooks/use-modal-manager', () => ({
  useModalManager: () => ({ openModal: vi.fn() }),
}))

const mockUseHomeChat = vi.mocked(useHomeChat)

// jsdom doesn't implement scrollIntoView (used by the auto-scroll effect).
window.HTMLElement.prototype.scrollIntoView = vi.fn()

function mockChat(overrides: Record<string, unknown> = {}) {
  mockUseHomeChat.mockReturnValue({
    sessions: [],
    currentSession: undefined,
    currentSessionId: null,
    messages: [],
    turns: [],
    suggestions: [],
    route: null,
    isStreaming: false,
    loadingSessions: false,
    createSession: vi.fn(),
    updateSession: vi.fn(),
    deleteSession: vi.fn(),
    switchSession: vi.fn(),
    newSession: vi.fn(),
    sendMessage: vi.fn(),
    cancelStreaming: vi.fn(),
    refetchSessions: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useHomeChat>)
}

describe('HomePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the welcome state and sends a message from the composer', () => {
    mockChat()
    render(<HomePage />)

    // i18n is not initialized in tests — t() returns the raw key.
    expect(screen.getByText('home.welcomeTitle')).toBeInTheDocument()

    const input = screen.getByPlaceholderText('home.placeholder')
    fireEvent.change(input, { target: { value: 'why is the sky blue?' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    // useHomeChat is mocked at module level; grab the last mock's sendMessage
    expect(mockUseHomeChat().sendMessage).toHaveBeenCalledWith(
      'why is the sky blue?',
      expect.objectContaining({ models: expect.objectContaining({ strategy: 'model:1' }) })
    )
  })

  it('starter suggestions are clickable and send their question', () => {
    mockChat()
    render(<HomePage />)

    const starter = screen.getByText('home.starterHighestSpender')
    fireEvent.click(starter)

    expect(mockUseHomeChat().sendMessage).toHaveBeenCalledWith(
      'Who is the highest spender this year?',
      expect.objectContaining({})
    )
  })

  it('renders knowledge answers with sources and follow-up suggestions', () => {
    mockChat({
      messages: [
        { id: '1', type: 'human', content: 'top spender?' },
        { id: '2', type: 'ai', content: 'Sarah spent MYR 100.' },
        { id: '3', type: 'human', content: 'what is rag?' },
        { id: '4', type: 'ai', content: 'retrieval augmented generation' },
      ],
      turns: [
        // Legacy turn from before the analytics removal: a stale analytics
        // payload must not be rendered anymore.
        {
          analytics_answer: {
            query_id: null,
            status: 'ok',
            answer_text: 'Sarah spent MYR 100.',
            kpis: [],
            table: null,
            chart: null,
            scope: { dataset: 'Sales', period: '2026', refunds: 'excluded' },
            freshness_at: null,
            query_template: null,
          },
          suggestions: [],
        },
        { suggestions: ['Tell me more?'] },
      ],
      suggestions: ['Tell me more?'],
    })
    render(<HomePage />)

    expect(screen.getByText('Sarah spent MYR 100.')).toBeInTheDocument()
    expect(screen.getByText('retrieval augmented generation')).toBeInTheDocument()
    // The analytics answer surface is gone — legacy analytics payloads on
    // old turns are not rendered.
    expect(screen.queryByTestId('analytics-surface')).not.toBeInTheDocument()

    // Follow-up suggestion pill is clickable and sends the suggestion
    const pill = screen.getByText('Tell me more?')
    fireEvent.click(pill)
    expect(mockUseHomeChat().sendMessage).toHaveBeenCalledWith(
      'Tell me more?',
      expect.objectContaining({})
    )
  })
})
