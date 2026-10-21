"""
Generate the golden fixtures: `dataset.json` and `queries.json`.

20 queries x one fixed server secret -> the exact noisy answers the oracle gives.
The JSON is language-neutral on purpose: it carries every input (rows, policy,
rho, seed derivation parts) and every expected output, so the Rust engine can load
it with no Python in the loop. This is the engine's acceptance test (Phase 4.3.8
differential test builds on it).

Regenerate with:  cd oracle && uv run python tests/golden/generate.py
Only do that when the oracle's behaviour is *meant* to change (and amend SPEC.md);
`test_golden.py` fails on any unannounced change to the answers.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from privguard_oracle.dp import execute
from privguard_oracle.mechanisms import derive_seed
from privguard_oracle.spec import ColumnPolicy, QuerySpec, TablePolicy

HERE = Path(__file__).parent
SERVER_SECRET = b"privguard-golden-secret-v1"
PRINCIPAL_ID = 1
SALT = b""
DELTA = 1e-6
DELTA_PARTITION = 1e-2

CHARGE_POLICY = {"mechanism": "laplace", "lower_clamp": 0.0, "upper_clamp": 1000.0}
TIGHT_POLICY = {"mechanism": "laplace", "lower_clamp": -50.0, "upper_clamp": 400.0}
DEFAULT_TABLE = {"max_rows_per_unit": 5, "max_groups_per_unit": 3}
TIGHT_TABLE = {"max_rows_per_unit": 2, "max_groups_per_unit": 1}


def build_dataset() -> list[dict]:
    """~250 visits over 80 patients, built to exercise every code path.

    * patients 1-5 have 8 visits (> C = 5, so L-inf bounding binds);
    * patients 6-15 touch all four admission types (> G = 3, so L0 bounding binds);
    * TRAUMA and dept 4 hold only 2-3 patients, so partition selection suppresses them;
    * a few NULL, negative and > 1000 charges exercise null handling and the clamp.
    """
    rng = random.Random(20260101)
    types = ["ELECTIVE", "EMERGENCY", "URGENT", "TRAUMA"]
    rows: list[dict] = []
    vid = 1

    def add(pid: int, adm: str, dept: int) -> None:
        nonlocal vid
        charge: float | None = round(rng.uniform(50.0, 1500.0), 2)
        if vid % 37 == 0:
            charge = None
        elif vid % 41 == 0:
            charge = -20.0
        rows.append(
            {
                "visit_id": vid,
                "patient_id": pid,
                "dept_id": dept,
                "attending_staff_id": 1 + vid % 7,
                "admit_ts": f"2024-{1 + vid % 12:02d}-{1 + vid % 28:02d} 00:00:00",
                "discharge_ts": f"2024-{1 + vid % 12:02d}-{1 + vid % 28:02d} 12:00:00",
                "total_charge": charge,
                "admission_type": adm,
            }
        )
        vid += 1

    for pid in range(1, 81):
        if pid in (78, 79, 80):
            for _ in range(2):
                add(pid, "TRAUMA", 4)
        elif pid <= 5:
            for i in range(8):
                add(pid, types[i % 2], 1 + i % 3)
        elif pid <= 15:
            for i in range(4):
                add(pid, types[i], 1 + i % 3)
        elif pid <= 30:
            add(pid, "URGENT", 1 + pid % 3)
            add(pid, "ELECTIVE", 1 + pid % 3)
        else:
            for _ in range(1 + pid % 3):
                add(pid, types[pid % 2], 1 + pid % 3)
    return rows


# (id, agg, column, filters, group_by, column_policy, table_policy, rho)
_Q = [
    ("count_all", "COUNT", None, [], None, None, DEFAULT_TABLE, 0.5),
    ("count_elective", "COUNT", None, [["admission_type", "exact", "ELECTIVE"]], None, None, DEFAULT_TABLE, 0.5),
    ("count_dept_and_charge", "COUNT", None, [["dept_id", "exact", 2], ["total_charge", "gt", 500.0]], None, None, DEFAULT_TABLE, 0.25),
    ("count_ne_filter_tight_c", "COUNT", None, [["admission_type", "ne", "TRAUMA"]], None, None, TIGHT_TABLE, 1.0),
    ("count_admit_ts_range", "COUNT", None, [["admit_ts", "gte", "2024-03-01 00:00:00"], ["admit_ts", "lt", "2024-09-01 00:00:00"]], None, None, DEFAULT_TABLE, 0.5),
    ("sum_all", "SUM", "total_charge", [], None, CHARGE_POLICY, DEFAULT_TABLE, 0.5),
    ("sum_dept1", "SUM", "total_charge", [["dept_id", "exact", 1]], None, CHARGE_POLICY, DEFAULT_TABLE, 0.5),
    ("sum_charge_band", "SUM", "total_charge", [["total_charge", "gte", 100.0], ["total_charge", "lte", 900.0]], None, CHARGE_POLICY, DEFAULT_TABLE, 0.5),
    ("sum_tight_clamp", "SUM", "total_charge", [], None, TIGHT_POLICY, TIGHT_TABLE, 0.75),
    ("avg_all", "AVG", "total_charge", [], None, CHARGE_POLICY, DEFAULT_TABLE, 0.5),
    ("avg_emergency", "AVG", "total_charge", [["admission_type", "exact", "EMERGENCY"]], None, CHARGE_POLICY, DEFAULT_TABLE, 0.5),
    ("avg_charge_lt", "AVG", "total_charge", [["total_charge", "lt", 800.0]], None, TIGHT_POLICY, DEFAULT_TABLE, 1.0),
    ("count_by_admission", "COUNT", None, [], "admission_type", None, DEFAULT_TABLE, 0.5),
    ("count_by_dept", "COUNT", None, [], "dept_id", None, DEFAULT_TABLE, 0.5),
    ("count_by_admission_high_rho", "COUNT", None, [], "admission_type", None, DEFAULT_TABLE, 4.0),
    ("count_by_admission_filtered", "COUNT", None, [["total_charge", "gt", 300.0]], "admission_type", None, DEFAULT_TABLE, 0.5),
    ("sum_by_admission", "SUM", "total_charge", [], "admission_type", CHARGE_POLICY, DEFAULT_TABLE, 0.5),
    ("sum_by_dept", "SUM", "total_charge", [["total_charge", "gte", 0.0]], "dept_id", CHARGE_POLICY, DEFAULT_TABLE, 1.0),
    ("sum_by_admission_tight", "SUM", "total_charge", [], "admission_type", TIGHT_POLICY, TIGHT_TABLE, 0.5),
    ("count_by_dept_low_rho", "COUNT", None, [], "dept_id", None, DEFAULT_TABLE, 0.02),
]


def _spec(q: dict) -> QuerySpec:
    return QuerySpec(
        agg=q["agg"],
        column=q["column"],
        filters=tuple((c, o, v) for c, o, v in q["filters"]),
        group_by=q["group_by"],
    )


def run_query(q: dict, rows: list[dict]) -> dict:
    """Compute the expected-output block for one fixture entry from its inputs."""
    spec = _spec(q)
    seed = derive_seed(
        bytes.fromhex(q["server_secret_hex"]),
        spec.ast_hash(),
        q["principal_id"],
        bytes.fromhex(q["salt_hex"]),
    )
    res = execute(
        spec,
        rows,
        rho=q["rho"],
        seed=seed,
        table_policy=TablePolicy(**q["table_policy"]),
        column_policy=ColumnPolicy(**q["column_policy"]) if q["column_policy"] else None,
        delta=q["delta"],
        delta_partition=q["delta_partition"],
    )
    return {
        "rows": res.rows,
        "mechanism": res.mechanism,
        "rho_charged": res.rho_charged,
        "eps_equivalent": res.eps_equivalent,
        "sensitivity": res.sensitivity,
        "scale": res.scale,
        "suppressed_groups": res.suppressed_groups,
    }


def build_queries(rows: list[dict]) -> dict:
    out = []
    for qid, agg, col, filters, group_by, cpol, tpol, rho in _Q:
        q = {
            "id": qid,
            "agg": agg,
            "column": col,
            "filters": filters,
            "group_by": group_by,
            "table_policy": tpol,
            "column_policy": cpol,
            "rho": rho,
            "delta": DELTA,
            "delta_partition": DELTA_PARTITION,
            "server_secret_hex": SERVER_SECRET.hex(),
            "principal_id": PRINCIPAL_ID,
            "salt_hex": SALT.hex(),
        }
        spec = _spec(q)
        q["canonical"] = spec.canonical()
        q["fingerprint_hex"] = spec.fingerprint_hex()
        q["seed_hex"] = derive_seed(
            SERVER_SECRET, spec.ast_hash(), PRINCIPAL_ID, SALT
        ).hex()
        q["expected"] = run_query(q, rows)
        out.append(q)
    return {
        "version": 1,
        "note": "Generated by generate.py from the Python oracle. Do not hand-edit.",
        "dataset": "dataset.json",
        "queries": out,
    }


def _dump(obj: object, path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")


def main() -> None:
    rows = build_dataset()
    _dump(rows, HERE / "dataset.json")
    _dump(build_queries(rows), HERE / "queries.json")
    print(f"wrote {len(rows)} rows and {len(_Q)} queries to {HERE}")


if __name__ == "__main__":
    main()
