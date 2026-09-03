"""
The synthetic dataset generator. `SPEC.md` §2.2.

Determinism is the point: the CSVs are the shared ground truth that both this
oracle and the Rust loader read, so "same seed, same bytes" is a contract, not a
nicety.
"""
from __future__ import annotations

from privguard_oracle.dataset import (
    ICD10_RARE,
    TABLES,
    GeneratorConfig,
    generate,
    load_visits,
    write_csv,
)
from privguard_oracle.spec import FILTERABLE, GROUPABLE, PRIVACY_UNIT

SMALL = GeneratorConfig(patients=40, seed=7)


def test_the_same_seed_reproduces_the_same_dataset():
    assert generate(SMALL) == generate(SMALL)


def test_a_different_seed_gives_a_different_dataset():
    assert generate(SMALL) != generate(GeneratorConfig(patients=40, seed=8))


def test_every_table_is_populated():
    data = generate(SMALL)
    assert set(data) == set(TABLES)
    for name, rows in data.items():
        assert rows, f"{name} is empty"


def test_patient_count_is_honoured():
    assert len(generate(SMALL)["patients"]) == 40


def test_visits_reference_real_patients_and_departments():
    data = generate(SMALL)
    patient_ids = {p["patient_id"] for p in data["patients"]}
    dept_ids = {d["dept_id"] for d in data["departments"]}
    for v in data["visits"]:
        assert v["patient_id"] in patient_ids
        assert v["dept_id"] in dept_ids


def test_visit_ids_are_unique_and_dense():
    visits = generate(SMALL)["visits"]
    ids = [v["visit_id"] for v in visits]
    assert len(set(ids)) == len(ids)
    assert ids == list(range(1, len(ids) + 1))


def test_every_patient_has_at_least_one_visit():
    # Contribution bounding is only exercised if units actually have rows.
    data = generate(SMALL)
    with_visits = {v["patient_id"] for v in data["visits"]}
    assert with_visits == {p["patient_id"] for p in data["patients"]}


def test_some_patients_exceed_the_contribution_bound():
    # If no unit ever had more than C rows, the L-inf bound would never bind and
    # the DP tests would be vacuous.
    data = generate(GeneratorConfig(patients=100, seed=7))
    counts: dict[int, int] = {}
    for v in data["visits"]:
        counts[v["patient_id"]] = counts.get(v["patient_id"], 0) + 1
    assert max(counts.values()) > 5


def test_the_attack_target_is_unique():
    # Exactly one patient carries the rare diagnosis, so the attack lab has a
    # single privacy unit to try to isolate.
    data = generate(SMALL)
    visit_owner = {v["visit_id"]: v["patient_id"] for v in data["visits"]}
    carriers = {
        visit_owner[d["visit_id"]] for d in data["diagnoses"] if d["icd10_code"] == ICD10_RARE
    }
    assert len(carriers) == 1


def test_visits_carry_every_column_the_query_layer_needs():
    row = generate(SMALL)["visits"][0]
    assert PRIVACY_UNIT in row
    for col in FILTERABLE | GROUPABLE:
        assert col in row, f"visits is missing {col}"


def test_charges_are_positive_and_long_tailed():
    charges = [v["total_charge"] for v in generate(GeneratorConfig(patients=100, seed=7))["visits"]]
    assert all(c > 0 for c in charges)
    # A long tail is what makes clamping visibly matter.
    charges.sort()
    median = charges[len(charges) // 2]
    assert charges[-1] > 5 * median


def test_csv_round_trip_preserves_types(tmp_path):
    data = generate(SMALL)
    write_csv(data, tmp_path)
    loaded = load_visits(tmp_path)

    assert len(loaded) == len(data["visits"])
    first = loaded[0]
    assert isinstance(first["visit_id"], int)
    assert isinstance(first["patient_id"], int)
    assert isinstance(first["total_charge"], float)
    assert isinstance(first["admission_type"], str)
    # Values must survive the trip, not merely the types.
    assert first["total_charge"] == data["visits"][0]["total_charge"]


def test_csv_files_are_written_for_every_table(tmp_path):
    paths = write_csv(generate(SMALL), tmp_path)
    assert {p.stem for p in paths} == set(TABLES)
    for p in paths:
        assert p.exists() and p.stat().st_size > 0


def test_regenerating_produces_identical_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_csv(generate(SMALL), a)
    write_csv(generate(SMALL), b)
    for table in TABLES:
        assert (a / f"{table}.csv").read_bytes() == (b / f"{table}.csv").read_bytes()
