"""The committed non-private baseline matches what the oracle computes, and is sane."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden"

_spec = importlib.util.spec_from_file_location("golden_baseline", GOLDEN / "generate_baseline.py")
baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline)

ROWS = json.loads((GOLDEN / "dataset.json").read_text(encoding="utf-8"))
QUERIES = json.loads((GOLDEN / "queries.json").read_text(encoding="utf-8"))
COMMITTED = json.loads((GOLDEN / "baseline.json").read_text(encoding="utf-8"))


def test_committed_baseline_matches_the_generator():
    assert baseline.build(ROWS, QUERIES) == COMMITTED


def test_one_baseline_answer_per_golden_query():
    assert [q["id"] for q in COMMITTED["queries"]] == [q["id"] for q in QUERIES["queries"]]


def test_unfiltered_count_is_the_row_count():
    count_all = next(q for q in COMMITTED["queries"] if q["id"] == "count_all")
    assert count_all["rows"] == [{"value": len(ROWS)}]


def test_grouped_counts_add_up_to_the_total():
    by_admission = next(q for q in COMMITTED["queries"] if q["id"] == "count_by_admission")
    assert sum(r["value"] for r in by_admission["rows"]) == len(ROWS)
