'use client'

import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { StatusBadge } from '@/components/shell/status-badge'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { useAnalyticsAsk, useAnalyticsDatasets } from '@/lib/hooks/use-analytics'
import { useTranslation } from '@/lib/hooks/use-translation'
import { ChevronDown, Database, ShieldAlert } from 'lucide-react'
import type { AnalyticsAnswer } from '@/lib/types/analytics'

interface Suggestion {
  labelKey: string
  question: string
}

const SUGGESTIONS: Suggestion[] = [
  {
    labelKey: 'searchPage.analyticsSuggestionHighestSpender',
    question: 'Who is the highest spender this year?',
  },
  {
    labelKey: 'searchPage.analyticsSuggestionRanking',
    question: 'Rank customers by total spend in 2026',
  },
  {
    labelKey: 'searchPage.analyticsSuggestionAverageTicket',
    question: "What is Sarah Lim's average transaction value?",
  },
  {
    labelKey: 'searchPage.analyticsSuggestionTopServices',
    question: 'What are our top services?',
  },
]

// Literal keys (the unused-key test scans source text; no dynamic building).
const STATUS_TITLE_KEYS: Record<AnalyticsAnswer['status'], string> = {
  ok: 'searchPage.analyticsStatusTitle.ok',
  denied: 'searchPage.analyticsStatusTitle.denied',
  no_data: 'searchPage.analyticsStatusTitle.no_data',
}

function BarsChart({ answer }: { answer: AnalyticsAnswer }) {
  const { t } = useTranslation()
  if (!answer.chart || answer.chart.items.length === 0) return null
  const max = Math.max(...answer.chart.items.map((item) => item.value), 1)
  return (
    <div className="mt-4">
      <div className="flex items-center justify-between">
        <b className="text-sm">{answer.chart.title}</b>
        {answer.scope && (
          <small className="text-xs text-muted-foreground">
            {t('searchPage.analyticsBarsUnit')}
          </small>
        )}
      </div>
      {answer.chart.items.map((item) => (
        <div
          key={item.label}
          className="grid grid-cols-[110px_1fr_85px] items-center gap-2 my-2 text-xs"
        >
          <span className="truncate">{item.label}</span>
          <div className="h-2 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-primary rounded-full"
              style={{ width: `${Math.max((item.value / max) * 100, 2)}%` }}
            />
          </div>
          <b className="text-right">{item.value.toLocaleString()}</b>
        </div>
      ))}
    </div>
  )
}

function AnswerSurface({ answer }: { answer: AnalyticsAnswer }) {
  const { t } = useTranslation()
  const denied = answer.status === 'denied'
  return (
    <div className="mt-4 rounded-xl border bg-card p-4" data-testid="analytics-answer">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[10px] font-extrabold uppercase tracking-[0.12em] text-muted-foreground">
            {t('searchPage.analyticsAnswerEyebrow')}
          </div>
          <h3 className="font-display text-base font-bold leading-tight">
            {t(STATUS_TITLE_KEYS[answer.status])}
          </h3>
        </div>
        <StatusBadge>
          {denied
            ? t('searchPage.analyticsBadgeDenied')
            : answer.status === 'no_data'
              ? t('searchPage.analyticsBadgeNoData')
              : t('searchPage.analyticsBadgeLive')}
        </StatusBadge>
      </div>

      {denied && <ShieldAlert className="my-3 h-5 w-5 text-muted-foreground" />}
      <p className="mt-2 text-sm">{answer.answer_text}</p>

      {!denied && answer.kpis.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-3">
          {answer.kpis.map((kpi) => (
            <div key={kpi.label} className="rounded-lg border p-3">
              <small className="block text-[10px] font-bold uppercase text-muted-foreground">
                {kpi.label}
              </small>
              <strong className="block text-lg mt-0.5">{kpi.value}</strong>
              {kpi.note && (
                <em className="not-italic text-xs text-muted-foreground">{kpi.note}</em>
              )}
            </div>
          ))}
        </div>
      )}

      {!denied && <BarsChart answer={answer} />}

      {!denied && answer.table && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr>
                {answer.table.columns.map((column) => (
                  <th
                    key={column}
                    className="py-2 pr-3 text-[10px] uppercase tracking-wide text-muted-foreground"
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {answer.table.rows.map((row, index) => (
                <tr key={index} className="border-t">
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="py-2 pr-3">
                      {cell === null ? '' : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!denied && answer.scope && (
        <div className="mt-3 flex flex-wrap gap-2">
          <span className="inline-flex items-center rounded-full border px-2 py-1 text-[10px] font-bold uppercase">
            {answer.scope.dataset}
          </span>
          <span className="inline-flex items-center rounded-full border px-2 py-1 text-[10px] font-bold uppercase">
            {answer.scope.period}
          </span>
          <span className="inline-flex items-center rounded-full border px-2 py-1 text-[10px] font-bold uppercase">
            {t('searchPage.analyticsRefundsChip', { refunds: answer.scope.refunds })}
          </span>
          {answer.freshness_at && (
            <span className="inline-flex items-center rounded-full border px-2 py-1 text-[10px] font-bold uppercase">
              {t('searchPage.analyticsFreshness', { date: answer.freshness_at.slice(0, 10) })}
            </span>
          )}
        </div>
      )}

      {!denied && answer.query_template && (
        <Collapsible className="mt-3">
          <div className="rounded-lg bg-muted p-3 text-xs">
            <p className="text-muted-foreground">
              <b>{t('searchPage.analyticsHowCalculatedTitle')}</b>{' '}
              {t('searchPage.analyticsHowCalculated')}
            </p>
            <CollapsibleTrigger className="flex w-full items-center gap-2 mt-2 text-muted-foreground hover:text-foreground">
              <ChevronDown className="h-4 w-4" />
              {t('searchPage.analyticsViewQuery')}
            </CollapsibleTrigger>
            <CollapsibleContent>
              <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px]">
                {answer.query_template}
              </pre>
            </CollapsibleContent>
          </div>
        </Collapsible>
      )}
    </div>
  )
}

export function AnalyticsMode() {
  const { t } = useTranslation()
  const datasetsQuery = useAnalyticsDatasets()
  const ask = useAnalyticsAsk()
  const [question, setQuestion] = useState('')
  const [includeRefunds, setIncludeRefunds] = useState(false)
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null)

  const datasets = datasetsQuery.data ?? []
  const dataset =
    datasets.find((d) => d.id === selectedDatasetId) ?? datasets[0] ?? null

  const handleAnalyse = () => {
    if (!question.trim() || !dataset) return
    ask.mutate({
      question: question.trim(),
      dataset_id: dataset.id,
      include_refunds: includeRefunds,
    })
  }

  // No permitted datasets → honest empty state, no ask box (AN-003 surface).
  if (datasetsQuery.isSuccess && datasets.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6 text-center text-muted-foreground">
          <Database className="mx-auto mb-2 h-5 w-5" />
          {t('searchPage.analyticsNoDatasets')}
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">{t('searchPage.analyticsTitle')}</CardTitle>
        <p className="text-sm text-muted-foreground">
          {t('searchPage.analyticsDesc')}
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="analytics-question">{t('searchPage.analyticsQuestion')}</Label>
          <Textarea
            id="analytics-question"
            placeholder={t('searchPage.analyticsPlaceholder')}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !ask.isPending) {
                e.preventDefault()
                handleAnalyse()
              }
            }}
            disabled={ask.isPending}
            rows={3}
          />
          <div className="flex flex-wrap items-center gap-2">
            {datasets.length > 1 ? (
              <Select
                value={dataset?.id ?? ''}
                onValueChange={setSelectedDatasetId}
                disabled={ask.isPending}
              >
                <SelectTrigger
                  className="h-7 w-44 rounded-full text-[10px] font-bold uppercase"
                  aria-label={t('searchPage.analyticsDatasetLabel')}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {datasets.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              dataset && (
                <span className="inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[10px] font-bold uppercase">
                  <Database className="h-3 w-3" />
                  {dataset.name}
                  {dataset.freshness_at &&
                    ` · ${t('searchPage.analyticsFreshness', { date: dataset.freshness_at.slice(0, 10) })}`}
                </span>
              )
            )}
            <div className="flex items-center gap-2 ml-auto">
              <Checkbox
                id="analytics-refunds"
                checked={includeRefunds}
                onCheckedChange={(checked) => setIncludeRefunds(checked as boolean)}
                disabled={ask.isPending}
              />
              <Label htmlFor="analytics-refunds" className="font-normal cursor-pointer">
                {t('searchPage.analyticsIncludeRefunds')}
              </Label>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion.question}
              type="button"
              onClick={() => setQuestion(suggestion.question)}
              disabled={ask.isPending}
              className="rounded-full border px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted transition-colors disabled:opacity-50"
            >
              {t(suggestion.labelKey)}
            </button>
          ))}
        </div>

        <Button
          onClick={handleAnalyse}
          disabled={ask.isPending || !question.trim() || !dataset}
          className="w-full sm:w-auto"
        >
          {ask.isPending ? (
            <>
              <LoadingSpinner size="sm" className="mr-2" />
              {t('searchPage.analyticsAnalysing')}
            </>
          ) : (
            t('searchPage.analyticsAnalyse')
          )}
        </Button>

        {ask.data && <AnswerSurface answer={ask.data} />}
        {ask.isError && (
          <p className="text-sm text-destructive">{t('searchPage.analyticsError')}</p>
        )}
      </CardContent>
    </Card>
  )
}
