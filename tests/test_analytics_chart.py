"""Unit tests for the generic chart synthesis on the text-to-SQL path.

The ADR-015 path answers free-form questions, so result shapes are not
curated like the template path's `_build_chart`. `_chart_from_table` derives
a bars chart from whatever (label, numeric) column pairing the rows carry —
e.g. daily revenue: one bar per day.
"""

from open_notebook.analytics.service import _CHART_MAX_ROWS, _chart_from_table


def _daily_revenue_rows(days: int = 3) -> list:
    return [
        {"transaction_date": f"2026-01-{i + 1:02d}", "total_revenue": 100 * (i + 1)}
        for i in range(days)
    ]


class TestChartFromTable:
    def test_daily_revenue_shape_charts_one_bar_per_day(self):
        chart = _chart_from_table(_daily_revenue_rows())
        assert chart == {
            "kind": "bars",
            "title": "total revenue by transaction date",
            "items": [
                {"label": "2026-01-01", "value": 100},
                {"label": "2026-01-02", "value": 200},
                {"label": "2026-01-03", "value": 300},
            ],
        }

    def test_prefers_date_like_label_over_other_text_columns(self):
        rows = [
            {"customer_name": "Sarah Lim", "transaction_date": "2026-01-01", "spend": 10},
            {"customer_name": "Sarah Lim", "transaction_date": "2026-01-02", "spend": 20},
        ]
        chart = _chart_from_table(rows)
        assert chart is not None
        assert [i["label"] for i in chart["items"]] == ["2026-01-01", "2026-01-02"]
        assert chart["title"] == "spend by transaction date"

    def test_falls_back_to_first_non_numeric_column_as_label(self):
        rows = [
            {"service": "Full Groom", "transactions": 12},
            {"service": "Bath", "transactions": 30},
        ]
        chart = _chart_from_table(rows)
        assert chart is not None
        assert [i["label"] for i in chart["items"]] == ["Full Groom", "Bath"]

    def test_no_numeric_column_means_no_chart(self):
        assert _chart_from_table([{"a": "x", "b": "y"}]) is None

    def test_single_column_means_no_chart(self):
        assert _chart_from_table([{"total": 5}, {"total": 6}]) is None

    def test_bool_columns_are_not_numeric_values(self):
        # A boolean flag column must not be picked as the charted value.
        assert (
            _chart_from_table(
                [
                    {"day": "2026-01-01", "is_refund": False},
                    {"day": "2026-01-02", "is_refund": True},
                ]
            )
            is None
        )

    def test_none_in_numeric_column_disqualifies_it(self):
        rows = [
            {"transaction_date": "2026-01-01", "total": None},
            {"transaction_date": "2026-01-02", "total": 200},
        ]
        assert _chart_from_table(rows) is None

    def test_too_many_rows_stays_table_only(self):
        rows = [
            {"transaction_date": f"2026-01-{i % 28 + 1:02d}", "total": i}
            for i in range(_CHART_MAX_ROWS + 1)
        ]
        assert _chart_from_table(rows) is None

    def test_empty_rows_no_chart(self):
        assert _chart_from_table([]) is None
