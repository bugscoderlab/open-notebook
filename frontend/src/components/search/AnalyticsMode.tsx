'use client'

import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { AnalyticsAnswerSurface } from '@/components/search/AnalyticsAnswerSurface'
import { useAnalyticsAsk, useAnalyticsDatasets } from '@/lib/hooks/use-analytics'
import { extractAnalyticsErrorDetail } from '@/lib/api/analytics'
import { useTranslation } from '@/lib/hooks/use-translation'
import { Database } from 'lucide-react'

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

        {ask.data && <AnalyticsAnswerSurface answer={ask.data} />}
        {ask.isError && (
          <p className="text-sm text-destructive">
            {extractAnalyticsErrorDetail(ask.error) ??
              t('searchPage.analyticsError')}
          </p>
        )}
      </CardContent>
    </Card>
  )
}
