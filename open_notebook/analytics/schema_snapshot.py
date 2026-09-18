"""One-time schema snapshot → parquet: interim schema context (ADR-015).

The text-to-SQL pipeline hands the model a description of the analytics
schema. Until live ``information_schema`` introspection lands, a snapshot
job materializes that description once into a parquet artifact, and the
context builder renders it into the generation prompt.

The artifact holds schema metadata ONLY — table names, column names, data
types, ordinal positions. No row data: sampled rows would leak across
teams (the parquet has no ``data_team`` filter).

Run: ``uv run python -m open_notebook.analytics.schema_snapshot`` (output
overridable with ``--output`` or ``ANALYTICS_SCHEMA_PATH``). Re-runnable:
each run overwrites the artifact.
"""

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text

from open_notebook.analytics.engine import get_engine

SCHEMA_QUERY = """
SELECT
    table_name,
    column_name,
    data_type,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = current_schema()
ORDER BY table_name, ordinal_position
""".strip()

#: Columns the artifact is expected to carry (used to validate on load).
ARTIFACT_COLUMNS = ("table_name", "column_name", "data_type", "ordinal_position")

DEFAULT_SNAPSHOT_PATH = Path("analytics_schema/schema.parquet")


def default_snapshot_path() -> Path:
    """Resolve the artifact location: env override → cwd default."""
    override = os.environ.get("ANALYTICS_SCHEMA_PATH")
    return Path(override) if override else DEFAULT_SNAPSHOT_PATH


async def snapshot_schema(output_path: Optional[Path] = None) -> Path:
    """Introspect the analytics database and write the schema artifact.

    Args:
        output_path: Artifact destination; defaults to
            :func:`default_snapshot_path`.

    Returns:
        The path written (parent directories are created).
    """
    path = Path(output_path) if output_path else default_snapshot_path()
    # Direct engine use, not run_readonly_query: the 500-row execution cap
    # must never silently truncate a schema artifact.
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(SCHEMA_QUERY))
        rows = [dict(row._mapping) for row in result]
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "table_name": pa.array([r["table_name"] for r in rows], type=pa.string()),
            "column_name": pa.array([r["column_name"] for r in rows], type=pa.string()),
            "data_type": pa.array([r["data_type"] for r in rows], type=pa.string()),
            "ordinal_position": pa.array(
                [r["ordinal_position"] for r in rows], type=pa.int64()
            ),
        }
    )
    pq.write_table(table, path)
    return path


def load_schema(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read the artifact back as a list of column-metadata dicts."""
    source = Path(path) if path else default_snapshot_path()
    table = pq.read_table(source)
    missing = [c for c in ARTIFACT_COLUMNS if c not in table.column_names]
    if missing:
        raise ValueError(f"Schema artifact missing columns: {missing}")
    return table.to_pylist()


def render_schema_context(
    columns: List[Dict[str, Any]], dataset_name: str = "dataset"
) -> str:
    """Render artifact rows into the schema section of the generation prompt.

    Includes the mandatory team-filter rule: the model cannot know the
    authorized team names, so the placeholder is the ONLY accepted form.
    """
    by_table: Dict[str, List[Dict[str, Any]]] = {}
    for row in columns:
        by_table.setdefault(str(row["table_name"]), []).append(row)

    lines = [f"Dataset: {dataset_name}", "", "Available tables and columns:"]
    for table_name in sorted(by_table):
        lines.append(f"- {table_name}")
        for row in by_table[table_name]:
            lines.append(f"  - {row['column_name']} ({row['data_type']})")
    lines.extend(
        [
            "",
            "Mandatory rule: every query MUST include the predicate",
            "`data_team IN (:authorized_team_ids)` in its outer WHERE clause.",
            "Never write team names as literals and never add any other",
            "condition on the data_team column.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Artifact path (default: ANALYTICS_SCHEMA_PATH or ./analytics_schema/schema.parquet)",
    )
    args = parser.parse_args()
    path = asyncio.run(snapshot_schema(args.output))
    print(f"Schema snapshot written to {path}")


if __name__ == "__main__":
    main()
