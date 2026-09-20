import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError } from 'axios'
import { AnalyticsMode } from './AnalyticsMode'
import { useAnalyticsAsk, useAnalyticsDatasets } from '@/lib/hooks/use-analytics'
import type { AnalyticsAnswer } from '@/lib/types/analytics'

vi.mock('@/lib/hooks/use-analytics', () => ({
  useAnalyticsDatasets: vi.fn(),
  useAnalyticsAsk: vi.fn(),
}))

const mockDatasets = vi.mocked(useAnalyticsDatasets)
const mockAsk = vi.mocked(useAnalyticsAsk)

const SALES_DATASET = {
  id: 'dataset:sales2026',
  name: 'Sales 2026',
  team_id: 'team:finance',
  team_name: 'Finance',
  source_type: 'postgres',
  freshness_at: '2026-09-14T00:00:00Z',
}

const OK_ANSWER: AnalyticsAnswer = {
  query_id: 'analytics_query_log:x',
  status: 'ok',
  answer_text: 'Sarah Lim is the highest spender with MYR 8,460.',
  kpis: [
    { label: 'HIGHEST SPENDER', value: 'Sarah Lim', note: '' },
    { label: 'TOTAL SPEND', value: 'MYR 8,460', note: '' },
    { label: 'TRANSACTIONS', value: '24', note: '' },
  ],
  table: {
    columns: ['customer_name', 'total_spend', 'transaction_count'],
    rows: [['Sarah Lim', 8460, 24]],
  },
  chart: {
    kind: 'bars',
    title: 'Top customers by spend',
    items: [{ label: 'Sarah Lim', value: 8460 }],
  },
  scope: { dataset: 'Sales 2026', period: '2026-01-01 → 2026-09-14', refunds: 'excluded' },
  freshness_at: '2026-09-14T00:00:00Z',
  query_template: 'SELECT … AND data_team IN (:authorized_team_ids) …',
}

describe('AnalyticsMode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the honest empty state when no dataset is permitted', () => {
    mockDatasets.mockReturnValue({
      data: [],
      isSuccess: true,
    } as unknown as ReturnType<typeof useAnalyticsDatasets>)
    mockAsk.mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useAnalyticsAsk>)

    render(<AnalyticsMode />)

    // t() returns the key in tests
    expect(screen.getByText('searchPage.analyticsNoDatasets')).toBeInTheDocument()
  })

  it('renders KPIs, bars and scope for an ok answer — the chart replaces the table', () => {
    mockDatasets.mockReturnValue({
      data: [SALES_DATASET],
      isSuccess: true,
    } as unknown as ReturnType<typeof useAnalyticsDatasets>)
    mockAsk.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      data: OK_ANSWER,
    } as unknown as ReturnType<typeof useAnalyticsAsk>)

    render(<AnalyticsMode />)

    expect(screen.getByText('Sarah Lim is the highest spender with MYR 8,460.')).toBeInTheDocument()
    expect(screen.getByText('TOTAL SPEND')).toBeInTheDocument()
    expect(screen.getByText('MYR 8,460')).toBeInTheDocument()
    expect(screen.getByText('Top customers by spend')).toBeInTheDocument()
    // A graphed answer shows the chart instead of the table.
    expect(screen.queryByText('customer_name')).not.toBeInTheDocument()
    // The answer surface carries the dataset as a scope chip.
    expect(screen.getAllByText('Sales 2026').length).toBeGreaterThanOrEqual(1)
    // View-query disclosure (AN-009)
    fireEvent.click(screen.getByText('searchPage.analyticsViewQuery'))
    expect(screen.getByText(/data_team IN \(:authorized_team_ids\)/)).toBeInTheDocument()
  })

  it('falls back to the table when the answer has no chart', () => {
    mockDatasets.mockReturnValue({
      data: [SALES_DATASET],
      isSuccess: true,
    } as unknown as ReturnType<typeof useAnalyticsDatasets>)
    mockAsk.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      data: { ...OK_ANSWER, chart: null },
    } as unknown as ReturnType<typeof useAnalyticsAsk>)

    render(<AnalyticsMode />)

    expect(screen.getByText('customer_name')).toBeInTheDocument()
    expect(screen.queryByText('Top customers by spend')).not.toBeInTheDocument()
  })

  it('renders a denied answer without any data surfaces', () => {
    mockDatasets.mockReturnValue({
      data: [SALES_DATASET],
      isSuccess: true,
    } as unknown as ReturnType<typeof useAnalyticsDatasets>)
    mockAsk.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      data: {
        status: 'denied',
        answer_text: "You don't have permission to access that dataset.",
      } as unknown as AnalyticsAnswer,
    } as unknown as ReturnType<typeof useAnalyticsAsk>)

    render(<AnalyticsMode />)

    expect(screen.getByText('searchPage.analyticsBadgeDenied')).toBeInTheDocument()
    expect(screen.getByText("You don't have permission to access that dataset.")).toBeInTheDocument()
    // Zero leakage: no KPIs, no table, no chart, no query disclosure.
    expect(screen.queryByText('TOTAL SPEND')).not.toBeInTheDocument()
    expect(screen.queryByText('searchPage.analyticsViewQuery')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('submits the question with the selected dataset and refund toggle', () => {
    const mutate = vi.fn()
    mockDatasets.mockReturnValue({
      data: [SALES_DATASET],
      isSuccess: true,
    } as unknown as ReturnType<typeof useAnalyticsDatasets>)
    mockAsk.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<typeof useAnalyticsAsk>)

    render(<AnalyticsMode />)

    fireEvent.click(screen.getByText('searchPage.analyticsSuggestionHighestSpender'))
    fireEvent.click(screen.getByLabelText('searchPage.analyticsIncludeRefunds'))
    fireEvent.click(screen.getByRole('button', { name: 'searchPage.analyticsAnalyse' }))

    expect(mutate).toHaveBeenCalledWith({
      question: 'Who is the highest spender this year?',
      dataset_id: 'dataset:sales2026',
      include_refunds: true,
    })
  })
})

describe('AnalyticsMode error surface (#21)', () => {
  function renderWithAskError(error: unknown) {
    mockDatasets.mockReturnValue({
      data: [SALES_DATASET],
      isSuccess: true,
    } as unknown as ReturnType<typeof useAnalyticsDatasets>)
    mockAsk.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      data: undefined,
      isError: true,
      error,
    } as unknown as ReturnType<typeof useAnalyticsAsk>)
    render(<AnalyticsMode />)
  }

  it('shows the backend refusal detail for a 4xx with a detail message', () => {
    const error = new AxiosError(
      'Request failed',
      '400',
      undefined,
      undefined,
      {
        status: 400,
        statusText: '',
        headers: {},
        config: {} as never,
        data: {
          detail:
            "That question does not match an approved analytics query, so I can't answer it.",
        },
      } as never
    )

    renderWithAskError(error)

    expect(
      screen.getByText(
        "That question does not match an approved analytics query, so I can't answer it."
      )
    ).toBeInTheDocument()
  })

  it('falls back to the generic message for network errors', () => {
    renderWithAskError(new AxiosError('Network Error', 'ERR_NETWORK'))

    expect(screen.getByText('searchPage.analyticsError')).toBeInTheDocument()
  })

  it('falls back to the generic message for 5xx responses', () => {
    const error = new AxiosError(
      'Request failed',
      '500',
      undefined,
      undefined,
      {
        status: 500,
        statusText: '',
        headers: {},
        config: {} as never,
        data: { detail: '' },
      } as never
    )

    renderWithAskError(error)

    expect(screen.getByText('searchPage.analyticsError')).toBeInTheDocument()
  })
})
