"""
The 1000-query differential fixture (`tests/golden/diff_queries.json`) still matches the oracle.

The Rust engine replays the same file (`privguard-plan` golden_tests.rs), so this guards the
other half of the differential test: the oracle's answers must not change unannounced.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden"

_spec = importlib.util.spec_from_file_location("golden_generate_diff", GOLDEN / "generate_diff.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

ROWS = json.loads((GOLDEN / "dataset.json").read_text(encoding="utf-8"))
QUERIES = json.loads((GOLDEN / "diff_queries.json").read_text(encoding="utf-8"))["queries"]


def test_fixture_has_a_thousand_unique_queries():
    assert len(QUERIES) == 1000
    assert len({q["id"] for q in QUERIES}) == 1000


def test_fixture_covers_every_aggregate_and_shape():
    assert {q["agg"] for q in QUERIES} == {"COUNT", "SUM", "AVG"}
    assert any(q["group_by"] for q in QUERIES)
    assert any(q["expected"]["suppressed_groups"] for q in QUERIES)
    assert any(not q["filters"] for q in QUERIES)
    assert any(len(q["filters"]) == 3 for q in QUERIES)
    assert len({q["seed_hex"] for q in QUERIES}) > 500


def test_oracle_reproduces_every_committed_answer():
    bad = [q["id"] for q in QUERIES if gen.base.run_query(q, ROWS) != q["expected"]]
    assert not bad, f"oracle drifted on {bad[:5]} ({len(bad)} total)"


def test_generator_is_deterministic():
    assert gen.build_diff_queries(ROWS)["queries"] == QUERIES
