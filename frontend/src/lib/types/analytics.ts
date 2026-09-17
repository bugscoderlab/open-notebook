// Analytics types (issue #10/T9) — frozen contract shapes from
// docs/7-DEVELOPMENT/team-access/api-contracts.md (Analytics).

export type AnalyticsAnswerStatus = 'ok' | 'denied' | 'no_data'

export interface AnalyticsDataset {
  id: string
  name: string
  team_id: string | null
  team_name: string
  source_type: string
  freshness_at: string | null
}

export interface AnalyticsKpi {
  label: string
  value: string
  note: string
}

export interface AnalyticsTable {
  columns: string[]
  rows: (string | number | null)[][]
}

export interface AnalyticsChart {
  kind: 'bars'
  title: string
  items: { label: string; value: number }[]
}

export interface AnalyticsScope {
  dataset: string
  period: string
  refunds: string
}

/** Full answer shape for status ok / no_data. */
export interface AnalyticsAnswer {
  query_id: string | null
  status: AnalyticsAnswerStatus
  answer_text: string
  kpis: AnalyticsKpi[]
  table: AnalyticsTable | null
  chart: AnalyticsChart | null
  scope: AnalyticsScope | null
  freshness_at: string | null
  query_template: string | null
}

export interface AnalyticsAskRequest {
  question: string
  dataset_id?: string
  include_refunds?: boolean
}

/** Stored query-log entry (GET /analytics/queries/{id}, AN-009). */
export interface AnalyticsQueryRecord {
  query_id: string
  user_id: string | null
  dataset_id: string | null
  dataset_name: string | null
  question: string
  template_id: string | null
  duration_ms: number | null
  row_count: number | null
  status: string
  created: string | null
  query_template: string | null
}
