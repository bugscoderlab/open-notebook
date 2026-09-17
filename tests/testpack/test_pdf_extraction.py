"""Real-PDF extraction proof: the canaries survive the actual pipeline.

The canary sweep seeds ``full_text`` from pdfplumber; this module proves the
same canaries also come out of the *real* processing engine (content-core,
the same ``extract_content`` the source pipeline calls) — so the sweep is
meaningful for documents uploaded through the UI/API too.

Tier: testpack. Hermetic: no database, no AI models.
"""

import pytest

from .conftest import TESTDATA, matrix_rows

pytestmark = pytest.mark.testpack

# 00_access_control_test_guide.pdf has no CSV row; it carries QA-GUIDE-002.
EXTRA_CANARIES = {"00_access_control_test_guide.pdf": "QA-GUIDE-002"}


def _expected_canary(file: str) -> str:
    for row in matrix_rows():
        if row["file"] == file:
            return row["canary"]
    return EXTRA_CANARIES[file]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file",
    sorted([r["file"] for r in matrix_rows()] + list(EXTRA_CANARIES)),
    ids=lambda f: f,
)
async def test_canary_survives_real_extraction(file):
    """content-core extraction of the actual PDF keeps the canary text."""
    from content_core import extract_content

    output = await extract_content(file_path=str(TESTDATA / file))
    assert output.title, f"{file}: extraction returned no title"
    assert _expected_canary(file) in (output.content or "")
