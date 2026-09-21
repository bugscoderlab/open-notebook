import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SearchPage from './page'
import { useSearch } from '@/lib/hooks/use-search'
import { useModalManager } from '@/lib/hooks/use-modal-manager'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/search',
  useSearchParams: () => new URLSearchParams('mode=search'),
}))

vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock('@/components/search/NotebookScopeSelector', () => ({
  NotebookScopeSelector: () => null,
}))

vi.mock('@/components/search/StreamingResponse', () => ({
  StreamingResponse: () => null,
}))

vi.mock('@/components/search/AdvancedModelsDialog', () => ({
  AdvancedModelsDialog: () => null,
}))

vi.mock('@/components/search/SaveToNotebooksDialog', () => ({
  SaveToNotebooksDialog: () => null,
}))

vi.mock('@/lib/hooks/use-search', () => ({
  useSearch: vi.fn(),
}))

const { sendAskMock } = vi.hoisted(() => ({ sendAskMock: vi.fn() }))

vi.mock('@/lib/hooks/use-ask', () => ({
  useAsk: () => ({
    sendAsk: sendAskMock,
    isStreaming: false,
    strategy: null,
    answers: [],
    finalAnswer: '',
  }),
}))

vi.mock('@/lib/hooks/use-models', () => ({
  useModelDefaults: () => ({
    data: { default_chat_model: 'model:chat', default_embedding_model: 'model:embedding' },
    isLoading: false,
  }),
  useModels: () => ({ data: [], isLoading: false }),
}))

vi.mock('@/lib/hooks/use-modal-manager', () => ({
  useModalManager: vi.fn(),
}))

const mockUseSearch = vi.mocked(useSearch)
const mockUseModalManager = vi.mocked(useModalManager)
const openModal = vi.fn()

describe('SearchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseSearch.mockReturnValue({
      data: {
        results: [{
          id: 'source_insight:matched-insight',
          parent_id: 'source:parent-source',
          title: 'Matched insight',
          final_score: 0.9,
          created: '2026-01-01T00:00:00Z',
          updated: '2026-01-01T00:00:00Z',
        }],
        total_count: 1,
        search_type: 'text',
      },
      isPending: false,
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useSearch>)
    mockUseModalManager.mockReturnValue({ openModal } as unknown as ReturnType<typeof useModalManager>)
  })

  it('opens the record that matched instead of its parent source', () => {
    render(<SearchPage />)

    fireEvent.click(screen.getByRole('button', { name: 'Matched insight' }))

    expect(openModal).toHaveBeenCalledWith('insight', 'matched-insight')
  })

  it('renders the clarification gate toggle unchecked by default', () => {
    render(<SearchPage />)
    fireEvent.click(screen.getByRole('tab', { name: 'searchPage.askBeta' }))

    expect(screen.getByRole('checkbox', { name: 'searchPage.clarifyQuestions' })).not.toBeChecked()
  })

  it('asks with the gate off by default, and on after checking the toggle', () => {
    render(<SearchPage />)
    fireEvent.click(screen.getByRole('tab', { name: 'searchPage.askBeta' }))

    fireEvent.change(screen.getByPlaceholderText('searchPage.enterQuestionPlaceholder'), {
      target: { value: 'my ceiling is 2.5m, what ladder is suitable?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'searchPage.ask' }))
    expect(sendAskMock).toHaveBeenLastCalledWith(
      'my ceiling is 2.5m, what ladder is suitable?',
      { strategy: 'model:chat', answer: 'model:chat', finalAnswer: 'model:chat' },
      { notebookIds: [], clarify: false }
    )

    fireEvent.click(screen.getByRole('checkbox', { name: 'searchPage.clarifyQuestions' }))
    fireEvent.click(screen.getByRole('button', { name: 'searchPage.ask' }))
    expect(sendAskMock).toHaveBeenLastCalledWith(
      'my ceiling is 2.5m, what ladder is suitable?',
      { strategy: 'model:chat', answer: 'model:chat', finalAnswer: 'model:chat' },
      { notebookIds: [], clarify: true }
    )
  })
})
