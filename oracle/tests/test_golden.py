"""
Golden-value test: 20 queries x a fixed seed -> committed noisy answers.

`tests/golden/queries.json` is language-neutral; the Rust engine loads the very same
file. This test fails if the oracle's answers ever change, so a change to noise
derivation, sensitivity, bounding or partition selection cannot slip through
unnoticed. If a change is intended: amend SPEC.md, run
`uv run python tests/golden/generate.py`, and commit the new JSON in the same PR.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"

_spec = importlib.util.spec_from_file_location("golden_generate", GOLDEN / "generate.py")
generate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate)

FIXTURE = json.loads((GOLDEN / "queries.json").read_text(encoding="utf-8"))
ROWS = json.loads((GOLDEN / "dataset.json").read_text(encoding="utf-8"))
QUERIES = FIXTURE["queries"]


def test_there_are_twenty_queries_with_unique_ids():
    assert len(QUERIES) == 20
    assert len({q["id"] for q in QUERIES}) == 20


@pytest.mark.parametrize("q", QUERIES, ids=[q["id"] for q in QUERIES])
def test_oracle_reproduces_committed_answer(q):
    # Recomputed from the JSON inputs alone (no generator state), which is exactly
    # what a non-Python implementation has available.
    assert generate.run_query(q, ROWS) == q["expected"]


@pytest.mark.parametrize("q", QUERIES, ids=[q["id"] for q in QUERIES])
def test_seed_and_fingerprint_are_stable(q):
    spec = generate._spec(q)
    assert spec.canonical() == q["canonical"]
    assert spec.fingerprint_hex() == q["fingerprint_hex"]
    from privguard_oracle.mechanisms import derive_seed

    seed = derive_seed(
        bytes.fromhex(q["server_secret_hex"]),
        spec.ast_hash(),
        q["principal_id"],
        bytes.fromhex(q["salt_hex"]),
    )
    assert seed.hex() == q["seed_hex"]


def test_committed_files_match_the_generator():
    # Guards against hand-edited fixtures and against a dataset change that was
    # not accompanied by regenerated answers.
    assert generate.build_dataset() == ROWS
    assert generate.build_queries(ROWS) == FIXTURE


def test_fixtures_cover_every_aggregate_and_mechanism():
    aggs = {q["agg"] for q in QUERIES}
    assert aggs == {"COUNT", "SUM", "AVG"}
    mechanisms = {q["expected"]["mechanism"] for q in QUERIES}
    assert mechanisms == {"discrete_laplace", "laplace", "gaussian"}
    grouped = [q for q in QUERIES if q["group_by"]]
    assert len(grouped) >= 6
    assert {q["group_by"] for q in grouped} == {"admission_type", "dept_id"}


def test_fixtures_exercise_partition_selection_both_ways():
    grouped = [q["expected"] for q in QUERIES if q["group_by"]]
    assert any(e["suppressed_groups"] > 0 for e in grouped), "no group is ever suppressed"
    assert any(e["suppressed_groups"] == 0 for e in grouped), "no query releases every group"
    assert all(e["rows"] for e in grouped), "a query released nothing"


def test_dataset_exercises_bounding_nulls_and_clamping():
    by_patient: dict[int, int] = {}
    for r in ROWS:
        by_patient[r["patient_id"]] = by_patient.get(r["patient_id"], 0) + 1
    assert max(by_patient.values()) > 5  # L-inf bound (default C = 5) binds
    types_per_patient: dict[int, set] = {}
    for r in ROWS:
        types_per_patient.setdefault(r["patient_id"], set()).add(r["admission_type"])
    assert max(len(t) for t in types_per_patient.values()) > 3  # L0 bound (default G = 3) binds
    charges = [r["total_charge"] for r in ROWS]
    assert any(c is None for c in charges)
    assert any(c is not None and c < 0 for c in charges)
    assert any(c is not None and c > 1000 for c in charges)
