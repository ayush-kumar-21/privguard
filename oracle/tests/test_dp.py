"""
The reference DP executor. `SPEC.md` §4.

The privacy-critical properties under test:

  * contribution bounding actually binds, and binds deterministically;
  * clamping caps a single unit's influence on a SUM;
  * one privacy unit cannot move a released answer by more than the
    sensitivity the noise was calibrated against;
  * the same query and seed always give the same answer (noise cache soundness).
"""
from __future__ import annotations

import statistics

import pytest

from privguard_oracle.dp import (
    apply_filters,
    contribution_bound,
    execute,
    true_aggregate,
)
from privguard_oracle.mechanisms import derive_seed
from privguard_oracle.spec import ColumnPolicy, DPQueryError, QuerySpec, TablePolicy

SEED = derive_seed(b"secret", bytes(32), 1)
CHARGE_POLICY = ColumnPolicy(mechanism="laplace", lower_clamp=0.0, upper_clamp=1000.0)


def visit(vid: int, pid: int, charge: float = 100.0, dept: int = 1, adm: str = "ELECTIVE"):
    return {
        "visit_id": vid,
        "patient_id": pid,
        "dept_id": dept,
        "attending_staff_id": 1,
        "admit_ts": "2024-01-01 00:00:00",
        "discharge_ts": "2024-01-02 00:00:00",
        "total_charge": charge,
        "admission_type": adm,
    }


COUNT = QuerySpec(agg="COUNT", column=None)
SUM = QuerySpec(agg="SUM", column="total_charge")


# ------------------------------------------------------------------ filtering
def test_conjunctive_filters_narrow_the_rows():
    rows = [visit(1, 1, dept=1), visit(2, 2, dept=2), visit(3, 3, dept=1)]
    got = apply_filters(rows, (("dept_id", "exact", 1),))
    assert [r["visit_id"] for r in got] == [1, 3]


def test_all_comparison_operators():
    rows = [visit(i, i, charge=float(i * 10)) for i in range(1, 6)]  # 10..50
    def ids(op, val):
        return [r["visit_id"] for r in apply_filters(rows, (("total_charge", op, val),))]

    assert ids("exact", 30.0) == [3]
    assert ids("ne", 30.0) == [1, 2, 4, 5]
    assert ids("gt", 30.0) == [4, 5]
    assert ids("gte", 30.0) == [3, 4, 5]
    assert ids("lt", 30.0) == [1, 2]
    assert ids("lte", 30.0) == [1, 2, 3]


def test_null_never_matches_any_operator():
    # SQL three-valued logic: NULL compares false even under `ne`.
    rows = [dict(visit(1, 1), total_charge=None)]
    for op in ("exact", "ne", "gt", "gte", "lt", "lte"):
        assert apply_filters(rows, (("total_charge", op, 10.0),)) == []


# --------------------------------------------------------- contribution bounds
def test_linf_bound_caps_rows_per_unit():
    # One patient with 10 visits, C = 3.
    rows = [visit(i, 1) for i in range(1, 11)]
    kept = contribution_bound(rows, TablePolicy(max_rows_per_unit=3), None)
    assert len(kept) == 3


def test_linf_bound_is_deterministic_and_takes_the_lowest_keys():
    rows = [visit(i, 1) for i in range(1, 11)]
    kept = contribution_bound(rows, TablePolicy(max_rows_per_unit=3), None)
    assert [r["visit_id"] for r in kept] == [1, 2, 3]
    # Input order must not matter — the bound sorts by primary key.
    assert contribution_bound(list(reversed(rows)), TablePolicy(max_rows_per_unit=3), None) == kept


def test_bound_applies_per_unit_not_globally():
    rows = [visit(1, 1), visit(2, 1), visit(3, 2), visit(4, 2)]
    kept = contribution_bound(rows, TablePolicy(max_rows_per_unit=1), None)
    assert len(kept) == 2  # one row from each of two patients
    assert {r["patient_id"] for r in kept} == {1, 2}


def test_l0_bound_caps_groups_per_unit():
    # One patient touching 5 departments, G = 2.
    rows = [visit(i, 1, dept=i) for i in range(1, 6)]
    kept = contribution_bound(
        rows, TablePolicy(max_rows_per_unit=10, max_groups_per_unit=2), "dept_id"
    )
    assert {r["dept_id"] for r in kept} == {1, 2}  # lowest-sorting two


def test_l0_and_linf_compose_to_c_times_g():
    # 3 depts x 4 visits each, C = 2, G = 2  ->  at most 4 rows survive.
    rows = []
    vid = 0
    for dept in (1, 2, 3):
        for _ in range(4):
            vid += 1
            rows.append(visit(vid, 1, dept=dept))
    kept = contribution_bound(
        rows, TablePolicy(max_rows_per_unit=2, max_groups_per_unit=2), "dept_id"
    )
    assert len(kept) == 4
    assert {r["dept_id"] for r in kept} == {1, 2}


def test_a_unit_under_the_bound_is_untouched():
    rows = [visit(1, 1), visit(2, 2)]
    assert len(contribution_bound(rows, TablePolicy(max_rows_per_unit=5), None)) == 2


# ---------------------------------------------------------------- aggregation
def test_true_aggregate_clamps_before_summing():
    # Two rows well above the clamp: the sum must be 2 * upper_clamp, not the
    # raw total. This is what bounds one unit's influence.
    rows = [visit(1, 1, charge=1e9), visit(2, 2, charge=1e9)]
    agg = true_aggregate(rows, SUM, CHARGE_POLICY)
    assert agg["total"] == 2000.0
    assert agg["n"] == 2


def test_true_aggregate_groups_and_counts_distinct_units():
    rows = [visit(1, 1, dept=1), visit(2, 1, dept=1), visit(3, 2, dept=2)]
    spec = QuerySpec(agg="COUNT", column=None, group_by="dept_id")
    groups = true_aggregate(rows, spec, None)
    assert groups == [
        {"dept_id": 1, "n": 2, "units": 1},
        {"dept_id": 2, "n": 1, "units": 1},
    ]


# ------------------------------------------------------------------ execution
def test_the_same_seed_gives_the_same_answer():
    # Noise-cache soundness: repeating a query cannot yield a fresh draw.
    rows = [visit(i, i) for i in range(1, 51)]
    a = execute(COUNT, rows, rho=0.05, seed=SEED)
    b = execute(COUNT, rows, rho=0.05, seed=SEED)
    assert a.rows == b.rows


def test_a_different_seed_gives_a_different_answer():
    rows = [visit(i, i) for i in range(1, 51)]
    other = derive_seed(b"secret", bytes(32), 2)
    a = execute(COUNT, rows, rho=0.05, seed=SEED)
    b = execute(COUNT, rows, rho=0.05, seed=other)
    assert a.rows != b.rows


def test_repeat_and_average_cannot_recover_the_truth():
    # The heart of the blueprint's third contribution. Re-asking the same
    # question 500 times must not converge on the true count, because every
    # answer is byte-identical.
    rows = [visit(i, i) for i in range(1, 101)]
    answers = [execute(COUNT, rows, rho=0.05, seed=SEED).rows[0]["value"] for _ in range(500)]
    assert len(set(answers)) == 1, "repeated queries produced independent noise"


def test_count_is_noisy_but_near_the_truth():
    rows = [visit(i, i) for i in range(1, 1001)]  # 1000 patients, 1 visit each
    got = execute(COUNT, rows, rho=0.5, seed=SEED).rows[0]["value"]
    assert got != 1000, "no noise was added"
    assert abs(got - 1000) < 100, f"noise implausibly large: {got}"


def test_noise_shrinks_as_budget_grows():
    # Averaged over many seeds so a single lucky draw cannot decide the test.
    rows = [visit(i, i) for i in range(1, 501)]

    def mean_abs_error(rho):
        errs = []
        for k in range(60):
            seed = derive_seed(b"secret", bytes(32), k)
            v = execute(COUNT, rows, rho=rho, seed=seed).rows[0]["value"]
            errs.append(abs(v - 500))
        return statistics.fmean(errs)

    assert mean_abs_error(1.0) < mean_abs_error(0.01)


def test_one_unit_cannot_move_a_sum_beyond_its_sensitivity():
    # The neighbouring-dataset property, checked on the *true* aggregate: the
    # clamped, bounded sum can move by at most C*M when one patient is removed.
    base = [visit(i, i, charge=500.0) for i in range(1, 21)]
    extra = [visit(100 + j, 999, charge=1e9) for j in range(20)]  # one greedy patient
    policy = TablePolicy(max_rows_per_unit=5)

    with_unit = true_aggregate(contribution_bound(base + extra, policy, None), SUM, CHARGE_POLICY)
    without = true_aggregate(contribution_bound(base, policy, None), SUM, CHARGE_POLICY)

    delta = with_unit["total"] - without["total"]
    max_influence = policy.max_rows_per_unit * CHARGE_POLICY.magnitude  # C * M
    assert 0 < delta <= max_influence


def test_sum_requires_a_column_policy():
    with pytest.raises(DPQueryError, match="clamp bounds"):
        execute(SUM, [visit(1, 1)], rho=0.05, seed=SEED)


def test_nonpositive_rho_is_rejected():
    with pytest.raises(DPQueryError, match="rho must be positive"):
        execute(COUNT, [visit(1, 1)], rho=0.0, seed=SEED)


def test_result_reports_its_accounting():
    res = execute(COUNT, [visit(i, i) for i in range(1, 11)], rho=0.05, seed=SEED)
    assert res.rho_charged == 0.05
    assert res.eps_equivalent > 0.05  # eps always exceeds rho
    assert res.mechanism == "discrete_laplace"
    assert res.sensitivity == 5.0  # default C


def test_sum_and_avg_use_laplace():
    rows = [visit(i, i) for i in range(1, 51)]
    assert execute(SUM, rows, rho=0.05, seed=SEED, column_policy=CHARGE_POLICY).mechanism == "laplace"
    avg = QuerySpec(agg="AVG", column="total_charge")
    assert execute(avg, rows, rho=0.05, seed=SEED, column_policy=CHARGE_POLICY).mechanism == "laplace"


# ---------------------------------------------------------- partition select
def test_grouped_query_uses_the_gaussian_and_may_suppress():
    rows = [visit(i, i, dept=(i % 4) + 1) for i in range(1, 201)]
    spec = QuerySpec(agg="COUNT", column=None, group_by="dept_id")
    res = execute(spec, rows, rho=1.0, seed=SEED)
    assert res.mechanism == "gaussian"
    assert res.suppressed_groups + len(res.rows) == 4


def test_tiny_groups_are_suppressed():
    # 40 patients in dept 1, a single patient in dept 2. With tau well above 1,
    # the singleton group must not be released.
    rows = [visit(i, i, dept=1) for i in range(1, 41)]
    rows.append(visit(999, 999, dept=2))
    spec = QuerySpec(agg="COUNT", column=None, group_by="dept_id")
    res = execute(spec, rows, rho=0.5, seed=SEED)
    assert res.suppressed_groups >= 1
    assert 2 not in [r["dept_id"] for r in res.rows]


def test_grouped_results_are_deterministic():
    rows = [visit(i, i, dept=(i % 3) + 1) for i in range(1, 151)]
    spec = QuerySpec(agg="COUNT", column=None, group_by="dept_id")
    a = execute(spec, rows, rho=0.5, seed=SEED)
    b = execute(spec, rows, rho=0.5, seed=SEED)
    assert a.rows == b.rows and a.suppressed_groups == b.suppressed_groups
