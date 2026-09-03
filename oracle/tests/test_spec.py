"""
Query spec validation and fingerprinting. `SPEC.md` §3 and §4.6.

The fingerprint tests matter more than they look: if two queries that mean the
same thing hash differently, an analyst gets two independent noise draws for one
question and can average them. That is precisely the attack the noise cache
exists to stop.
"""
from __future__ import annotations

import pytest

from privguard_oracle.spec import (
    ColumnPolicy,
    DPQueryError,
    QuerySpec,
    TablePolicy,
)


def count_star(**kw) -> QuerySpec:
    return QuerySpec(agg="COUNT", column=None, **kw)


# ------------------------------------------------------------- fingerprinting
def test_predicate_order_does_not_change_the_fingerprint():
    a = count_star(filters=(("dept_id", "exact", 1), ("total_charge", "gt", 100)))
    b = count_star(filters=(("total_charge", "gt", 100), ("dept_id", "exact", 1)))
    assert a.canonical() == b.canonical()
    assert a.ast_hash() == b.ast_hash()


def test_different_predicates_change_the_fingerprint():
    a = count_star(filters=(("dept_id", "exact", 1),))
    b = count_star(filters=(("dept_id", "exact", 2),))
    assert a.ast_hash() != b.ast_hash()


def test_a_string_literal_is_distinct_from_the_number():
    # '1' and 1 select different rows, so they must not share a cache entry.
    a = count_star(filters=(("dept_id", "exact", 1),))
    b = count_star(filters=(("dept_id", "exact", "1"),))
    assert a.ast_hash() != b.ast_hash()


def test_operator_changes_the_fingerprint():
    a = count_star(filters=(("total_charge", "gt", 100),))
    b = count_star(filters=(("total_charge", "gte", 100),))
    assert a.ast_hash() != b.ast_hash()


def test_grouping_changes_the_fingerprint():
    assert count_star().ast_hash() != count_star(group_by="dept_id").ast_hash()


def test_aggregate_changes_the_fingerprint():
    a = count_star()
    b = QuerySpec(agg="SUM", column="total_charge")
    assert a.ast_hash() != b.ast_hash()


def test_fingerprint_is_stable_across_instances():
    assert count_star().ast_hash() == count_star().ast_hash()


def test_fingerprint_is_a_sha256_digest():
    h = count_star().ast_hash()
    assert isinstance(h, bytes) and len(h) == 32
    assert len(count_star().fingerprint_hex()) == 64


def test_canonical_form_shape():
    spec = QuerySpec(agg="SUM", column="total_charge", group_by="dept_id")
    assert spec.canonical() == "SUM|total_charge|visits|GROUP=dept_id"


# ------------------------------------------------------------------ validation
def test_count_accepts_only_star():
    with pytest.raises(DPQueryError, match="only COUNT"):
        QuerySpec(agg="COUNT", column="total_charge")


def test_sum_and_avg_require_a_numeric_column():
    with pytest.raises(DPQueryError, match="numeric column"):
        QuerySpec(agg="SUM", column="admission_type")
    with pytest.raises(DPQueryError, match="numeric column"):
        QuerySpec(agg="AVG", column=None)


def test_avg_with_group_by_is_rejected():
    with pytest.raises(DPQueryError, match="AVG with GROUP BY"):
        QuerySpec(agg="AVG", column="total_charge", group_by="dept_id")


def test_unknown_aggregate_is_rejected():
    with pytest.raises(DPQueryError, match="unsupported aggregate"):
        QuerySpec(agg="MEDIAN", column="total_charge")


def test_only_the_fact_table_is_queryable():
    with pytest.raises(DPQueryError, match="only FROM visits"):
        QuerySpec(agg="COUNT", column=None, table="patients")


def test_unfilterable_column_is_rejected():
    with pytest.raises(DPQueryError, match="not filterable"):
        count_star(filters=(("mrn", "exact", "x"),))


def test_ungroupable_column_is_rejected():
    with pytest.raises(DPQueryError, match="not groupable"):
        count_star(group_by="total_charge")


def test_unsupported_operator_is_rejected():
    with pytest.raises(DPQueryError, match="unsupported operator"):
        count_star(filters=(("dept_id", "like", 1),))


# --------------------------------------------------------------------- policy
def test_inverted_clamp_bounds_are_rejected():
    with pytest.raises(DPQueryError, match="inverted"):
        ColumnPolicy(mechanism="laplace", lower_clamp=10.0, upper_clamp=0.0)


def test_zero_magnitude_policy_is_rejected():
    # M = 0 gives SUM a zero sensitivity, i.e. "publish the true sum with no
    # noise". Refuse rather than silently release.
    with pytest.raises(DPQueryError, match="zero-sensitivity"):
        ColumnPolicy(mechanism="laplace", lower_clamp=0.0, upper_clamp=0.0)


def test_policy_magnitude_is_the_larger_absolute_bound():
    assert ColumnPolicy("laplace", -50.0, 10.0).magnitude == 50.0
    assert ColumnPolicy("laplace", 0.0, 10.0).magnitude == 10.0


def test_contribution_bounds_must_be_positive():
    with pytest.raises(DPQueryError, match="max_rows_per_unit"):
        TablePolicy(max_rows_per_unit=0)
    with pytest.raises(DPQueryError, match="max_groups_per_unit"):
        TablePolicy(max_groups_per_unit=0)
